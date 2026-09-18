"""
The overall assessment-window close-out (see scoring_service.
close_expired_assessment_windows, AppSettings.assessment_window_days) -
the one-level-earlier counterpart to test_lazy_expiry.py's in-progress
timeout tests. A round that was never even started has no started_at,
so nothing about per-round timeouts can ever resolve it; without this,
"not_started" has no maximum residency at all.

Anchored to round 1's own Submission.started_at whenever the candidate
has actually begun - universal, every candidate gets one the instant
they click Start, regardless of how their account was created - falling
back to CandidateAppearance.exam_date only for the genuine no-show case
(scheduled, never started anything at all). This started out anchored to
exam_date only, which meant any candidate without a CandidateAppearance
(every seeded/demo account) got no enforcement whatsoever, silently -
see test_a_seeded_account_gets_enforced_once_it_actually_starts_a_round
below for the regression test that gap needed.
"""
from datetime import datetime, timedelta

from .conftest import HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, _login, _auth, _publish_scenario


def _txt_file(text: str):
    return {"file": ("candidates.txt", text.encode(), "text/plain")}


def _date(days_from_now: int = 0) -> str:
    return (datetime.utcnow() + timedelta(days=days_from_now)).strftime("%Y-%m-%d")


def _set_exam_date(email: str, days_ago: int):
    import app.database as database_module
    from app.models import CandidateAppearance, User

    db = database_module.SessionLocal()
    user = db.query(User).filter(User.email == email).one()
    appearance = next(a for a in user.appearances if a.is_current)
    appearance.exam_date = datetime.utcnow() - timedelta(days=days_ago)
    db.commit()
    db.close()


def _set_round1_started_at(email: str, days_ago: int):
    import app.database as database_module
    from app.models import Submission, User

    db = database_module.SessionLocal()
    user = db.query(User).filter(User.email == email).one()
    submission = db.query(Submission).filter(
        Submission.user_id == user.id, Submission.round_number == 1, Submission.archived.is_(False),
    ).one()
    submission.started_at = datetime.utcnow() - timedelta(days=days_ago)
    db.commit()
    db.close()


def test_a_never_started_round_gets_closed_out_once_round1_started_and_window_passed(client, monkeypatch):
    """The common case: candidate genuinely began (round 1 has a real
    started_at), then stalled before round 2. Anchored to THAT moment,
    not exam_date - exam_date is set far in the future here specifically
    to prove it's irrelevant once the candidate has actually started."""
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1")
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="R2")
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 90, "misses": [], "final_score": 90, "feedback_text": "great"},
    )
    monkeypatch.setattr(
        llm_service, "score_round2_submission",
        lambda **kwargs: {"coverage_score": 0, "misses": ["Nothing submitted"], "final_score": 0, "feedback_text": "no attempt"},
    )

    # Exam date is far in the future - irrelevant once round 1 has a real started_at.
    upload_body = client.post("/hr/candidates/upload", files=_txt_file(f"email,exam_date\njohn.doe@acme.com,{_date(30)}\n"), cookies=_auth(hr_token)).json()
    john_password = next(r["password"] for r in upload_body["rows"] if r["username"] == "john.doe")

    cand_token = _login(client, "john.doe", john_password)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )

    candidate_id = next(c for c in client.get("/hr/candidates", cookies=_auth(hr_token)).json() if c["email"] == "john.doe@acme.com")["id"]

    # Still within the default 1-day window - untouched.
    report = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    row = next(c for c in report if c["id"] == candidate_id)
    assert next(r for r in row["rounds"] if r["round_number"] == 2)["status"] == "not_started"

    # Round 1 "started" two days ago - the window (anchored there, not
    # exam_date) has now passed.
    _set_round1_started_at("john.doe@acme.com", days_ago=2)
    client.get("/hr/candidates", cookies=_auth(hr_token))  # closes it out, schedules scoring
    report = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    row = next(c for c in report if c["id"] == candidate_id)
    round2 = next(r for r in row["rounds"] if r["round_number"] == 4)  # debugging is slot 4 since the swap
    assert round2["status"] == "scored"
    assert round2["final_score"] == 0
    assert round2["auto_closed_reason"] == "Assessment window closed before this round was ever started"

    # Round 1 (already completed for real) is untouched by any of this.
    round1 = next(r for r in row["rounds"] if r["round_number"] == 1)
    assert round1["final_score"] == 90
    assert round1["auto_closed_reason"] is None


