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
    _login, _auth, _publish_scenario,
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
    their own deadline had passed."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_scenario(client, hr_token, monkeypatch)  # 30-minute default limit
    _stub_round1_scoring(monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))

    # Blocked while genuinely still within the window.
    res = client.patch(f"/hr/scenarios/{published['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 409

    # Candidate vanishes - never submits, never calls /expire (simulating
    # a closed tab/crash/logout/network-loss - nothing the server can
    # tell apart from each other). Their deadline passes.
    _set_started_at(1, minutes_ago=31)

    # No longer blocked - the abandoned round is lazily closed out as
    # part of this same request.
    res = client.patch(f"/hr/scenarios/{published['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["time_limit_minutes"] == 45

    # The submission itself is now a real, scored, closed round - not
    # still sitting at in_progress.
    report = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    c1 = next(c for c in report if c["email"] == CANDIDATE1_EMAIL)
    round1 = next(r for r in c1["rounds"] if r["round_number"] == 1)
    assert round1["status"] == "scored"
    assert round1["final_score"] == 0
    assert round1["auto_closed_reason"] == "Time limit reached without a manual submit"


def test_only_the_genuinely_expired_candidate_gets_closed_others_still_block(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_scenario(client, hr_token, monkeypatch)
    _stub_round1_scoring(monkeypatch)
    cand1_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    cand2_token = _login(client, CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand1_token))
    client.post("/candidate/round/1/start", cookies=_auth(cand2_token))
    _set_started_at(1, minutes_ago=31, email=CANDIDATE1_EMAIL)  # only candidate1 has expired

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
    published = _publish_scenario(client, hr_token, monkeypatch)
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

    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="Debug scenario")
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
