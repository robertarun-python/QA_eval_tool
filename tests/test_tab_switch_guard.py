"""
Anti-cheating tab-switch/focus-loss logging (see app.js's passive
tabSwitch* functions and routers/candidate.py's POST
/round/{n}/tab-switch). Purely passive, same pattern real assessment
platforms use - log the event, surface it to HR, never interrupt or gate
the candidate's round. The browser side can't be exercised here - this
covers the one endpoint it drives.
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

    # Doesn't gate anything - the round is still in progress and normally
    # submittable after being logged against twice.
    res = client.get("/candidate/round/1", headers=_auth(cand_token))
    assert res.json()["submission"]["status"] == "in_progress"

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


def test_tab_switch_is_candidate_only(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    assert client.post("/candidate/round/1/tab-switch", headers=_auth(hr_token)).status_code == 403


def test_invalid_round_number_is_rejected(client):
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert client.post("/candidate/round/9/tab-switch", headers=_auth(cand_token)).status_code == 400
