"""R2 scoring integrity: evidence-package citations, bounded refunds, operator-only known
defects and verified detections, final_score = sum of the five areas.

Replays the frozen pilot A/B/C scorer outputs (tests/fixtures/r2_scoring/*.json) through the
deterministic audit - no AI call anywhere in this file."""
import copy
import json
from pathlib import Path

import pytest

from app.services import llm_service, scoring_service
from app.services.round2_automation_evidence_audit import audit_round2_automation_findings

from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login
from .test_round2_automation import (
    _SUFFICIENT, _reach_automation_round, _select, _sequential_call_claude, _state, _submit_payload,
)

FIXTURES = Path(__file__).parent / "fixtures" / "r2_scoring"


def _load(name):
    return json.loads((FIXTURES / f"{name}_r2.json").read_text())


def _payload(tc_evidence):
    """The audit payload score_round2_automation_submission builds (_auto_tc_audit_payload)."""
    return [{"title": b["label"], "turns": [
        {"turn_number": t["turn_number"], "candidate_prompt": t["candidate_prompt"], "model_response": t.get("response_message", "")}
        for t in b["turns"]]} for b in tc_evidence]


def _replay(fixture, findings=None, known_defects=None, scores=None, final=None):
    """Runs a frozen scorer result through the same post-scoring path the live submit uses."""
    result = copy.deepcopy(fixture["scoring"])
    if findings is not None:
        result["findings"] = findings
    if scores is not None:
        result["scores"] = scores
    if final is not None:
        result["final_score"] = final
    scoring_service._r2_final_from_subscores(result)
    supporting, audit_inputs = scoring_service._r2_audit_texts(fixture["tc_evidence"])
    misses, final_score, audit = scoring_service._round2_automation_findings_to_misses(
        result, _payload(fixture["tc_evidence"]), supporting, known_defects=known_defects or [], **audit_inputs)
    return result, misses, final_score, audit


D2 = {
    "id": "D2", "creditable": True,
    "requirement": "A duplicate submission creates only one debit",
    "actual_behaviour": "Two identical transfer requests both succeed and debit twice",
    "observable_signal": "two successful transfers / balance debited twice",
    "observable_signals": ["expected 1, got 2", "successful count: 2"],
    "targets": ["duplicate", "double-click", "resubmit"],
}
D1 = {
    "id": "D1", "creditable": False,
    "requirement": "Daily limit message shows the remaining amount",
    "actual_behaviour": "Shows the wrong remaining amount",
    "observable_signal": "remaining amount differs from expected",
    "observable_signals": ["remaining"], "targets": ["limit"],
}


def _c_as_detection(fixture):
    """C's frozen findings, with the two run-output findings labelled as the D2 detection they describe."""
    findings = copy.deepcopy(fixture["scoring"]["findings"])
    for f in findings[:2]:
        f["kind"], f["defect_id"] = "defect_detected", "D2"
    return findings


# ---- A: fake 100 is gone ----

def test_a_replay_scores_62_with_no_refund_and_six_misses():
    fixture = _load("A")
    assert fixture["saved_final_score"] == 100  # what the old audit produced
    _, misses, final, audit = _replay(fixture)
    assert final == 62
    assert len(misses) == 6
    assert audit["supported"] == 6 and audit["not_established"] == 0
    assert all(f["evidence_status"] == "SUPPORTED" for f in audit["findings"])


def test_full_evidence_package_quotes_are_supported():
    fixture = _load("A")
    label = fixture["tc_evidence"][0]["label"]
    code_line = fixture["tc_evidence"][0]["final_code"].splitlines()[0]
    quotes = ['"exit_code": 1', '"code_edits": []', fixture["tc_evidence"][0]["design"]["expected_result"][:40], code_line]
    stdout = fixture["tc_evidence"][0]["execution_result"].get("stdout", "")
    if stdout.strip():
        quotes.append(stdout.strip().splitlines()[0])
    findings = [{"claim": f"Test case 0: weakness {i}", "severity": "low", "evidence": [{"quote": q, "test_case": label}]}
                for i, q in enumerate(quotes)]
    _, misses, _, audit = _replay(fixture, findings=findings)
    assert [f["evidence_status"] for f in audit["findings"]] == ["SUPPORTED"] * len(quotes)
    assert len(misses) == len(quotes)


