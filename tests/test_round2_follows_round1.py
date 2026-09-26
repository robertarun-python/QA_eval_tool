"""
A candidate's Round 2 is the practice app built for the Round 1 scenario THEY
answered - never just whichever Round 2 is live - and Round 1 shows only a
cut-down look at it, without the edge cases built from Round 1's answer key.
"""
from datetime import datetime

import pytest

from app import database as database_module
from app.models import RoundStatus, Scenario, Submission, User
from app.services.practice_app import service as practice_app_service

from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, _auth, _login, _publish_round4_scenario
from .test_practice_app_service import _db_scenario, _fake_factory, _hr, _r1


@pytest.fixture
def builds_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(practice_app_service, "BUILDS_DIR", tmp_path)
    return tmp_path


def _approve_practice_app(client, token, round1_id):
    client.post(f"/hr/scenarios/{round1_id}/practice-app", cookies=_auth(token))
    res = client.post(f"/hr/scenarios/{round1_id}/practice-app/approve", cookies=_auth(token))
    assert res.status_code == 200, res.text
    return res.json()["round2_scenario_id"]


def _submitted_round1(email, round1_id):
    db = database_module.SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        db.add(Submission(user_id=user.id, scenario_id=round1_id, round_number=1, status=RoundStatus.submitted,
                          started_at=datetime.utcnow(), submitted_at=datetime.utcnow(),
                          content=[{"title": "Valid login", "steps": "log in", "expected_result": "home page"}]))
        db.commit()
    finally:
        db.close()


def _set(scenario_id, **values):
    db = database_module.SessionLocal()
    try:
        scenario = db.get(Scenario, scenario_id)
        for k, v in values.items():
            setattr(scenario, k, v)
        db.commit()
    finally:
        db.close()


def _round2_scenario(client, cand):
    res = client.get("/candidate/round/2", cookies=_auth(cand))
    assert res.status_code == 200, res.text
    return (res.json()["scenario"] or {}).get("id")


def test_round2_is_the_app_built_for_the_candidates_own_round1(client, monkeypatch, builds_dir):
    token = _hr(client)
    credit = _r1(client, token, monkeypatch, title="Credit Card")  # live
    library = _r1(client, token, monkeypatch, title="Library")
    _fake_factory(monkeypatch)
    credit_r2 = _approve_practice_app(client, token, credit["id"])
    library_r2 = _approve_practice_app(client, token, library["id"])
    _submitted_round1(CANDIDATE1_EMAIL, credit["id"])
    # HR moves on to Library: its practice app goes live with it.
    client.post(f"/hr/scenarios/{library['id']}/move-to-screening", cookies=_auth(token))
    assert _db_scenario(library_r2).is_live and not _db_scenario(credit_r2).is_live

    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert _round2_scenario(client, cand) == credit_r2  # the app for the test cases they wrote
    started = client.post("/candidate/round/2/start", cookies=_auth(cand))
    assert started.status_code == 201, started.text
    assert started.json()["scenario_id"] == credit_r2


def test_round2_is_not_available_when_their_round1_has_no_practice_app(client, monkeypatch, builds_dir):
    token = _hr(client)
    credit = _r1(client, token, monkeypatch, title="Credit Card")
    library = _r1(client, token, monkeypatch, title="Library")
    _fake_factory(monkeypatch)
    _approve_practice_app(client, token, credit["id"])  # live Round 2, built for Credit Card
    _submitted_round1(CANDIDATE1_EMAIL, library["id"])

    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert _round2_scenario(client, cand) is None
    assert client.post("/candidate/round/2/start", cookies=_auth(cand)).status_code == 404


def test_a_round2_not_built_for_any_round1_still_serves_everyone(client, monkeypatch):
    token = _hr(client)
    library = _r1(client, token, monkeypatch, title="Library")
    generic = _publish_round4_scenario(client, token, monkeypatch)
    _submitted_round1(CANDIDATE1_EMAIL, library["id"])
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert _round2_scenario(client, cand) == generic["id"]


def test_a_started_round_stays_on_its_scenario_when_hr_switches(client, monkeypatch):
    token = _hr(client)
    first = _r1(client, token, monkeypatch, title="Credit Card")
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert client.post("/candidate/round/1/start", cookies=_auth(cand)).status_code == 201
    second = _r1(client, token, monkeypatch, title="Library")
    client.post(f"/hr/scenarios/{second['id']}/move-to-screening", cookies=_auth(token))

    state = client.get("/candidate/round/1", cookies=_auth(cand)).json()
    assert state["scenario"]["id"] == first["id"] and state["submission"] is not None  # their work is still there
    again = client.post("/candidate/round/1/start", cookies=_auth(cand))
    assert again.json()["scenario_id"] == first["id"]  # not a fresh round on the new scenario


