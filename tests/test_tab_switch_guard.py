"""
Anti-cheating tab-switch/focus-loss guard (see app.js's tabGuard* functions
and routers/candidate.py's POST /round/{n}/tab-switch and
POST /round/{n}/abandon). The browser side can't be exercised here - this
covers the two endpoints it drives: logging a violation, and the "Exit
test" terminal action, plus how both show up on HR's side.
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    _login, _auth, _publish_scenario,
)


def _start_round1(client, cand_token):
    return client.post("/candidate/round/1/start", headers=_auth(cand_token))


def test_tab_switch_is_logged_on_the_in_progress_submission(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _start_round1(client, cand_token)

    res = client.post("/candidate/round/1/tab-switch", headers=_auth(cand_token))
    assert res.status_code == 204
    res = client.post("/candidate/round/1/tab-switch", headers=_auth(cand_token))
    assert res.status_code == 204

    candidates = client.get("/hr/candidates", headers=_auth(hr_token)).json()
    c1 = next(c for c in candidates if c["email"] == CANDIDATE1_EMAIL)
    round1 = next(r for r in c1["rounds"] if r["round_number"] == 1)
    assert round1["tab_switch_count"] == 2

    report = client.get(f"/hr/candidates/{c1['id']}/report", headers=_auth(hr_token)).json()
    sub1 = next(s for s in report if s["round_number"] == 1)
    assert sub1["tab_switch_count"] == 2
    assert len(sub1["tab_switch_events_json"]) == 2


def test_tab_switch_is_a_silent_no_op_once_the_round_is_no_longer_in_progress(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _start_round1(client, cand_token)
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        headers=_auth(cand_token),
    )

    # The round is submitted now - a late-arriving tab-switch beacon
    # (e.g. the request was already in flight when the candidate's own
    # submit landed first) must not error out or attach to anything.
    res = client.post("/candidate/round/1/tab-switch", headers=_auth(cand_token))
    assert res.status_code == 204


def test_abandon_ends_the_round_without_scoring_it(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _start_round1(client, cand_token)

    res = client.post("/candidate/round/1/abandon", headers=_auth(cand_token))
    assert res.status_code == 200
    assert res.json()["status"] == "abandoned"

    candidates = client.get("/hr/candidates", headers=_auth(hr_token)).json()
    c1 = next(c for c in candidates if c["email"] == CANDIDATE1_EMAIL)
    round1 = next(r for r in c1["rounds"] if r["round_number"] == 1)
    assert round1["status"] == "abandoned"
    assert round1["final_score"] is None
    assert c1["aggregate_score"] is None  # an abandoned round contributes nothing


def test_abandoned_round_stays_locked_and_blocks_the_next_round(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="Checkout flow")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="Debug scenario")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _start_round1(client, cand_token)
    client.post("/candidate/round/1/abandon", headers=_auth(cand_token))

    # Can't restart or resubmit the abandoned round...
    res = client.post("/candidate/round/1/start", headers=_auth(cand_token))
    assert res.status_code == 201  # idempotent GET-like return of the existing (abandoned) row
    assert res.json()["status"] == "abandoned"
    res = client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        headers=_auth(cand_token),
    )
    assert res.status_code == 400

    # ...and round 2 never unlocks, since _max_completed_round only
    # advances on submitted/scored.
    res = client.get("/candidate/round/2", headers=_auth(cand_token))
    assert res.status_code == 403


def test_abandon_requires_an_in_progress_submission(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)

    # Never started.
    res = client.post("/candidate/round/1/abandon", headers=_auth(cand_token))
    assert res.status_code == 400

    _start_round1(client, cand_token)
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        headers=_auth(cand_token),
    )
    # Already submitted.
    res = client.post("/candidate/round/1/abandon", headers=_auth(cand_token))
    assert res.status_code == 400


def test_tab_switch_and_abandon_are_candidate_only(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    assert client.post("/candidate/round/1/tab-switch", headers=_auth(hr_token)).status_code == 403
    assert client.post("/candidate/round/1/abandon", headers=_auth(hr_token)).status_code == 403


def test_invalid_round_number_is_rejected(client):
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert client.post("/candidate/round/9/tab-switch", headers=_auth(cand_token)).status_code == 400
    assert client.post("/candidate/round/9/abandon", headers=_auth(cand_token)).status_code == 400