def test_fabricated_quote_is_not_established_and_refunded():
    fixture = _load("A")
    label = fixture["tc_evidence"][0]["label"]
    findings = copy.deepcopy(fixture["scoring"]["findings"])
    findings.append({"claim": "Test case 0: hard-coded password", "severity": "high",
                     "evidence": [{"quote": "password = 'hunter2-never-written'", "test_case": label}]})
    _, misses, final, audit = _replay(fixture, findings=findings)
    assert audit["findings"][-1]["evidence_status"] == "NOT_ESTABLISHED"
    assert len(misses) == 6
    # 38-point deduction shared by 6 supported (81 pts) + 1 rejected high (15 pts): refund 6, never 15.
    assert final == 62 + round(38 * 15 / 96)


def test_quote_tagged_with_the_wrong_test_case_is_rejected():
    fixture = _load("B")
    tc0, tc6 = (b["label"] for b in fixture["tc_evidence"])
    quote = "# Step 1: POST PRACTICE_APP_URL + 'test/reset'"  # only in test case 6's code
    assert quote in fixture["tc_evidence"][1]["final_code"] and quote not in fixture["tc_evidence"][0]["final_code"]
    assert quote and quote not in json.dumps(fixture["tc_evidence"][0], indent=2)
    findings = [{"claim": "Test case 0: x", "severity": "medium", "evidence": [{"quote": quote, "test_case": tc0}]},
                {"claim": "Test case 6: x", "severity": "medium", "evidence": [{"quote": quote, "test_case": tc6}]}]
    _, _, _, audit = _replay(fixture, findings=findings)
    assert [f["evidence_status"] for f in audit["findings"]] == ["NOT_ESTABLISHED", "SUPPORTED"]


# ---- refund boundaries ----

def _report(statuses_and_severities):
    """An audit report with findings of the given (supported?, severity)."""
    tc = [{"title": "T", "turns": []}]
    findings = [{"claim": f"c{i}", "severity": sev,
                 "evidence": [{"quote": "real text" if ok else "invented text", "test_case": "T"}]}
                for i, (ok, sev) in enumerate(statuses_and_severities)]
    return audit_round2_automation_findings(tc, findings, {"T": ["real text"]})


def test_refund_is_zero_when_nothing_is_rejected():
    assert _report([(True, "high"), (True, "low")]).bounded_adjustment(50) == 0
    assert _report([]).bounded_adjustment(40) == 0


def test_refund_never_exceeds_the_attributable_deduction():
    report = _report([(True, "high"), (False, "high")])
    assert report.bounded_adjustment(62) == 15  # 38 * 15/30 = 19 capped at the rejected weight 15
    assert _report([(False, "high"), (False, "high")]).bounded_adjustment(90) == 10  # only 10 was deducted
    assert _report([(False, "high")]).bounded_adjustment(100) == 0  # nothing deducted, nothing to refund
    assert _report([(True, "high"), (False, "low")]).bounded_adjustment(80) == round(20 * 3 / 18)


def test_final_score_with_a_refund_never_passes_100():
    fixture = _load("B")
    label = fixture["tc_evidence"][0]["label"]
    findings = [{"claim": "Test case 0: x", "severity": "high", "evidence": [{"quote": "not in evidence", "test_case": label}]}]
    _, misses, final, _ = _replay(fixture, findings=findings)
    assert misses == [] and final == 100  # deduction 7, rejected 15 -> refund 7


# ---- B: unchanged ----