def test_round1_view_leaves_out_what_is_built_from_the_answer_key(client, monkeypatch, builds_dir):
    token = _hr(client)
    library = _r1(client, token, monkeypatch, title="Library")
    _fake_factory(monkeypatch)
    round2 = _approve_practice_app(client, token, library["id"])
    config = dict(_db_scenario(round2).config_json)
    config.pop("round1_sheet")  # an app approved before the design's account was stored
    _set(round2, config_json=config,
         environment_json={"fields": {"Base URL": "https://library.example.test", "Test account username": "testuser",
                                      "Test account password": "Test@123", "Account at borrowing limit username": "maxuser",
                                      "Account at borrowing limit password": "Max@123", "Unavailable book title": "To Kill a Mockingbird"},
                           "notes": "maxuser has 5 borrowed books."},
         ui_mockup_json={"screens": [{"name": "Book Details", "elements": [
             {"type": "text", "text": "Title: The Great Gatsby"}, {"type": "button", "text": "Borrow"},
             {"type": "text", "text": "No copies available (shown when trying to borrow unavailable book)"}]},
             {"name": "Errors only", "elements": [{"type": "text", "text": "Oops (Shown on a failed login)"}]}]})

    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    state = client.get("/candidate/round/1", cookies=_auth(cand)).json()
    assert state["environment"] == {"fields": {"Base URL": "https://library.example.test", "Test account username": "testuser",
                                               "Test account password": "Test@123"}, "notes": None}
    assert state["ui_mockup"] == {"screens": [{"name": "Book Details", "elements": [
        {"type": "text", "text": "Title: The Great Gatsby"}, {"type": "button", "text": "Borrow"}]}]}


def test_round1_shows_nothing_from_a_round2_built_for_another_scenario(client, monkeypatch, builds_dir):
    token = _hr(client)
    credit = _r1(client, token, monkeypatch, title="Credit Card")
    _fake_factory(monkeypatch)
    _approve_practice_app(client, token, credit["id"])  # live, but for Credit Card
    library = _r1(client, token, monkeypatch, title="Library")
    client.post(f"/hr/scenarios/{library['id']}/move-to-screening", cookies=_auth(token))

    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    state = client.get("/candidate/round/1", cookies=_auth(cand)).json()
    assert state["scenario"]["id"] == library["id"]
    assert state["environment"] is None and state["ui_mockup"] is None


def test_round1_shows_the_designs_main_account_for_an_approved_app(client, monkeypatch, builds_dir):
    """Stored at approval straight from the practice app's design - no guessing from field names."""
    token = _hr(client)
    library = _r1(client, token, monkeypatch, title="Library")
    _fake_factory(monkeypatch)
    _approve_practice_app(client, token, library["id"])
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    state = client.get("/candidate/round/1", cookies=_auth(cand)).json()
    assert state["environment"]["fields"] == {"Test login": "qa.patient.demo@testportal.io", "Password": "Px!7mK@2024Test"}


def test_the_older_sheet_fallback_skips_special_accounts_and_pages():
    """C9: the first field containing "login"/"password" could be a page or the
    locked account - both built from the Round 1 answer key."""
    from app.routers.candidate import _main_login_fields
    fields = {"App URL": "https://hotel.example.test", "Login page": "/signin", "Locked account password": "x",
              "Locked account username": "locked.user", "Test username": "qa", "Test password": "p",
              "Rooms page url note": "see docs"}
    assert _main_login_fields(fields) == {"App URL": "https://hotel.example.test", "Test username": "qa", "Test password": "p"}


def test_hr_is_told_how_many_candidates_are_waiting_for_round2(client, monkeypatch, builds_dir):
    """D7: a candidate who finished a Round 1 with no approved app can't go on -
    the Round 2 card has to say so, or they wait unseen until their window ends."""
    token = _hr(client)
    library = _r1(client, token, monkeypatch, title="Library")
    _submitted_round1(CANDIDATE1_EMAIL, library["id"])
    status = lambda: client.get(f"/hr/scenarios/{library['id']}/practice-app", cookies=_auth(token)).json()  # noqa: E731
    assert status()["waiting_candidates"] == 1
    _fake_factory(monkeypatch)
    _approve_practice_app(client, token, library["id"])
    assert status()["waiting_candidates"] == 0
