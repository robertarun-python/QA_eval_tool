"""
The server-side counterpart to POST /round/{n}/expire (see
test_round_expiry.py) - that endpoint only fires because the candidate's
OWN browser hit zero on the timer and called it. If the candidate is
simply gone - closed the tab, crashed, logged out, network loss, anything
- nothing ever calls it, and the submission would sit at in_progress
forever with no way for the round (or HR's time-limit edits for that
whole band - see the real bug report this came from) to ever unblock.

scoring_service.close_expired_submissions is the fix: lazily finalize any
in_progress submission whose own deadline has already passed, the moment
anything server-side next looks at it - HR's time-limit block, HR's
candidate views, the candidate's own round-gating checks if they ever
come back. Same principle as /expire: whatever content exists (possibly
none) becomes the final submission, scored normally - "didn't attempt
it" is a scoring outcome, not a special case. Distinguished from a normal
submit via Submission.auto_closed_reason, HR-visible only.

Deliberately does NOT fire the moment a candidate merely logs out - the
deadline is the one consistent rule for every candidate regardless of
*how* they became unreachable (see the design discussion this came
from): logging out just ends their session, they can still come back and
finish within their original time.
"""
from datetime import datetime, timedelta

from .conftest import (
    HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD,
    _login, _auth, _publish_scenario, _publish_round4_scenario, _complete_rounds_1_through_3,
)


def _set_started_at(round_number, minutes_ago, email=CANDIDATE1_EMAIL):
    import app.database as database_module
    from app.models import Submission, User

    db = database_module.SessionLocal()
    user = db.query(User).filter(User.email == email).one()
    submission = db.query(Submission).filter(Submission.round_number == round_number, Submission.user_id == user.id).one()
    submission.started_at = datetime.utcnow() - timedelta(minutes=minutes_ago)
    db.commit()
    db.close()


def _stub_round1_scoring(monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {
            "coverage_score": 0, "misses": ["Nothing submitted"], "final_score": 0,
            "feedback_text": "No test cases were written.",
            "concept_coverage": [],
            "_provenance": {"model": "claude-sonnet-4-5", "prompt_file": "round1_scoring.txt", "prompt_hash": "abc123"},
        },
    )


def test_expired_in_progress_submission_no_longer_blocks_hr_time_limit_edit(client, monkeypatch):
    """The exact bug report: a candidate who abandoned a round (never
    called /expire themselves - nothing distinguishes that from a
    genuine logout/crash/network-loss at the DB level) kept blocking
    HR's time-limit edits for the whole band forever, even long after
    their own deadline had passed.

    Round 1 itself can't demonstrate this anymore - its time limit is
    now locked the moment it's published (rounds 1-3 are draft-only, see
    update_scenario_time_limit), so this exercises the same in-progress
    guard through round 4 instead, the one round where it's still
    reachable via this endpoint."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_round4_scenario(client, hr_token, monkeypatch)  # 30-minute default limit
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_rounds_1_through_3(client, hr_token, cand_token, monkeypatch, seed_upto=1)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    # Blocked while genuinely still within the window.
    res = client.patch(f"/hr/scenarios/{published['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 409

    # Candidate vanishes - never submits, never calls /expire (simulating
    # a closed tab/crash/logout/network-loss - nothing the server can
    # tell apart from each other). Their deadline passes.
    _set_started_at(2, minutes_ago=31)

    # No longer blocked - the abandoned round is lazily closed out as
    # part of this same request.
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "score_round4_conversation", lambda **kwargs: {
        "coverage_score": 0, "misses": [], "final_score": 0, "feedback_text": "Nothing submitted.",
    })
    res = client.patch(f"/hr/scenarios/{published['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["time_limit_minutes"] == 45

    # The submission itself is now a real, scored, closed round - not
    # still sitting at in_progress.
    report = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    c1 = next(c for c in report if c["email"] == CANDIDATE1_EMAIL)
    round4 = next(r for r in c1["rounds"] if r["round_number"] == 2)  # automation is slot 2 since the swap
    assert round4["status"] == "scored"
    assert round4["final_score"] == 0
    assert round4["auto_closed_reason"] == "Time limit reached without a manual submit"


def test_only_the_genuinely_expired_candidate_gets_closed_others_still_block(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_round4_scenario(client, hr_token, monkeypatch)
    cand1_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    cand2_token = _login(client, CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD)
    _complete_rounds_1_through_3(client, hr_token, cand1_token, monkeypatch, seed_upto=1)
    _complete_rounds_1_through_3(client, hr_token, cand2_token, monkeypatch, email=CANDIDATE2_EMAIL, seed_upto=1)
    client.post("/candidate/round/2/start", cookies=_auth(cand1_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand2_token))
    _set_started_at(2, minutes_ago=31, email=CANDIDATE1_EMAIL)  # only candidate1 has expired

    res = client.patch(f"/hr/scenarios/{published['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 409
    # candidate1 was lazily closed out and no longer named; candidate2 is
    # still genuinely mid-round and still blocks.
    assert CANDIDATE1_EMAIL not in res.json()["detail"]
    assert CANDIDATE2_EMAIL in res.json()["detail"]


def test_candidate_returning_after_the_deadline_sees_the_round_already_closed(client, monkeypatch):
    """If the candidate DOES come back within their original window,
    nothing closes early - they can still finish normally. Only once the
    deadline has actually passed does their own next request see it as
    closed, same as HR's."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    _stub_round1_scoring(monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))

    # Still within the window - untouched, resumable.
    state = client.get("/candidate/round/1", cookies=_auth(cand_token)).json()
    assert state["submission"]["status"] == "in_progress"

    _set_started_at(1, minutes_ago=31)

    # Comes back after the deadline - the round is already closed, and
    # round 2 is reachable without ever having called /submit or /expire.
    # (This same request's response reflects "submitted" rather than
    # "scored" - close_expired_submissions scores via its own separate
    # DB session, same as score_submission_in_background always has, so
    # this request's own session doesn't observe that write. A genuinely
    # separate follow-up request does - see the fresh GET right below.)
    state = client.get("/candidate/round/1", cookies=_auth(cand_token)).json()
    assert state["submission"]["status"] == "submitted"

    state = client.get("/candidate/round/1", cookies=_auth(cand_token)).json()
    assert state["submission"]["status"] == "scored"

    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    res = client.get("/candidate/round/2", cookies=_auth(cand_token))
    assert res.status_code == 200