def test_b_replay_is_unchanged():
    fixture = _load("B")
    _, misses, final, audit = _replay(fixture)
    assert final == fixture["saved_final_score"] == 93
    assert misses == fixture["saved_misses"] and len(misses) == 2
    assert audit["supported"] == 2


# ---- C: a verified detection is not a miss or a penalty ----

def test_c_replay_without_known_defects_keeps_its_three_supported_findings():
    fixture = _load("C")
    _, misses, final, audit = _replay(fixture)
    assert final == 80 and len(misses) == 3
    assert "defects_detected" not in audit


def test_c_verified_d2_detection_is_not_a_miss_or_a_penalty():
    fixture = _load("C")
    _, misses, final, audit = _replay(fixture, findings=_c_as_detection(fixture), known_defects=[D2])
    assert [d["defect_id"] for d in audit["defects_detected"]] == ["D2", "D2"]
    assert [f["evidence_status"] for f in audit["findings"]] == ["VERIFIED_DEFECT", "VERIFIED_DEFECT", "SUPPORTED"]
    # The two detections are not misses; the medium cites run output without any D2 failure
    # signal, so it is not demonstrably the same defect and stays a miss.
    assert misses == [fixture["scoring"]["findings"][2]["claim"]]
    assert "needs_review" not in audit
    # The frozen sub-scores were written under the old prompt - offline structural result only.
    assert final == 80


def _with_d2_verified(fixture, weakness):
    return _replay(fixture, findings=_c_as_detection(fixture) + [weakness], known_defects=[D2])


def test_unrelated_weakness_mentioning_duplicate_stays_a_miss():
    fixture = _load("C")
    label = fixture["tc_evidence"][1]["label"]
    assert "Message: Transfer successful" in fixture["tc_evidence"][1]["execution_result"]["stdout"]
    weakness = {"claim": "Test case 8: the duplicate UI check only reads the message text and never verifies the transaction history",
                "severity": "medium", "evidence": [{"quote": "Message: Transfer successful", "test_case": label}]}
    _, misses, final, audit = _with_d2_verified(fixture, weakness)
    assert audit["findings"][-1]["evidence_status"] == "SUPPORTED"
    assert weakness["claim"] in misses and final == 80


def test_unrelated_weakness_quoting_part_of_the_detection_output_stays_a_miss():
    fixture = _load("C")
    label = fixture["tc_evidence"][1]["label"]
    quote = '"reference": "WLT000000005"'
    assert quote in fixture["scoring"]["findings"][0]["evidence"][0]["quote"]  # inside the detection's own quote
    weakness = {"claim": "Test case 8: the test hard-codes the expected reference number instead of reading it",
                "severity": "medium", "evidence": [{"quote": quote, "test_case": label}]}
    _, misses, final, audit = _with_d2_verified(fixture, weakness)
    assert audit["findings"][-1]["evidence_status"] == "SUPPORTED"
    assert weakness["claim"] in misses and final == 80


def test_weakness_quoting_candidate_code_stays_a_miss():
    fixture = _load("C")
    label = fixture["tc_evidence"][1]["label"]
    code_quote = "# Step 6: DB: count rows in transaction with remarks 'dup-api' and status 'Successful' (expect 1)"
    weakness = {"claim": "Test case 8: the duplicate DB check relies on a fixed remark", "severity": "medium",
                "evidence": [{"quote": code_quote, "test_case": label}]}
    _, misses, _, audit = _with_d2_verified(fixture, weakness)
    assert audit["findings"][-1]["evidence_status"] == "SUPPORTED" and weakness["claim"] in misses


