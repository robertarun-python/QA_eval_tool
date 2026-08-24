"""
GET /hr/candidates - the computed "result" field (see hr.py's
_build_candidate_summary) and Submission.submitted_at. Constructs
Submission/Score rows directly via SessionLocal rather than driving each
round's full HTTP flow - what's under test here is the AGGREGATION logic
(when does "selected"/"not_selected" apply, vs "in_progress"), not each
round's own submit/scoring plumbing, which is already covered elsewhere
(test_round1.py etc.).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from datetime import datetime, timedelta

from .conftest import HR_EMAIL, HR_PASSWORD, CANDIDATE3_EMAIL, _login, _auth


def _make_scenario(db, round_number, band="7+"):
    from app.models import Scenario, ScenarioStatus, ExperienceBand, User, Role

    hr = db.query(User).filter(User.role == Role.hr).first()
    scenario = Scenario(
        round_number=round_number, title=f"R{round_number}", description="desc",
        experience_band=ExperienceBand(band), created_by=hr.id,
        status=ScenarioStatus.published, is_live=True, time_limit_minutes=30,
        reference_json=[] if round_number in (1, 2) else {"test_cases": [], "expected_approach": ""},
    )
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return scenario


def _make_scored_submission(db, candidate, scenario, round_number, final_score, status="scored"):
    from app.models import Submission, Score, RoundStatus

    now = datetime.utcnow()
    submission = Submission(
        user_id=candidate.id, scenario_id=scenario.id, round_number=round_number,
        status=RoundStatus(status), started_at=now - timedelta(minutes=10),
        submitted_at=now if status != "in_progress" else None,
        content={},
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    if final_score is not None:
        db.add(Score(submission_id=submission.id, final_score=final_score, coverage_score=final_score))
        db.commit()
    return submission


def _get_candidate3_summary(client, hr_token):
    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    return next(c for c in res.json() if c["email"] == CANDIDATE3_EMAIL)


def test_result_is_in_progress_with_only_some_rounds_scored(client, monkeypatch):
    from app.database import SessionLocal
    from app.models import User, Role

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    db = SessionLocal()
    try:
        candidate = db.query(User).filter(User.email == CANDIDATE3_EMAIL, User.role == Role.candidate).first()
        s1 = _make_scenario(db, 1)
        _make_scored_submission(db, candidate, s1, 1, final_score=90)
        # Rounds 2-4 never started.
    finally:
        db.close()

    summary = _get_candidate3_summary(client, hr_token)
    assert summary["result"] == "in_progress"


def test_result_is_selected_when_aggregate_clears_the_bar(client, monkeypatch):
    from app.database import SessionLocal
    from app.models import User, Role

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    # Default final_passing_score is 280/400 (70 each) - four rounds at
    # 90 each comfortably clears it.
    db = SessionLocal()
    try:
        candidate = db.query(User).filter(User.email == CANDIDATE3_EMAIL, User.role == Role.candidate).first()
        for round_number in (1, 2, 3, 4):
            scenario = _make_scenario(db, round_number)
            _make_scored_submission(db, candidate, scenario, round_number, final_score=90)
    finally:
        db.close()

    summary = _get_candidate3_summary(client, hr_token)
    assert summary["result"] == "selected"
    assert summary["aggregate_score"] == 360


def test_result_is_not_selected_when_aggregate_misses_the_bar(client, monkeypatch):
    from app.database import SessionLocal
    from app.models import User, Role

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    db = SessionLocal()
    try:
        candidate = db.query(User).filter(User.email == CANDIDATE3_EMAIL, User.role == Role.candidate).first()
        for round_number in (1, 2, 3, 4):
            scenario = _make_scenario(db, round_number)
            _make_scored_submission(db, candidate, scenario, round_number, final_score=40)
    finally:
        db.close()

    summary = _get_candidate3_summary(client, hr_token)
    assert summary["result"] == "not_selected"
    assert summary["aggregate_score"] == 160


def test_result_stays_in_progress_when_one_round_is_scoring_failed(client, monkeypatch):
    """Even with 3 strong scores, a round stuck at scoring_failed must
    never let a "selected" verdict slip through - there's no real
    final_score for that round yet."""
    from app.database import SessionLocal
    from app.models import User, Role

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    db = SessionLocal()
    try:
        candidate = db.query(User).filter(User.email == CANDIDATE3_EMAIL, User.role == Role.candidate).first()
        for round_number in (1, 2, 3):
            scenario = _make_scenario(db, round_number)
            _make_scored_submission(db, candidate, scenario, round_number, final_score=95)
        scenario4 = _make_scenario(db, 4)
        _make_scored_submission(db, candidate, scenario4, 4, final_score=None, status="scoring_failed")
    finally:
        db.close()

    summary = _get_candidate3_summary(client, hr_token)
    assert summary["result"] == "in_progress"


def test_submitted_at_is_set_on_a_real_round_submit(client, monkeypatch):
    from app.services import llm_service
    from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, FAKE_REFERENCE, _publish_scenario

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1")
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    before = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    row_before = next(c for c in before if c["email"] == CANDIDATE1_EMAIL)
    assert row_before["rounds"][0]["submitted_at"] is None

    res = client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    assert res.status_code == 201

    after = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    row_after = next(c for c in after if c["email"] == CANDIDATE1_EMAIL)
    assert row_after["rounds"][0]["submitted_at"] is not None