def test_a_normal_submit_has_no_auto_closed_reason(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    _stub_round1_scoring(monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )

    report = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    c1 = next(c for c in report if c["email"] == CANDIDATE1_EMAIL)
    round1 = next(r for r in c1["rounds"] if r["round_number"] == 1)
    assert round1["auto_closed_reason"] is None


def test_close_expired_submissions_schedules_scoring_as_a_background_task_not_inline(client, monkeypatch):
    """close_expired_submissions must schedule each newly-closed
    submission's scoring via BackgroundTasks, same as every submit
    endpoint already does (submit_round/submit_round2/round4_submit/
    expire_round) - not call score_submission_in_background directly.
    Direct calls block whichever request happened to be the one that
    lazily closed the submission (e.g. HR's /candidates dashboard, which
    can lazily close several abandoned submissions in a single request)
    on a real LLM call before the response can be sent."""
    from fastapi import BackgroundTasks
    import app.database as database_module
    from app.models import Submission, User
    from app.services import scoring_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    _set_started_at(1, minutes_ago=31)

    called = []
    monkeypatch.setattr(scoring_service, "score_submission_in_background", lambda sid: called.append(sid))

    db = database_module.SessionLocal()
    user = db.query(User).filter(User.email == CANDIDATE1_EMAIL).one()
    submission = db.query(Submission).filter(Submission.round_number == 1, Submission.user_id == user.id).one()

    background_tasks = BackgroundTasks()
    scoring_service.close_expired_submissions(db, [submission], background_tasks)

    assert called == [], "scoring must not run inline - it must be scheduled for after the response is sent"
    assert len(background_tasks.tasks) == 1

    db.close()


def test_expired_round4_in_progress_submission_no_longer_blocks_round4_config_edit(client, monkeypatch):
    """Same bug class as the time-limit block above, but for round 4's
    own in-progress guard (_require_round4_not_in_progress) - it must
    also lazily close an abandoned-past-deadline submission before
    counting, or an abandoned round 4 candidate blocks HR from ever
    editing that scenario's config again.

    Round 3 has to be completed too, not just 1/2, before round 4/start
    is reachable - ROUND_SEQUENCE is (1, 2, 3, 4) now that round 3
    (AI-prompted coding) is a real round again."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_round4_scenario(client, hr_token, monkeypatch)
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "score_round4_conversation", lambda **kwargs: {
        "coverage_score": 0, "misses": [], "final_score": 0, "feedback_text": "Nothing submitted.",
    })
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_rounds_1_through_3(client, hr_token, cand_token, monkeypatch, seed_upto=1)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    res = client.patch(
        f"/hr/scenarios/{published['id']}/round4-config", json={"assistance_pct": 80}, cookies=_auth(hr_token),
    )
    assert res.status_code == 409

    _set_started_at(2, minutes_ago=31)

    res = client.patch(
        f"/hr/scenarios/{published['id']}/round4-config", json={"assistance_pct": 80}, cookies=_auth(hr_token),
    )
    assert res.status_code == 200
    assert res.json()["config_json"]["assistance_pct"] == 80


def test_expired_in_progress_submission_shows_correctly_in_appearance_report_not_stale(client, monkeypatch):
    """appearance_report (HR's "Past appearances" drill-down) must lazily
    close an abandoned-past-deadline submission the same way its sibling
    candidate_report already does - otherwise it can show a stale
    in_progress round for the exact same submission candidate_report
    would already show correctly closed."""
    from datetime import datetime as dt
    import app.database as database_module
    from app.models import CandidateAppearance, Submission, User

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    _stub_round1_scoring(monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    _set_started_at(1, minutes_ago=31)

    db = database_module.SessionLocal()
    user = db.query(User).filter(User.email == CANDIDATE1_EMAIL).one()
    appearance = CandidateAppearance(user_id=user.id, email=user.email, exam_date=dt.utcnow(), is_current=True)
    db.add(appearance)
    db.commit()
    db.refresh(appearance)
    submission = db.query(Submission).filter(Submission.round_number == 1, Submission.user_id == user.id).one()
    submission.appearance_id = appearance.id
    db.commit()
    candidate_id, appearance_id = user.id, appearance.id
    db.close()

    # This same request's response reflects "submitted" rather than
    # "scored" - close_expired_submissions scores via its own separate
    # DB session (see score_submission_in_background), so this request's
    # own session doesn't observe that write. A genuinely separate
    # follow-up request does - see the fresh GET right below, same
    # pattern as test_candidate_returning_after_the_deadline_sees_the_round_already_closed above.
    res = client.get(
        f"/hr/candidates/{candidate_id}/appearances/{appearance_id}/report", cookies=_auth(hr_token),
    )
    assert res.status_code == 200
    round1 = next(r for r in res.json() if r["round_number"] == 1)
    assert round1["status"] == "submitted"
    assert round1["auto_closed_reason"] == "Time limit reached without a manual submit"

    res = client.get(
        f"/hr/candidates/{candidate_id}/appearances/{appearance_id}/report", cookies=_auth(hr_token),
    )
    round1 = next(r for r in res.json() if r["round_number"] == 1)
    assert round1["status"] == "scored"


def test_candidate_summary_reflects_lazily_closed_round_not_stale_in_progress(client, monkeypatch):
    """_gather_candidate_rounds (feeding both the AI candidate summary and
    the PDF export) must lazily close an abandoned-past-deadline
    submission before reading its status, or HR's summary/PDF can
    describe a round as still in_progress when every other HR view
    already shows it correctly closed."""
    import app.database as database_module
    from app.models import User
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    _stub_round1_scoring(monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    _set_started_at(1, minutes_ago=31)

    captured = {}

    def fake_summary(**kwargs):
        captured["rounds"] = kwargs["rounds"]
        return {"rounds": [], "key_observations": [], "verdict": "ok"}

    monkeypatch.setattr(llm_service, "generate_candidate_summary", fake_summary)

    db = database_module.SessionLocal()
    candidate_id = db.query(User).filter(User.email == CANDIDATE1_EMAIL).one().id
    db.close()

    # As in the appearance-report test above: this same request's own db
    # session doesn't observe the background scoring session's commit, so
    # it sees "submitted" here - a fresh follow-up request sees "scored".
    res = client.post(f"/hr/candidates/{candidate_id}/summary", cookies=_auth(hr_token))
    assert res.status_code == 200
    round1 = next(r for r in captured["rounds"] if r["round_number"] == 1)
    assert round1["status"] == "submitted"

    res = client.post(f"/hr/candidates/{candidate_id}/summary", cookies=_auth(hr_token))
    assert res.status_code == 200
    round1 = next(r for r in captured["rounds"] if r["round_number"] == 1)
    assert round1["status"] == "scored"