def test_weakness_resting_only_on_the_verified_defects_failure_output_is_not_a_second_penalty():
    fixture = _load("C")
    label = fixture["tc_evidence"][1]["label"]
    weakness = {"claim": "Test case 8: the API assertion failed", "severity": "high",
                "evidence": [{"quote": "FAIL: dup-api Successful count - expected 1, got 2", "test_case": label}]}
    _, misses, final, audit = _with_d2_verified(fixture, weakness)
    assert audit["findings"][-1]["evidence_status"] == "SUPERSEDED_BY_DEFECT"
    assert weakness["claim"] not in misses
    assert audit["findings"][0]["evidence_status"] == "VERIFIED_DEFECT"


def test_failure_signal_in_another_test_cases_output_does_not_cancel():
    """D2 is verified only at test case 8. Test case 0 (never aimed at duplicates) prints the
    same failure line in its own run; a weakness about test case 0 citing it is not cancelled."""
    fixture = _load("C")
    line = "FAIL: dup-api Successful count - expected 1, got 2"
    fixture["tc_evidence"][0]["execution_result"]["stdout"] += "\n" + line
    other = fixture["tc_evidence"][0]["label"]
    weakness = {"claim": "Test case 0: asserts on an unrelated count", "severity": "high",
                "evidence": [{"quote": line, "test_case": other}]}
    _, misses, _, audit = _with_d2_verified(fixture, weakness)
    assert [d["defect_id"] for d in audit["defects_detected"]] == ["D2", "D2"]  # still verified at test case 8
    assert audit["findings"][-1]["evidence_status"] == "SUPPORTED"
    assert weakness["claim"] in misses


def test_c_detection_without_run_output_evidence_is_unverified():
    fixture = _load("C")
    label = fixture["tc_evidence"][1]["label"]
    design_quote = fixture["tc_evidence"][1]["design"]["title"][:50]
    findings = [{"claim": "Test case 8: found the duplicate debit defect", "severity": "high", "kind": "defect_detected",
                 "defect_id": "D2", "evidence": [{"quote": design_quote, "test_case": label}]}]
    _, misses, final, audit = _replay(fixture, findings=findings, known_defects=[D2])
    assert audit["findings"][0]["evidence_status"] == "UNVERIFIED_DETECTION"
    assert audit["findings"][0]["reason"] == "not_shown_in_run_output"
    assert audit["needs_review"] is True and audit["unverified_detections"][0]["defect_id"] == "D2"
    assert misses == [] and final == 80  # no credit, no refund
    assert "defects_detected" not in audit


def test_c_detection_citing_its_own_code_not_the_run_is_unverified():
    """The code saying what it will check is not the run showing the defect."""
    fixture = _load("C")
    label = fixture["tc_evidence"][1]["label"]
    code_quote = "# Step 6: DB: count rows in transaction with remarks 'dup-api' and status 'Successful' (expect 1)"
    assert code_quote in fixture["tc_evidence"][1]["final_code"]
    findings = [{"claim": "Test case 8: duplicate debit", "severity": "high", "kind": "defect_detected", "defect_id": "D2",
                 "evidence": [{"quote": code_quote, "test_case": label}]}]
    _, _, final, audit = _replay(fixture, findings=findings, known_defects=[dict(D2, observable_signals=["dup-api"])])
    assert audit["findings"][0]["evidence_status"] == "UNVERIFIED_DETECTION"
    assert audit["findings"][0]["reason"] == "not_shown_in_run_output"
    assert audit["needs_review"] is True and final == 80


def test_c_detection_merely_mentioned_without_evidence_is_unverified():
    fixture = _load("C")
    findings = [{"claim": "Test case 8: the app allows duplicates", "severity": "high",
                 "kind": "defect_detected", "defect_id": "D2", "evidence": []}]
    _, _, final, audit = _replay(fixture, findings=findings, known_defects=[D2])
    assert audit["findings"][0]["evidence_status"] == "UNVERIFIED_DETECTION"
    assert audit["needs_review"] is True and final == 80


