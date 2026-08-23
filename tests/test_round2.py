"""
Round 2 (debugging) reuses round 1's reference-generation/publish pipeline,
but candidates submit an investigation write-up (a list of areas checked
plus one root-cause conclusion - see schemas.Round2SubmissionCreate), not
test-case rows - scored via scoring_service.score_round2_investigation.
These tests follow the same fake-llm_service approach as test_round1.py.
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD,
    CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,  # 0-7 band
    FAKE_REFERENCE, _login, _auth, _publish_scenario,
)

FAKE_INVESTIGATION = [{"area": "Checked API response for a 299 customer in the affected location"}]
FAKE_ROOT_CAUSE = "A location-based advertisement rule overrides the 299 package's ad-free entitlement."


def test_round2_scenario_creation_generates_reference(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round2_reference", lambda **kwargs: list(FAKE_REFERENCE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    res = client.post(
        "/hr/scenarios",
        json={"round_number": 2, "title": "Off-by-one in pagination", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 20},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 201
    assert res.json()["reference_json"] == FAKE_REFERENCE


def test_round2_locked_until_round1_submitted(client, monkeypatch):
    _publish_scenario(client, _login(client, HR_EMAIL, HR_PASSWORD), monkeypatch, round_number=2)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.get("/candidate/round/2", cookies=_auth(cand_token))
    assert res.status_code == 403


def test_round2_submit_requires_investigation_and_root_cause(client, monkeypatch):
    from app.services import llm_service
    # Round 1's submit below fires a real background scoring call unless
    # mocked - separate function from the reference generator
    # _publish_scenario already mocks.
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="Round 1 gate")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="Race condition in checkout")

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "Case", "steps": "...", "expected_result": "..."}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    # Missing required fields entirely - schema validation, not app logic.
    res = client.post("/candidate/round/2/submit", json={"investigation": []}, cookies=_auth(cand_token))
    assert res.status_code == 422


def test_round2_submission_scored_via_background_task(client, monkeypatch):
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="Round 1 gate")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="Race condition in checkout")

    monkeypatch.setattr(
        llm_service, "score_round2_submission",
        lambda **kwargs: {"coverage_score": 70, "misses": ["didn't check for a race condition"], "final_score": 65, "feedback_text": "Reasonable start."},
    )
    # Round 1's own submit below also fires a real background scoring
    # call unless mocked too.
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    # Clear round 1 first - round 2 is gated behind it.
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "Case", "steps": "...", "expected_result": "..."}]},
        cookies=_auth(cand_token),
    )

    res = client.get("/candidate/round/2", cookies=_auth(cand_token))
    assert res.status_code == 200
    assert res.json()["scenario"]["title"] == "Race condition in checkout"

    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    res = client.post(
        "/candidate/round/2/submit",
        json={"investigation": FAKE_INVESTIGATION, "root_cause": FAKE_ROOT_CAUSE},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    submission_id = res.json()["id"]
    assert res.json()["content"]["root_cause"] == FAKE_ROOT_CAUSE

    res = client.get("/candidate/submissions", cookies=_auth(cand_token))
    scored = next(s for s in res.json() if s["id"] == submission_id)
    assert scored["status"] == "scored"
    assert "score" not in scored  # same candidate-facing rule as round 1

    # HR dashboard reflects the round 2 result.
    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    round2 = next(r for r in candidate_row["rounds"] if r["round_number"] == 2)
    assert round2["final_score"] == 65

    # And the report includes the investigation + root cause for HR's review.
    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_round2 = next(s for s in res.json() if s["round_number"] == 2)
    assert report_round2["content"]["investigation"] == FAKE_INVESTIGATION
    assert report_round2["content"]["root_cause"] == FAKE_ROOT_CAUSE

    # And the history dashboard picks up round 2 the same way it does round 1.
    res = client.get("/hr/history", cookies=_auth(hr_token))
    entry = next(h for h in res.json() if h["round_number"] == 2)
    assert entry["title"] == "Race condition in checkout"
    assert entry["scored_count"] == 1
    assert entry["not_cleared_count"] == 1  # 65 < default passing_score of 70


def test_round4_dedicated_submit_endpoint_is_reached_not_the_generic_one(client, monkeypatch):
    """Round 4 has its own endpoints (/round/4/turn, /round/4/submit,
    no body) - confirms /candidate/round/4/submit reaches that dedicated
    handler rather than being shadowed by the generic row-based
    /candidate/round/{n}/submit (round 1 only now, expects a `content`
    list it would 422 on). No round 4 scenario is published in this
    test, so the dedicated handler's own 404 ("no published scenario")
    is what proves it was reached instead.

    Round 3 has to be completed too, not just 1/2 - ROUND_SEQUENCE is
    (1, 2, 3, 4) now that round 3 (AI-prompted coding) is a real round
    again, so round 4 stays locked (403) behind it otherwise, and this
    test would never reach the dedicated handler it's trying to prove
    gets reached at all."""
    from app.services import llm_service, execution_service
    # Both submits below fire real background scoring calls unless mocked.
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "round3_coding_turn", lambda **kwargs: {"response_kind": "code_edit", "response_message": "ok", "code_after": "print(1)"})
    monkeypatch.setattr(llm_service, "score_round3_coding", lambda **kwargs: {
        "correctness_score": 100, "precision_score": 100, "efficiency_score": 100,
        "independent_judgment_score": 100, "final_score": 100,
        "misses": [], "guardrail_violations": [], "feedback_text": "ok",
    })
    monkeypatch.setattr(execution_service, "run_code", lambda **kwargs: execution_service.ExecutionResult(
        stdout="", stderr="", exit_code=0, timed_out=False, infra_error=False, duration_ms=1,
    ))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2")
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="R3")

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": FAKE_INVESTIGATION, "root_cause": FAKE_ROOT_CAUSE}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "solve it"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/submit", cookies=_auth(cand_token))

    res = client.post("/candidate/round/4/submit", cookies=_auth(cand_token))
    assert res.status_code == 404
    assert "published scenario" in res.json()["detail"]
