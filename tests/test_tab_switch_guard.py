"""
Anti-cheating tab-switch/fullscreen-exit guard (see app.js's
fullscreenchange handler and routers/candidate.py's POST
/round/{n}/tab-switch). Every exit is logged and surfaced to HR same as
before, but now also a genuine 3-strike gate: the 3rd exit in one round
force-finalizes it right there, scored on whatever's already persisted -
same "final answer" outcome as expire_round, just triggered by strikes
instead of the deadline. The browser side (fullscreen API, the overlay)
can't be exercised here - this covers the one endpoint it drives.
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    _login, _auth, _publish_scenario,
)


def _start_round1(client, cand_token):
    return client.post("/candidate/round/1/start", cookies=_auth(cand_token))


def test_tab_switch_is_logged_on_the_in_progress_submission(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _start_round1(client, cand_token)

    res = client.post("/candidate/round/1/tab-switch", cookies=_auth(cand_token))
    assert res.status_code == 200
    assert res.json() == {"strike_count": 1, "round_ended": False}
    res = client.post("/candidate/round/1/tab-switch", cookies=_auth(cand_token))
    assert res.json() == {"strike_count": 2, "round_ended": False}

    # Two strikes don't gate anything yet - the round is still in
    # progress and normally submittable.
    res = client.get("/candidate/round/1", cookies=_auth(cand_token))
    assert res.json()["submission"]["status"] == "in_progress"

    candidates = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    c1 = next(c for c in candidates if c["email"] == CANDIDATE1_EMAIL)
    round1 = next(r for r in c1["rounds"] if r["round_number"] == 1)
    assert round1["tab_switch_count"] == 2

    report = client.get(f"/hr/candidates/{c1['id']}/report", cookies=_auth(hr_token)).json()
    sub1 = next(s for s in report if s["round_number"] == 1)
    assert sub1["tab_switch_count"] == 2
    assert len(sub1["tab_switch_events_json"]) == 2


def test_third_fullscreen_exit_force_ends_the_round_and_scores_what_was_written(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _start_round1(client, cand_token)
    client.patch(
        "/candidate/round/1/draft",
        json={"content": [{"title": "Partial test case", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )

    client.post("/candidate/round/1/tab-switch", cookies=_auth(cand_token))
    client.post("/candidate/round/1/tab-switch", cookies=_auth(cand_token))
    res = client.post("/candidate/round/1/tab-switch", cookies=_auth(cand_token))
    assert res.json() == {"strike_count": 3, "round_ended": True}

    # The round is over now - finalized with whatever was already
    # autosaved, not blocked on the timer having actually expired.
    state = client.get("/candidate/round/1", cookies=_auth(cand_token)).json()
    assert state["submission"]["status"] == "scored"
    assert state["submission"]["content"][0]["title"] == "Partial test case"

    candidates = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    c1 = next(c for c in candidates if c["email"] == CANDIDATE1_EMAIL)
    round1 = next(r for r in c1["rounds"] if r["round_number"] == 1)
    assert round1["tab_switch_count"] == 3

    # A 4th beacon arriving late (e.g. already in flight) is now a no-op -
    # the round isn't in_progress anymore, so it can't be strike 4.
    res = client.post("/candidate/round/1/tab-switch", cookies=_auth(cand_token))
    assert res.json() == {"strike_count": 0, "round_ended": False}


def test_tab_switch_is_a_silent_no_op_once_the_round_is_no_longer_in_progress(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _start_round1(client, cand_token)
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )

    # The round is submitted now - a late-arriving tab-switch beacon
    # (e.g. the request was already in flight when the candidate's own
    # submit landed first) must not error out or attach to anything.
    res = client.post("/candidate/round/1/tab-switch", cookies=_auth(cand_token))
    assert res.status_code == 200
    assert res.json() == {"strike_count": 0, "round_ended": False}


def test_tab_switch_is_candidate_only(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    assert client.post("/candidate/round/1/tab-switch", cookies=_auth(hr_token)).status_code == 403


def test_invalid_round_number_is_rejected(client):
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert client.post("/candidate/round/9/tab-switch", cookies=_auth(cand_token)).status_code == 400
