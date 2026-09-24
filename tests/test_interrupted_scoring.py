"""A round stuck at "submitted" because the server restarted mid-scoring
becomes scoring_failed (retryable by HR) - scoring_service.fail_interrupted_scoring."""
from datetime import datetime, timedelta

from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, HR_EMAIL, HR_PASSWORD, _auth, _login, _publish_scenario


def _stuck_round1(client, monkeypatch, minutes_ago):
    from app.services import llm_service, scoring_service
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr, monkeypatch, round_number=1)
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    # Scoring never runs - as if the server died right after the submit.
    monkeypatch.setattr(scoring_service, "score_submission_in_background", lambda submission_id: None)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **k: {"final_score": 1})
    client.post("/candidate/round/1/start", cookies=_auth(cand))
    from app.routers import candidate as candidate_router
    monkeypatch.setattr(candidate_router, "score_submission_in_background", lambda submission_id: None)
    res = client.post("/candidate/round/1/submit", json={"content": [{"title": "t", "steps": "s", "expected_result": "e"}]}, cookies=_auth(cand))
    assert res.status_code == 201, res.text
    import app.database as database_module
    from app.models import Submission
    db = database_module.SessionLocal()
    sub = db.query(Submission).filter(Submission.round_number == 1).one()
    sub.submitted_at = datetime.utcnow() - timedelta(minutes=minutes_ago)
    db.commit()
    sid = sub.id
    db.close()
    return hr, sid


def _status(sid):
    import app.database as database_module
    from app.models import Submission
    db = database_module.SessionLocal()
    try:
        s = db.get(Submission, sid)
        return s.status.value, s.scoring_error
    finally:
        db.close()


def test_stuck_scoring_becomes_retryable_when_hr_looks(client, monkeypatch):
    hr, sid = _stuck_round1(client, monkeypatch, minutes_ago=30)
    client.get("/hr/candidates", cookies=_auth(hr))
    status, error = _status(sid)
    assert status == "scoring_failed" and "interrupted" in error
    assert client.post(f"/hr/submissions/{sid}/retry-scoring", cookies=_auth(hr)).status_code in (200, 202)


def test_scoring_still_running_is_left_alone(client, monkeypatch):
    hr, sid = _stuck_round1(client, monkeypatch, minutes_ago=2)
    client.get("/hr/candidates", cookies=_auth(hr))
    assert _status(sid)[0] == "submitted"
