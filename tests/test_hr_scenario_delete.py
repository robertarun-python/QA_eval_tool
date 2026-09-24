"""
DELETE /hr/scenarios/{id} - HR deleting a scenario from the Published or
Drafts list (see app.js's renderPublishedTable/renderDraftTable +
deleteScenarioFromList). Works for any round's scenarios; only Round 4
has no draft/published scenario list at all (its scenario is a single
always-live per-band settings panel - see routers/hr.py's
_generate_reference), so it isn't covered here.

Two safety gates, checked regardless of draft/published status - see
hr.py's delete_scenario docstring for the reasoning:
  1. Never the currently live scenario.
  2. Never one with any submission (including archived/test ones)
     pointing at it - real candidate history must never be orphaned.
"""
from .conftest import HR_EMAIL, HR_PASSWORD, _login, _auth, _publish_scenario


def test_hr_can_delete_a_draft_scenario(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: [])
    draft = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "Throwaway draft", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    ).json()

    res = client.delete(f"/hr/scenarios/{draft['id']}", cookies=_auth(hr_token))
    assert res.status_code == 204

    assert client.get(f"/hr/scenarios/{draft['id']}", cookies=_auth(hr_token)).status_code == 404


def test_hr_can_delete_a_published_non_live_scenario_with_no_submissions(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    # First publish auto-goes live (nothing else live yet for this band) -
    # a second publish for the same round+band stays non-live, which is
    # exactly the "published but never actually used" case this feature
    # is for (e.g. a duplicate scenario title created by mistake).
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Live one")
    duplicate = _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Duplicate, never made live")

    fetched = client.get(f"/hr/scenarios/{duplicate['id']}", cookies=_auth(hr_token)).json()
    assert fetched["status"] == "published"
    assert fetched["is_live"] is False

    res = client.delete(f"/hr/scenarios/{duplicate['id']}", cookies=_auth(hr_token))
    assert res.status_code == 204
    assert client.get(f"/hr/scenarios/{duplicate['id']}", cookies=_auth(hr_token)).status_code == 404


def test_hr_cannot_delete_the_live_scenario(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    live = _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="Currently live")

    res = client.delete(f"/hr/scenarios/{live['id']}", cookies=_auth(hr_token))
    assert res.status_code == 400
    assert "live" in res.json()["detail"].lower()

    # Untouched.
    assert client.get(f"/hr/scenarios/{live['id']}", cookies=_auth(hr_token)).status_code == 200


def test_hr_cannot_delete_a_scenario_with_a_submission_even_if_archived(client, monkeypatch):

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="Live one")
    other = _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="Has an old submission")

    # Directly create a submission against `other` via the DB, the same
    # way a stray dev-test attempt or a reset-and-archived candidate
    # cycle would leave one behind - the point being that even an
    # archived, never-scored submission must still block deletion.
    from app.database import SessionLocal
    from app.models import Submission, RoundStatus, User, Role
    from datetime import datetime

    db = SessionLocal()
    try:
        candidate = db.query(User).filter(User.role == Role.candidate).first()
        db.add(Submission(
            user_id=candidate.id, scenario_id=other["id"], round_number=1,
            status=RoundStatus.in_progress, started_at=datetime.utcnow(), archived=True,
        ))
        db.commit()
    finally:
        db.close()

    res = client.delete(f"/hr/scenarios/{other['id']}", cookies=_auth(hr_token))
    assert res.status_code == 400
    assert "submission" in res.json()["detail"].lower()
