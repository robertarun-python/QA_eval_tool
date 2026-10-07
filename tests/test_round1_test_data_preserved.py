"""
Round 1's test data survives every path a row is saved by (production, 2026-10-06): the draft autosave and
the time-up save both went through _sanitize_expired_round1_row, which dropped test_data - a candidate whose
round the timer closed was scored on rows with no test data and marked down for exactly that. The scorer
here is a stub that records what it was given; no AI call is made.
"""
import json

from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, HR_EMAIL, HR_PASSWORD, _auth, _login, _publish_scenario
from .test_round_expiry import _expire_started_at

ROW = {"title": "Pay EMI from SAV-1001", "preconditions": "Logged in as CUST001", "steps": "1. Open EMI payment\n2. Pay Now",
       "test_data": "loan LN-45678; account SAV-1001 (15,000.00); EMI 5,250.00",
       "expected_result": "Payment successful; balance 9,750.00", "priority": "High", "type": "Negative"}


def _round1(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    seen = []
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kw: (seen.append(json.loads(kw["candidate_submission"])), {
        "coverage_score": 10, "misses": [], "final_score": 10, "feedback_text": "ok", "concept_coverage": []})[1])
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand))
    return cand, seen


def _stored_round1():
    import app.database as database_module
    from app.models import Submission
    db = database_module.SessionLocal()
    try:
        sub = db.query(Submission).filter(Submission.round_number == 1).one()
        return sub.status, sub.content
    finally:
        db.close()


def test_autosave_keeps_test_data_and_every_other_field(client, monkeypatch):
    cand, _ = _round1(client, monkeypatch)
    assert client.patch("/candidate/round/1/draft", json={"content": [ROW]}, cookies=_auth(cand)).status_code == 204
    state = client.get("/candidate/round/1", cookies=_auth(cand)).json()
    assert state["submission"]["content"] == [ROW]           # test data, and every other field as typed
    assert state["submission"]["status"] == "in_progress"


def test_the_pages_time_up_save_keeps_test_data_and_scores_it(client, monkeypatch):
    cand, seen = _round1(client, monkeypatch)
    _expire_started_at(1, minutes_ago=31)
    res = client.post("/candidate/round/1/expire", json={"content": [ROW, {"title": "Half-written", "test_data": "amount 0.00"}]},
                      cookies=_auth(cand))
    assert res.status_code == 200
    assert res.json()["content"][0] == ROW
    assert res.json()["content"][1]["test_data"] == "amount 0.00"   # an incomplete row keeps it too
    assert seen and seen[-1][0]["test_data"] == ROW["test_data"]    # the scorer was given it


def test_the_servers_own_time_up_close_scores_the_autosaved_test_data(client, monkeypatch):
    """No /expire from the page (closed tab, lost connection): the round closes on the candidate's next
    request with the last autosave - which must still have the test data in it."""
    cand, seen = _round1(client, monkeypatch)
    client.patch("/candidate/round/1/draft", json={"content": [ROW]}, cookies=_auth(cand))
    _expire_started_at(1, minutes_ago=31)
    client.get("/candidate/round/1", cookies=_auth(cand))
    status, content = _stored_round1()
    assert status.value in ("submitted", "scored")
    assert content == [ROW]
    assert seen and seen[-1] == [ROW]


def test_missing_or_empty_test_data_still_works(client, monkeypatch):
    cand, _ = _round1(client, monkeypatch)
    rows = [{"title": "No test data key", "steps": "1. x", "expected_result": "y"},
            {"title": "Null test data", "steps": "1. x", "expected_result": "y", "test_data": None}]
    client.patch("/candidate/round/1/draft", json={"content": rows}, cookies=_auth(cand))
    saved = client.get("/candidate/round/1", cookies=_auth(cand)).json()["submission"]["content"]
    assert [r["test_data"] for r in saved] == ["", ""]
    assert saved[0] == {"title": "No test data key", "preconditions": "", "steps": "1. x", "test_data": "",
                        "expected_result": "y", "priority": "Medium", "type": "Positive"}


def test_rows_saved_before_the_fix_still_read_and_score(client, monkeypatch):
    """A submission stored without any test_data key (every timed-out row until now) is still readable
    by the candidate and HR, and scores - the scorer is simply given the row as it is."""
    cand, seen = _round1(client, monkeypatch)
    old_row = {"title": "Old row", "preconditions": "", "steps": "1. x", "expected_result": "y", "priority": "Medium", "type": "Positive"}
    import app.database as database_module
    from app.models import Submission
    db = database_module.SessionLocal()
    sub = db.query(Submission).filter(Submission.round_number == 1).one()
    sub.content = [old_row]
    db.commit()
    db.close()
    assert client.get("/candidate/round/1", cookies=_auth(cand)).json()["submission"]["content"] == [old_row]
    _expire_started_at(1, minutes_ago=31)
    client.get("/candidate/round/1", cookies=_auth(cand))            # closes and scores it as stored
    assert seen and seen[-1] == [old_row]
    hr = _auth(_login(client, HR_EMAIL, HR_PASSWORD))
    assert client.get("/hr/candidates", cookies=hr).status_code == 200
