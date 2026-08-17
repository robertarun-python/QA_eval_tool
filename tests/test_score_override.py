"""
HR's score-override + scoring-failure-recovery endpoints (see routers/hr.py's
POST /submissions/{id}/retry-scoring, PATCH /submissions/{id}/score, and
services/scoring_service.score_submission_in_background - the single,
exception-safe entry point every submit endpoint's background task and
these two HR-triggered actions all funnel through).
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    _login, _auth, _publish_scenario,
)


def _submit_round1(client, cand_token):
    client.post("/candidate/round/1/start", headers=_auth(cand_token))
    return client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        headers=_auth(cand_token),
    )


def _get_submission_id(client, hr_token, candidate_id, round_number=1):
    report = client.get(f"/hr/candidates/{candidate_id}/report", headers=_auth(hr_token)).json()
    return next(s for s in report if s["round_number"] == round_number)["id"]


def test_scoring_failure_is_surfaced_not_stranded(client, monkeypatch):
    from app.services import llm_service

    def _blow_up(**kwargs):
        raise ValueError("simulated malformed LLM response")

    monkeypatch.setattr(llm_service, "score_round1_submission", _blow_up)

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _submit_round1(client, cand_token)

    candidates = client.get("/hr/candidates", headers=_auth(hr_token)).json()
    candidate_id = next(c for c in candidates if c["email"] == CANDIDATE1_EMAIL)["id"]

    report = client.get(f"/hr/candidates/{candidate_id}/report", headers=_auth(hr_token)).json()
    submission = next(s for s in report if s["round_number"] == 1)
    assert submission["status"] == "scoring_failed"
    assert submission["score"] is None
    assert "simulated malformed LLM response" in submission["scoring_error"]


def test_retry_scoring_recovers_from_a_transient_failure(client, monkeypatch):
    from app.services import llm_service

    def _blow_up(**kwargs):
        raise ValueError("transient failure")

    monkeypatch.setattr(llm_service, "score_round1_submission", _blow_up)

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _submit_round1(client, cand_token)

    candidates = client.get("/hr/candidates", headers=_auth(hr_token)).json()
    candidate_id = next(c for c in candidates if c["email"] == CANDIDATE1_EMAIL)["id"]
    submission_id = _get_submission_id(client, hr_token, candidate_id)

    # Now make the retry succeed.
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 85, "misses": [], "final_score": 85, "feedback_text": "ok"},
    )
    res = client.post(f"/hr/submissions/{submission_id}/retry-scoring", headers=_auth(hr_token))
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "scored"
    assert body["score"]["final_score"] == 85
    assert body["scoring_error"] is None


def test_retry_scoring_requires_scoring_failed_status(client, monkeypatch):
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 85, "misses": [], "final_score": 85, "feedback_text": "ok"},
    )
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _submit_round1(client, cand_token)

    candidates = client.get("/hr/candidates", headers=_auth(hr_token)).json()
    candidate_id = next(c for c in candidates if c["email"] == CANDIDATE1_EMAIL)["id"]
    submission_id = _get_submission_id(client, hr_token, candidate_id)

    res = client.post(f"/hr/submissions/{submission_id}/retry-scoring", headers=_auth(hr_token))
    assert res.status_code == 400  # already scored, nothing to retry


def test_manual_override_on_a_failed_submission_creates_a_score(client, monkeypatch):
    from app.services import llm_service

    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: (_ for _ in ()).throw(ValueError("boom")))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _submit_round1(client, cand_token)

    candidates = client.get("/hr/candidates", headers=_auth(hr_token)).json()
    candidate_id = next(c for c in candidates if c["email"] == CANDIDATE1_EMAIL)["id"]
    submission_id = _get_submission_id(client, hr_token, candidate_id)

    res = client.patch(
        f"/hr/submissions/{submission_id}/score",
        json={"final_score": 72, "feedback_text": "Scored by hand after a repeated LLM failure.", "override_note": "LLM kept failing on malformed JSON; scored manually from the transcript."},
        headers=_auth(hr_token),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "scored"
    assert body["scoring_error"] is None
    assert body["score"]["final_score"] == 72
    assert body["score"]["original_final_score"] is None  # never had a real LLM score to preserve
    assert body["score"]["overridden_by_hr"] is True
    assert "malformed JSON" in body["score"]["override_note"]


def test_manual_override_preserves_original_score_across_multiple_overrides(client, monkeypatch):
    from app.services import llm_service

    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 60, "misses": ["boundary case"], "final_score": 60, "feedback_text": "Needs work."},
    )

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _submit_round1(client, cand_token)

    candidates = client.get("/hr/candidates", headers=_auth(hr_token)).json()
    candidate_id = next(c for c in candidates if c["email"] == CANDIDATE1_EMAIL)["id"]
    submission_id = _get_submission_id(client, hr_token, candidate_id)

    # First override: 60 -> 80.
    res = client.patch(
        f"/hr/submissions/{submission_id}/score",
        json={"final_score": 80, "override_note": "LLM missed that the candidate did cover the boundary case."},
        headers=_auth(hr_token),
    )
    assert res.json()["score"]["original_final_score"] == 60
    assert res.json()["score"]["final_score"] == 80

    # Second override: 80 -> 90 - original_final_score must stay 60, not become 80.
    res = client.patch(
        f"/hr/submissions/{submission_id}/score",
        json={"final_score": 90, "override_note": "On reflection, this deserves full marks."},
        headers=_auth(hr_token),
    )
    assert res.json()["score"]["original_final_score"] == 60
    assert res.json()["score"]["final_score"] == 90


def test_override_note_is_required_and_score_is_range_validated(client, monkeypatch):
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 60, "misses": [], "final_score": 60, "feedback_text": "ok"},
    )
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _submit_round1(client, cand_token)

    candidates = client.get("/hr/candidates", headers=_auth(hr_token)).json()
    candidate_id = next(c for c in candidates if c["email"] == CANDIDATE1_EMAIL)["id"]
    submission_id = _get_submission_id(client, hr_token, candidate_id)

    res = client.patch(f"/hr/submissions/{submission_id}/score", json={"final_score": 80}, headers=_auth(hr_token))
    assert res.status_code == 422  # missing override_note

    res = client.patch(
        f"/hr/submissions/{submission_id}/score",
        json={"final_score": 150, "override_note": "x"},
        headers=_auth(hr_token),
    )
    assert res.status_code == 422  # out of range


def test_override_and_retry_endpoints_require_hr(client, monkeypatch):
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert client.post("/hr/submissions/1/retry-scoring", headers=_auth(cand_token)).status_code == 403
    assert client.patch(
        "/hr/submissions/1/score", json={"final_score": 50, "override_note": "x"}, headers=_auth(cand_token)
    ).status_code == 403


def test_override_endpoints_404_for_nonexistent_submission(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    assert client.post("/hr/submissions/999999/retry-scoring", headers=_auth(hr_token)).status_code == 404
    assert client.patch(
        "/hr/submissions/999999/score", json={"final_score": 50, "override_note": "x"}, headers=_auth(hr_token)
    ).status_code == 404