def test_a_seeded_account_gets_enforced_once_it_actually_starts_a_round(client, monkeypatch):
    """The exact gap this was missing: a seeded/demo account has no
    CandidateAppearance at all (see models.CandidateAppearance - only the
    bulk-upload flow creates one), so the old exam_date-only anchor gave
    it zero enforcement, silently, forever. Once it actually starts a
    round, that round's own started_at is anchor enough - no
    CandidateAppearance required."""
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="0-7")
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="R2", band="0-7")
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 90, "misses": [], "final_score": 90, "feedback_text": "great"},
    )
    monkeypatch.setattr(
        llm_service, "score_round2_submission",
        lambda **kwargs: {"coverage_score": 0, "misses": ["Nothing submitted"], "final_score": 0, "feedback_text": "no attempt"},
    )

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )

    _set_round1_started_at(CANDIDATE1_EMAIL, days_ago=2)
    hr2 = _login(client, HR_EMAIL, HR_PASSWORD)
    client.get("/hr/candidates", cookies=_auth(hr2))
    report = client.get("/hr/candidates", cookies=_auth(hr2)).json()
    row = next(c for c in report if c["email"] == CANDIDATE1_EMAIL)
    round2 = next(r for r in row["rounds"] if r["round_number"] == 4)  # debugging is slot 4 since the swap
    assert round2["status"] == "scored"
    assert round2["auto_closed_reason"] == "Assessment window closed before this round was ever started"


def test_a_true_no_show_is_closed_via_the_exam_date_fallback(client, monkeypatch):
    """Scheduled but never started anything at all - round 1 itself has
    no started_at to anchor to, so this is the one case that still needs
    exam_date. Every round (including round 1) gets closed out."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1")

    from app.services import llm_service
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 0, "misses": ["Nothing submitted"], "final_score": 0, "feedback_text": "no attempt"},
    )

    client.post("/hr/candidates/upload", files=_txt_file(f"email,exam_date\nno.show@acme.com,{_date()}\n"), cookies=_auth(hr_token))
    candidate_id = next(c for c in client.get("/hr/candidates", cookies=_auth(hr_token)).json() if c["email"] == "no.show@acme.com")["id"]

    report = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    row = next(c for c in report if c["id"] == candidate_id)
    assert next(r for r in row["rounds"] if r["round_number"] == 1)["status"] == "not_started"

    _set_exam_date("no.show@acme.com", days_ago=2)
    client.get("/hr/candidates", cookies=_auth(hr_token))
    report = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    row = next(c for c in report if c["id"] == candidate_id)
    round1 = next(r for r in row["rounds"] if r["round_number"] == 1)
    assert round1["status"] == "scored"
    assert round1["auto_closed_reason"] == "Assessment window closed before this round was ever started"


def test_untouched_seeded_account_is_left_alone(client):
    """No CandidateAppearance and no round ever started - nothing to
    anchor a deadline to, and nothing misleading about it either: every
    round genuinely just hasn't happened yet. Distinct from the "started,
    then stalled" case above."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    report = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    row = next(c for c in report if c["email"] == CANDIDATE1_EMAIL)
    assert all(r["status"] == "not_started" for r in row["rounds"])


def test_a_round_with_no_live_scenario_stays_not_started_even_past_the_window(client, monkeypatch):
    """Nothing to score against - closing this out would mean inventing a
    scenario_id that doesn't exist. Left alone; HR would need to publish
    something for this round+band before it could ever resolve."""
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1")
    # Round 2 deliberately never published.
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 90, "misses": [], "final_score": 90, "feedback_text": "great"},
    )

    upload_body = client.post("/hr/candidates/upload", files=_txt_file(f"email,exam_date\njane.doe@acme.com,{_date()}\n"), cookies=_auth(hr_token)).json()
    jane_password = next(r["password"] for r in upload_body["rows"] if r["username"] == "jane.doe")
    cand_token = _login(client, "jane.doe", jane_password)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )

    _set_round1_started_at("jane.doe@acme.com", days_ago=2)
    report = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    row = next(c for c in report if c["email"] == "jane.doe@acme.com")
    assert next(r for r in row["rounds"] if r["round_number"] == 2)["status"] == "not_started"