def test_detection_on_a_test_that_did_not_target_the_behaviour_is_unverified():
    fixture = _load("C")
    off_target = dict(D2, targets=["beneficiary deletion"])
    _, _, _, audit = _replay(fixture, findings=_c_as_detection(fixture), known_defects=[off_target])
    assert {f["reason"] for f in audit["findings"][:2]} == {"test_did_not_target_behaviour"}


def test_unknown_or_non_creditable_defect_is_never_credited():
    fixture = _load("C")
    findings = _c_as_detection(fixture)
    findings[0]["defect_id"] = "D9"
    findings[1]["defect_id"] = "D1"
    _, _, _, audit = _replay(fixture, findings=findings, known_defects=[D1, D2])
    assert [f["reason"] for f in audit["findings"][:2]] == ["unknown_defect", "not_creditable"]
    assert "defects_detected" not in audit


def test_defect_detected_with_fabricated_run_output_is_unverified():
    fixture = _load("C")
    label = fixture["tc_evidence"][1]["label"]
    findings = [{"claim": "Test case 8: duplicate", "severity": "high", "kind": "defect_detected", "defect_id": "D2",
                 "evidence": [{"quote": "FAIL: dup-x Successful count - expected 1, got 2", "test_case": label}]}]
    _, _, _, audit = _replay(fixture, findings=findings, known_defects=[D2])
    assert audit["findings"][0]["reason"] == "evidence_not_established"


# ---- final_score = sum of the five areas ----

def test_final_score_is_the_sum_of_the_five_sub_scores():
    fixture = _load("B")
    result, _, final, _ = _replay(fixture, final=99)
    assert result["final_score"] == 93 and result["ai_final_score"] == 99 and final == 93


def test_sub_scores_are_clamped():
    result = {"scores": {"automation_design": 25, "test_data_and_assertions": -3, "ai_usage": 20,
                         "ai_output_review": 20, "execution_and_validation": 20}, "final_score": 82}
    scoring_service._r2_final_from_subscores(result)
    assert result["final_score"] == 80
    result = {"scores": {a: 30 for a in GOOD}, "final_score": 100}
    scoring_service._r2_final_from_subscores(result)
    assert result["final_score"] == 100


GOOD = {"automation_design": 18, "test_data_and_assertions": 16, "ai_usage": 18,
        "ai_output_review": 18, "execution_and_validation": 10}


def test_valid_areas_beat_the_ai_total_and_extra_areas_are_ignored():
    result = {"scores": dict(GOOD, bonus=50), "final_score": 100}
    scoring_service._r2_final_from_subscores(result)
    assert result["final_score"] == 80 and result["ai_final_score"] == 100


INVALID_SCORES = {
    "missing area": {k: v for k, v in GOOD.items() if k != "ai_usage"},
    "string area": dict(GOOD, ai_usage="18"),
    "null area": dict(GOOD, ai_usage=None),
    "nan area": dict(GOOD, ai_usage=float("nan")),
    "infinite area": dict(GOOD, ai_usage=float("inf")),
    "bool area": dict(GOOD, ai_usage=True),
    "no scores": None,
    "scores not a dict": [18, 16, 18, 18, 10],
}


@pytest.mark.parametrize("case", list(INVALID_SCORES))
def test_invalid_area_scores_never_fall_back_to_the_ai_total(case):
    result = {"final_score": 100, "findings": []}
    if INVALID_SCORES[case] is not None:
        result["scores"] = INVALID_SCORES[case]
    with pytest.raises(ValueError):
        scoring_service._r2_final_from_subscores(result)


