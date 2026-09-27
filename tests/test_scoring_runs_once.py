"""A round is scored once: scoring_service.score_submission_in_background
never re-scores a round that is already scored, and a second call for a round
whose scoring is still running does nothing - each would be a second paid AI
call whose result overwrites the first."""
from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, HR_EMAIL, HR_PASSWORD, _auth, _login, _publish_scenario


def _submitted_round1(client, monkeypatch):
    from app.routers import candidate as candidate_router
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr, monkeypatch, round_number=1)
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    monkeypatch.setattr(candidate_router, "score_submission_in_background", lambda submission_id: None)
    client.post("/candidate/round/1/start", cookies=_auth(cand))
    res = client.post("/candidate/round/1/submit", json={"content": [{"title": "t", "steps": "s", "expected_result": "e"}]}, cookies=_auth(cand))
    assert res.status_code == 201, res.text
    import app.database as database_module
    from app.models import Submission
    db = database_module.SessionLocal()
    try:
        return db.query(Submission).filter(Submission.round_number == 1).one().id
    finally:
        db.close()


def _set_status(sid, status):
    import app.database as database_module
    from app.models import RoundStatus, Submission
    db = database_module.SessionLocal()
    try:
        db.get(Submission, sid).status = RoundStatus(status)
        db.commit()
    finally:
        db.close()


def _status(sid):
    import app.database as database_module
    from app.models import Submission
    db = database_module.SessionLocal()
    try:
        return db.get(Submission, sid).status.value
    finally:
        db.close()


def _counting_scorer(monkeypatch, on_call=None):
    from app.models import RoundStatus
    from app.services import scoring_service
    calls = []

    def scorer(db, submission):
        calls.append(submission.id)
        if on_call:
            on_call(submission.id)
        submission.status = RoundStatus.scored
        db.commit()

    monkeypatch.setitem(scoring_service._SCORERS, 1, scorer)
    return calls


def test_submitted_round_is_scored(client, monkeypatch):
    from app.services import scoring_service
    sid = _submitted_round1(client, monkeypatch)
    calls = _counting_scorer(monkeypatch)
    scoring_service.score_submission_in_background(sid)
    assert calls == [sid] and _status(sid) == "scored"


def test_scored_round_is_not_scored_again(client, monkeypatch):
    from app.services import scoring_service
    sid = _submitted_round1(client, monkeypatch)
    _set_status(sid, "scored")
    calls = _counting_scorer(monkeypatch)
    scoring_service.score_submission_in_background(sid)
    assert calls == []


def test_round_still_in_progress_is_not_scored(client, monkeypatch):
    from app.services import scoring_service
    sid = _submitted_round1(client, monkeypatch)
    _set_status(sid, "in_progress")
    calls = _counting_scorer(monkeypatch)
    scoring_service.score_submission_in_background(sid)
    assert calls == [] and _status(sid) == "in_progress"


def test_second_call_while_scoring_runs_does_nothing(client, monkeypatch):
    # Two requests closing the same expired round at the same moment each
    # schedule scoring; the second arrives while the first is still running.
    from app.services import scoring_service
    sid = _submitted_round1(client, monkeypatch)
    calls = _counting_scorer(monkeypatch, on_call=scoring_service.score_submission_in_background)
    scoring_service.score_submission_in_background(sid)
    assert calls == [sid]


def test_failed_scoring_can_still_be_retried(client, monkeypatch):
    from app.services import scoring_service
    sid = _submitted_round1(client, monkeypatch)
    _set_status(sid, "scoring_failed")
    calls = _counting_scorer(monkeypatch)
    scoring_service.score_submission_in_background(sid)
    assert calls == [sid] and _status(sid) == "scored"


def test_a_round_can_be_scored_again_after_a_run_finishes(client, monkeypatch):
    # The "already running" mark is released when a run ends, even a failed one.
    from app.services import scoring_service
    sid = _submitted_round1(client, monkeypatch)

    def failing(db, submission):
        raise RuntimeError("model unavailable")
    monkeypatch.setitem(scoring_service._SCORERS, 1, failing)
    scoring_service.score_submission_in_background(sid)
    assert _status(sid) == "scoring_failed"
    calls = _counting_scorer(monkeypatch)
    scoring_service.score_submission_in_background(sid)
    assert calls == [sid]
