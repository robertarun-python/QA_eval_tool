"""
AI-Assisted Test Automation round (round 4 slot, config_json["mode"] ==
"ai_test_automation") - the candidate automates the test cases THEY
designed in round 1.

Same fake-llm_service discipline as the rest of this app's tests: never a
real Claude call. execution_service.run_code is exercised for REAL in the
execution test (proving the provided environment actually runs under the
existing single-file engine, unmodified) and left mocked elsewhere.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest

from .conftest import (
    HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    _login, _auth, _publish_scenario,
)

from app.services import llm_service, round4_auto_policy, execution_service

HELPERS_DIR = Path(__file__).parent.parent / "backend" / "app" / "prompts"
PYTHON_ENV = (HELPERS_DIR / "round4_auto_helpers_python.txt").read_text(encoding="utf-8")

# The candidate's own round 1 design - the immutable source of truth this
# whole round hangs off.
R1_ROWS = [
    {
        "title": "Reject zero amount",
        "preconditions": "Logged in",
        "steps": "Submit the amount form with 0.00",
        "test_data": "owner = jordan.rivera@example.com; amount = 0.00",
        "expected_result": "Rejected with 'Amount must be greater than zero' and nothing persisted",
    },
    {
        "title": "Accept a valid amount",
        "preconditions": "Logged in",
        "steps": "Submit the amount form with 25.50",
        "test_data": "owner = jordan.rivera@example.com; amount = 25.50",
        "expected_result": "Record persisted with status completed",
    },
    {
        "title": "Third case, not selected",
        "steps": "something else",
        "test_data": "n/a",
        "expected_result": "something",
    },
]

GROUND_TRUTH = "create_record rejects amount <= 0 and writes nothing; Database.find is the only proof of persistence."


def _publish_auto_scenario(client, hr_token, band="0-7"):
    """Creates + publishes the automation scenario directly (its config/
    reference are HR/system-authored, not LLM-generated - see
    seed_round4_auto.py, which this mirrors without touching the real DB)."""
    scenario = client.post(
        "/hr/scenarios",
        json={
            "round_number": 2, "title": "AI-Assisted Test Automation",
            "description": "Automate the test case(s) you designed in Round 1.",
            "experience_band": band, "time_limit_minutes": 45,
            "config_json": {"mode": "ai_test_automation", "environment_code_by_language": {"python": PYTHON_ENV}},
        },
        cookies=_auth(hr_token),
    ).json()

    import app.database as database_module
    from app.models import Scenario
    db = database_module.SessionLocal()
    row = db.query(Scenario).filter(Scenario.id == scenario["id"]).one()
    row.reference_json = {"ground_truth": GROUND_TRUTH, "validation_notes": "Assertions must prove the requirement."}
    db.commit()
    db.close()

    res = client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 200
    return scenario


def _reach_automation_round(client, hr_token, monkeypatch, r1_rows=None, language="python"):
    """Drives rounds 1-3 so round 4 is unlocked, submitting the candidate's
    real R1 rows (this round's whole input) and the R3 language choice."""
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Coding challenge")
    _publish_auto_scenario(client, hr_token)

    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **k: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **k: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "round3_coding_turn", lambda **k: {"response_kind": "code_edit", "response_message": "ok", "code_after": "print(1)"})
    monkeypatch.setattr(llm_service, "score_round3_coding", lambda **k: {
        "correctness_score": 100, "precision_score": 100, "efficiency_score": 100,
        "independent_judgment_score": 100, "final_score": 100, "misses": [], "guardrail_violations": [], "feedback_text": "ok",
    })
    monkeypatch.setattr(execution_service, "run_code", lambda **k: execution_service.ExecutionResult(
        stdout="", stderr="", exit_code=0, timed_out=False, infra_error=False, duration_ms=1,
    ))

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": r1_rows if r1_rows is not None else R1_ROWS}, cookies=_auth(cand_token))
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))
    client.post("/candidate/round/4/submit", json={"investigation": [{"area": "x"}], "root_cause": "y"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": language}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "write it"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/submit", cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    return cand_token


def _select(client, cand_token, indexes=(0,)):
    return client.post("/candidate/round/2/auto/select", json={"row_indexes": list(indexes)}, cookies=_auth(cand_token))


def _state(client, cand_token):
    return client.get("/candidate/round/2/auto/state", cookies=_auth(cand_token)).json()


# ---- R1 -> automation data continuity ----

def test_r1_design_including_test_data_reaches_the_automation_round(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)

    state = _state(client, cand_token)
    assert len(state["available_rows"]) == 3
    first = state["available_rows"][0]
    assert first["title"] == "Reject zero amount"
    assert first["test_data"] == "owner = jordan.rivera@example.com; amount = 0.00"
    assert first["expected_result"].startswith("Rejected with")
    assert state["selection_locked"] is False


def test_new_candidate_always_gets_python(client, monkeypatch):
    """DECIDED for the pilot: the automation round is Python-only for new
    candidates. It measures automation/QA thinking, not language choice,
    and a language picker isn't worth the complexity in a 30-minute round.

    Round 3 (where a language IS chosen) runs after round 2 since the
    2<->4 renumbering, so a new candidate has no round 3 on file at all -
    the setup below deliberately asks for "javascript" to prove that a
    round 3 preference expressed later cannot leak backwards into this
    round. See _round3_language_for."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch, language="javascript")
    assert _state(client, cand_token)["language"] == "python"


def test_language_still_honours_an_existing_round3_submission(client, monkeypatch):
    """The inheritance path itself is intact for the cases where a round 3
    submission does exist (a re-entering candidate, or data written before
    the renumbering) - it just isn't reachable in the normal new order."""
    from app.routers import candidate as candidate_router
    import app.database as database_module
    from app.models import Submission, Scenario, User, RoundStatus
    from datetime import datetime

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)

    db = database_module.SessionLocal()
    user = db.query(User).filter(User.email == CANDIDATE1_EMAIL).first()
    scenario = db.query(Scenario).filter(Scenario.round_number == 3).first()
    db.add(Submission(
        user_id=user.id, scenario_id=scenario.id, round_number=3,
        status=RoundStatus.submitted, started_at=datetime.utcnow(), submitted_at=datetime.utcnow(),
        content={"language": "javascript"},
    ))
    db.commit()
    resolved = candidate_router._round3_language_for(user, db)
    db.close()

    assert resolved == "javascript"


# ---- Test selection + immutable snapshot ----

def test_selecting_one_or_two_rows_locks_an_immutable_snapshot(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)

    res = _select(client, cand_token, (0, 1))
    assert res.status_code == 201
    state = res.json()
    assert state["selection_locked"] is True
    assert [r["index"] for r in state["selected"]] == [0, 1]
    assert state["selected"][0]["test_data"] == "owner = jordan.rivera@example.com; amount = 0.00"
    # Starting code is the provided environment, not a blank file.
    assert "class UI:" in state["code"]


def test_selection_cannot_be_changed_once_locked(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    assert _select(client, cand_token, (0,)).status_code == 201

    res = _select(client, cand_token, (1,))
    assert res.status_code == 400
    assert "locked" in res.json()["detail"].lower()
    assert [r["index"] for r in _state(client, cand_token)["selected"]] == [0]


def test_selection_rejects_more_than_two_duplicates_and_bad_indexes(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)

    assert client.post("/candidate/round/2/auto/select", json={"row_indexes": [0, 1, 2]}, cookies=_auth(cand_token)).status_code == 422
    assert client.post("/candidate/round/2/auto/select", json={"row_indexes": []}, cookies=_auth(cand_token)).status_code == 422
    assert client.post("/candidate/round/2/auto/select", json={"row_indexes": [0, 0]}, cookies=_auth(cand_token)).status_code == 400
    assert client.post("/candidate/round/2/auto/select", json={"row_indexes": [99]}, cookies=_auth(cand_token)).status_code == 400


def test_original_round1_submission_is_never_mutated_by_the_automation_round(client, monkeypatch):
    """The snapshot is a COPY. Nothing this round does may reach back and
    edit the round 1 submission HR audits."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)

    def _stored_r1():
        res = client.get("/hr/candidates", cookies=_auth(hr_token))
        candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
        res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
        return next(s for s in res.json() if s["round_number"] == 1)["content"]

    # Snapshot BEFORE the automation round touches anything. Compared
    # before-vs-after rather than against the literal R1_ROWS input,
    # because round 1's own submit normalizes rows through TestCaseRow
    # (see candidate.py's submit_round: row.model_dump()), so the stored
    # shape legitimately carries the schema defaults. What must hold is
    # that THIS round changes none of it.
    before = _stored_r1()
    assert before[0]["test_data"] == R1_ROWS[0]["test_data"]

    _select(client, cand_token, (0,))
    client.post("/candidate/round/2/auto/refine", json={"row_index": 0, "note": "changed my mind about the message"}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/code", json={"code": "print('edit')"}, cookies=_auth(cand_token))

    assert _stored_r1() == before


# ---- Append-only refinement ----

def test_refinement_is_append_only_and_never_overwrites_the_original(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    client.post("/candidate/round/2/auto/refine", json={"row_index": 0, "note": "first note"}, cookies=_auth(cand_token))
    res = client.post("/candidate/round/2/auto/refine", json={"row_index": 0, "note": "second note"}, cookies=_auth(cand_token))
    assert res.status_code == 201

    selected = res.json()["selected"][0]
    assert selected["refinements"] == ["first note", "second note"]
    # Original fields untouched by either note.
    assert selected["expected_result"] == R1_ROWS[0]["expected_result"]
    assert selected["test_data"] == R1_ROWS[0]["test_data"]


def test_refinement_requires_a_selected_row(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    assert client.post("/candidate/round/2/auto/refine", json={"row_index": 0, "note": "x"}, cookies=_auth(cand_token)).status_code == 400

    _select(client, cand_token, (0,))
    res = client.post("/candidate/round/2/auto/refine", json={"row_index": 1, "note": "x"}, cookies=_auth(cand_token))
    assert res.status_code == 400  # row 1 was not selected


# ---- AI generation boundary ----

PROHIBITED = [
    "what else should I test here?",
    "suggest test cases for this",
    "invent test data for the amount field",
    "what should I assert?",
    "write the whole test for me",
    "what edge cases am I missing?",
]


@pytest.mark.parametrize("prompt", PROHIBITED)
def test_prohibited_requests_are_refused_with_zero_llm_calls(client, monkeypatch, prompt):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    calls = {"n": 0}
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: calls.update(n=calls["n"] + 1) or "{}")

    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": prompt}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["response_kind"] == "refuse"
    assert res.json()["code_after"] is None
    assert calls["n"] == 0, "the deterministic policy must short-circuit before any LLM call"


def test_legitimate_request_reaches_the_generator_and_updates_the_code(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    generated = PYTHON_ENV + "\ndef test_reject_zero():\n    assert API.create_record('jordan.rivera@example.com', 0.00)['ok'] is False\n"
    captured = {}

    def _fake_call(prompt, max_tokens=4096):
        captured["prompt"] = prompt
        return json.dumps({"response_kind": "code_edit", "response_message": "Encoded your step.", "code_after": generated})

    monkeypatch.setattr(llm_service, "_call_claude", _fake_call)
    res = client.post(
        "/candidate/round/2/auto/turn",
        json={"candidate_prompt": "encode step 1 of my first test case using API.create_record"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    assert res.json()["response_kind"] == "code_edit"
    assert _state(client, cand_token)["code"] == generated

    # The candidate's own design IS given to the generator; the scenario's
    # HR-only ground truth is NOT.
    assert "Amount must be greater than zero" in captured["prompt"]
    assert GROUND_TRUTH not in captured["prompt"]


def test_untraceable_literal_detection_flags_invented_values_only():
    """Deterministic post-generation control - see round4_auto_policy."""
    selected = [{"test_data": "amount = 0.00", "expected_result": "rejected", "steps": "submit", "title": "t"}]
    flagged = round4_auto_policy.untraceable_literals(
        "a = 0.00\nb = 77.77\nUI.login('nobody@example.com')", selected, environment_code="def login(): pass",
    )
    assert "77.77" in flagged                      # value the candidate never specified
    assert "nobody@example.com" in flagged
    assert "0.00" not in flagged                   # straight from their own test data


# ---- Candidate code edit ----

def test_candidate_code_edit_is_recorded_separately_from_ai_output(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    edited = PYTHON_ENV + "\n# my own edit\n"
    res = client.post("/candidate/round/2/auto/code", json={"code": edited}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["code"] == edited
    assert res.json()["code_edits_count"] == 1

    client.post("/candidate/round/2/auto/code", json={"code": edited + "# again\n"}, cookies=_auth(cand_token))
    assert _state(client, cand_token)["code_edits_count"] == 2


# ---- Execution ----

def test_run_executes_the_real_provided_environment_via_the_existing_engine(client, monkeypatch):
    """No execution mocking here - proves the provided Python environment
    genuinely runs under the SAME execution_service.run_code everything
    else uses, unmodified, and that Run uses the editor's current text."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))
    monkeypatch.undo()  # restore the real run_code

    code = PYTHON_ENV + """
def test_reject_zero():
    setup()
    result = API.create_record("jordan.rivera@example.com", 0.00)
    assert result["ok"] is False
    assert Database.find(result["id"]) is None
    print("PASS: rejected zero amount")

test_reject_zero()
"""
    res = client.post("/candidate/round/2/auto/run", json={"code": code}, cookies=_auth(cand_token))
    assert res.status_code == 201
    run = res.json()
    assert run["infra_error"] is False
    assert run["exit_code"] == 0
    assert "PASS: rejected zero amount" in run["stdout"]


def test_run_requires_a_selection_first(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    assert client.post("/candidate/round/2/auto/run", cookies=_auth(cand_token)).status_code == 400


# ---- Submission + scoring/audit ----

def _submit_payload(code="x = 1\n", validation="The run proves the zero amount was rejected and nothing persisted."):
    return {"code": code, "validation": validation}


def test_submit_requires_a_run_and_an_interpretation(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    # No interpretation -> schema rejection.
    assert client.post("/candidate/round/2/auto/submit", json={"code": "x = 1"}, cookies=_auth(cand_token)).status_code == 422
    # Never run -> business rejection.
    res = client.post("/candidate/round/2/auto/submit", json=_submit_payload(), cookies=_auth(cand_token))
    assert res.status_code == 400
    assert "run" in res.json()["detail"].lower()


def test_full_submission_scores_and_audits_via_the_existing_patterns(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))
    client.post("/candidate/round/2/auto/refine", json={"row_index": 0, "note": "exact-match message"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded it.", "code_after": "print('x')"}))
    client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode my step 1"}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/code", json={"code": "print('my edit')"}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/run", json={"code": "print('my edit')"}, cookies=_auth(cand_token))

    captured = {}

    def _fake_scoring(**kwargs):
        captured.update(kwargs)
        return {
            "scores": {"automation_design": 16, "test_data_and_assertions": 14, "ai_usage": 15,
                       "ai_output_review": 13, "execution_and_validation": 12},
            "final_score": 70,
            "findings": [{"claim": "Asked the assistant to encode without stating the assertion",
                          "severity": "low",
                          "evidence": [{"turn": 1, "quote": "encode my step 1"}]}],
            "feedback_text": "Solid, but verify what the assertion proves.",
        }

    monkeypatch.setattr(llm_service, "score_round4_auto_conversation", _fake_scoring)

    res = client.post("/candidate/round/2/auto/submit", json=_submit_payload(validation="Proves rejection; does not prove persistence."), cookies=_auth(cand_token))
    assert res.status_code == 201

    # The scorer got the full primary-evidence set, plus REFERENCE-ONLY ground truth.
    assert captured["selected_design"][0]["title"] == "Reject zero amount"
    assert captured["refinements"][0]["note"] == "exact-match message"
    assert len(captured["turns"]) == 1
    assert len(captured["code_edits"]) == 1
    assert captured["validation_text"].startswith("Proves rejection")
    assert captured["ground_truth"] == GROUND_TRUTH
    assert captured["language"] == "python"

    # HR sees the score, the surviving finding, and the evidence audit.
    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    r4 = next(r for r in candidate_row["rounds"] if r["round_number"] == 2)
    assert r4["final_score"] == 70

    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_r4 = next(s for s in res.json() if s["round_number"] == 2)
    assert report_r4["score"]["misses_json"] == ["Asked the assistant to encode without stating the assertion"]
    assert report_r4["score"]["evidence_audit"]["total_findings"] == 1

    # Candidate still never sees any score.
    res = client.get("/candidate/submissions", cookies=_auth(cand_token))
    assert "score" not in next(s for s in res.json() if s["round_number"] == 2)


def test_unsupported_finding_is_dropped_by_the_shared_evidence_audit(client, monkeypatch):
    """The automation round reuses round4_evidence_audit unmodified - a
    finding citing a quote that was never said must not survive."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))
    client.post("/candidate/round/2/auto/run", json={"code": "print(1)"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "score_round4_auto_conversation", lambda **k: {
        "scores": {"automation_design": 10, "test_data_and_assertions": 10, "ai_usage": 10,
                   "ai_output_review": 10, "execution_and_validation": 10},
        "final_score": 50,
        "findings": [{"claim": "Said something they never said", "severity": "high",
                      "evidence": [{"turn": 1, "quote": "this quote does not exist anywhere"}]}],
        "feedback_text": "x",
    })

    client.post("/candidate/round/2/auto/submit", json=_submit_payload(), cookies=_auth(cand_token))

    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_r4 = next(s for s in res.json() if s["round_number"] == 2)
    assert report_r4["score"]["misses_json"] == []          # dropped
    assert report_r4["score"]["final_score"] > 50           # its severity points were restored


# ---- Legacy scenario compatibility ----

def test_legacy_round1_rows_without_test_data_still_work(client, monkeypatch):
    """A candidate whose round 1 predates the test_data field must still
    be able to select and automate - the field reads back as ""."""
    legacy_rows = [{"title": "Old shape", "steps": "do it", "expected_result": "it happens"}]
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch, r1_rows=legacy_rows)

    state = _state(client, cand_token)
    assert state["available_rows"][0]["test_data"] == ""
    res = _select(client, cand_token, (0,))
    assert res.status_code == 201
    assert res.json()["selected"][0]["test_data"] == ""


def test_auto_endpoints_reject_a_non_automation_round4_scenario(client, monkeypatch):
    """The other two round 4 modes are untouched: their scenarios must not
    be drivable through the automation endpoints."""
    from .conftest import _publish_round4_scenario
    from .test_round4 import _complete_round1_and_2

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    _publish_round4_scenario(client, hr_token, monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    res = client.get("/candidate/round/2/auto/state", cookies=_auth(cand_token))
    assert res.status_code == 400
    assert "not an AI-assisted automation scenario" in res.json()["detail"]


def test_publish_gate_requires_ground_truth_and_environment(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = client.post(
        "/hr/scenarios",
        json={"round_number": 2, "title": "Incomplete automation", "description": "d",
              "experience_band": "0-7", "time_limit_minutes": 45,
              "config_json": {"mode": "ai_test_automation"}},
        cookies=_auth(hr_token),
    ).json()

    res = client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 400
    assert "ground truth" in res.json()["detail"].lower()


# ---- Escaped-code repair (see llm_service._repair_escaped_code) ----
# Observed live during end-to-end validation: the model sometimes escapes
# its own JSON escapes, so code_after arrives as a single line containing
# literal backslash-n pairs. json.loads returns that faithfully and the
# generated automation then fails with a SyntaxError on line 1 - unusable.

def test_escaped_code_from_the_model_is_repaired():
    """A whole file arriving escaped - the shape seen live, where 137
    escaped pairs sat behind a single trailing real newline. The fixture
    keeps several escaped lines on purpose: the guard ignores blocks with
    fewer than 3, so that a short snippet whose only escapes are genuine
    string literals is never rewritten (see _repair_escaped_code)."""
    from app.services.llm_service import _repair_escaped_code
    bs = chr(92)
    escaped = (
        "def t():" + bs + "n"
        "    x = 1" + bs + "n"
        "    y = 2" + bs + "n"
        "    print(" + bs + '"ok' + bs + '")' + bs + "n"
        "    return x + y" + "\n"   # the stray real newline that defeated the first guard
    )
    repaired = _repair_escaped_code(escaped)
    assert repaired.count("\n") == 5
    assert bs + "n" not in repaired
    compile(repaired, "<generated>", "exec")  # the whole point: it must be runnable


def test_well_formed_code_is_never_touched():
    """The guard is narrow on purpose - anything with real newlines (i.e.
    every well-formed multi-line file) must pass through untouched, so a
    deliberate literal backslash-n inside normal code can't be mangled."""
    from app.services.llm_service import _repair_escaped_code
    good = 'def t():\n    print("a\nb")\n'
    assert _repair_escaped_code(good) == good
    assert _repair_escaped_code("x = 1") == "x = 1"
    assert _repair_escaped_code(None) is None
    assert _repair_escaped_code("") == ""


def test_auto_turn_repairs_escaped_code_end_to_end(monkeypatch):
    """Through the real round4_auto_turn, not just the helper."""
    import json as _json
    from app.services import llm_service
    bs = chr(92)
    payload = {"response_kind": "code_edit", "response_message": "done",
               "code_after": "def t():" + bs + "n    x = 1" + bs + "n    y = 2" + bs + "n    return x + y"}
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: _json.dumps(payload))
    out = llm_service.round4_auto_turn(
        language="python", selected_design=[{"title": "t"}], environment_code="",
        current_code="", conversation_so_far=[], candidate_prompt="encode my step 1",
    )
    assert out["code_after"] == "def t():\n    x = 1\n    y = 2\n    return x + y"


# ---- Scoring fidelity: a supported finding must survive to the Score ----
# End-to-end counterpart to test_round4_evidence_audit.py's unit coverage:
# proves the persisted evidence actually reaches the auditor through
# score_round4_auto_submission, not just that the auditor can use it.

def _score_with_finding(client, monkeypatch, hr_token, cand_token, finding, final_score=98):
    from app.services import llm_service as _llm
    _select(client, cand_token, (0,))
    client.post("/candidate/round/2/auto/code", json={"code": "print('x')\nassert 1 == 1\n"}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/run", json={"code": "print('x')\nassert 1 == 1\n"}, cookies=_auth(cand_token))
    monkeypatch.setattr(_llm, "score_round4_auto_conversation", lambda **k: {
        "scores": {"automation_design": 20, "test_data_and_assertions": 20, "ai_usage": 20,
                   "ai_output_review": 20, "execution_and_validation": 18},
        "final_score": final_score, "findings": [finding], "feedback_text": "ok",
    })
    res = client.post(
        "/candidate/round/2/auto/submit",
        json={"code": "print('x')\nassert 1 == 1\n",
              "validation": "The run proves the assertion held, but it does not prove persistence."},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    res = client.get(f"/hr/candidates/{row['id']}/report", cookies=_auth(hr_token))
    return next(s for s in res.json() if s["round_number"] == 2)["score"]


def test_finding_quoting_the_candidates_own_interpretation_is_not_refunded(client, monkeypatch):
    """The 98->100 defect, end to end: this quote is real text the
    candidate wrote, so the deduction must stand at 98."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    score = _score_with_finding(client, monkeypatch, hr_token, cand_token, {
        "claim": "Interpretation concedes persistence was not proven.",
        "severity": "low",
        "evidence": [{"quote": "it does not prove persistence"}],
    })
    assert score["final_score"] == 98, "a supported finding's deduction must be preserved"
    assert score["misses_json"] == ["Interpretation concedes persistence was not proven."]
    assert score["evidence_audit"]["supported"] == 1
    assert score["evidence_audit"]["not_established"] == 0


def test_fabricated_finding_is_still_dropped_and_refunded(client, monkeypatch):
    """The auditor's original job is unchanged by the fix."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    score = _score_with_finding(client, monkeypatch, hr_token, cand_token, {
        "claim": "The candidate said the run crashed.",
        "severity": "low",
        "evidence": [{"quote": "the run crashed catastrophically"}],
    })
    # 98 + 3 restored, capped at 100 by _round4_findings_to_misses - the
    # same arithmetic that produced the original 98->100 symptom. Here it
    # is CORRECT, because this finding genuinely cites nothing real.
    assert score["final_score"] == 100
    assert score["misses_json"] == []
    assert score["evidence_audit"]["not_established"] == 1