@pytest.mark.parametrize("case", ["missing area", "string area", "null area", "nan area"])
def test_invalid_area_scores_land_the_submission_in_scoring_failed(client, monkeypatch, case):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))
    _sequential_call_claude(monkeypatch, _SUFFICIENT, json.dumps(
        {"response_kind": "code_edit", "response_message": "ok", "code_after": "x = 1\n"}))
    client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode step 1"}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/run", json={"code": "x = 1\n"}, cookies=_auth(cand_token))
    monkeypatch.setattr(llm_service, "score_round2_automation_conversation", lambda **k: {
        "scores": copy.deepcopy(INVALID_SCORES[case]), "final_score": 100, "findings": [], "feedback_text": "ok"})
    client.post("/candidate/round/2/auto/submit", json=_submit_payload(), cookies=_auth(cand_token))

    import app.database as database_module
    from app.models import RoundStatus, Score, Submission
    db = database_module.SessionLocal()
    sub = db.query(Submission).filter(Submission.round_number == 2).one()
    assert sub.status == RoundStatus.scoring_failed
    assert "area scores" in (sub.scoring_error or "")
    score = db.query(Score).filter(Score.submission_id == sub.id).one_or_none()
    assert score is None or score.final_score != 100
    db.close()


# ---- prompt / scorer inputs ----

def test_prompt_carries_known_defects_only_as_reference():
    captured = {}
    original = llm_service._call_claude

    def fake(prompt, *a, **k):
        captured["prompt"] = prompt
        raise RuntimeError("stop")

    llm_service._call_claude = fake
    try:
        for kd in (None, "- D2: requirement: x"):
            kwargs = dict(language="python", tc_evidence=[], ground_truth="g", validation_notes="v")
            if kd:
                kwargs["known_defects"] = kd
            with pytest.raises(Exception):
                llm_service.score_round2_automation_conversation(**kwargs)
            prompt = captured["prompt"]
            ref = prompt.split("REFERENCE ONLY (never candidate evidence)")[1].split("END REFERENCE ONLY")[0]
            assert (kd or "None declared.") in ref
    finally:
        llm_service._call_claude = original


def test_known_defects_prompt_text_marks_non_creditable():
    text = scoring_service._known_defects_for_prompt([D1, D2])
    assert "D1:" in text and "creditable: no" in text and "D2:" in text and "creditable: yes" in text
    assert scoring_service._known_defects_for_prompt([]) == "None declared."


# ---- candidate isolation ----

def test_known_defects_never_reach_any_candidate_facing_response(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    marker = "OPERATOR-ONLY-DEFECT-MARKER"
    import app.database as database_module
    from app.models import Scenario
    db = database_module.SessionLocal()
    scenario = db.query(Scenario).filter(Scenario.round_number == 2).one()
    scenario.reference_json = {**(scenario.reference_json or {}), "known_defects": [
        dict(D2, requirement=marker, observable_signals=["x = 1"], targets=["step"])]}
    db.commit()
    db.close()

    bodies = [_state(client, cand_token)]
    _sequential_call_claude(monkeypatch, _SUFFICIENT, json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded it.", "code_after": "x = 1\n"}))
    bodies.append(client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode step 1"}, cookies=_auth(cand_token)).json())
    bodies.append(client.post("/candidate/round/2/auto/run", json={"code": "x = 1\n"}, cookies=_auth(cand_token)).json())

    seen = {}

    def fake_score(**k):
        seen.update(k)
        return {"scores": {"automation_design": 20, "test_data_and_assertions": 20, "ai_usage": 20,
                           "ai_output_review": 20, "execution_and_validation": 20},
                "final_score": 100, "findings": [], "feedback_text": "ok"}

    monkeypatch.setattr(llm_service, "score_round2_automation_conversation", fake_score)
    bodies.append(client.post("/candidate/round/2/auto/submit", json=_submit_payload(), cookies=_auth(cand_token)).json())
    bodies.append(client.get("/candidate/submissions", cookies=_auth(cand_token)).json())
    bodies.append(client.get("/candidate/round/2", cookies=_auth(cand_token)).json())

    assert marker in seen["known_defects"]  # the scorer got it as reference text...
    for body in bodies:  # ...the candidate never sees it
        blob = json.dumps(body)
        assert marker not in blob
        assert "known_defects" not in blob and "defects_detected" not in blob and "VERIFIED_DEFECT" not in blob
