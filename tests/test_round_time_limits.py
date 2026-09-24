"""
Per-round time limits set in HR Settings (AppSettings.round1..4_time_limit_minutes,
Scenario.round_time_limit_minutes, Submission.time_limit_minutes_at_start).
HR saw 30 minutes on one Round 1 scenario while candidates got 7 from the
live one, and asked for one setting that decides every round's limit. The
setting applies to every scenario in the round, live ones included, from
the next start; an attempt already running keeps the limit it started with.
No LLM involved.
"""
from datetime import datetime, timedelta

from .conftest import (
    HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    _login, _auth, _publish_scenario,
)

BASE = {"round1_passing_score": 70, "round2_passing_score": 70, "round3_passing_score": 70, "round4_passing_score": 70,
        "final_passing_score": 280, "reapplication_window_months": 6, "assessment_window_days": 1}


def _put_settings(client, hr_token, **limits):
    return client.put("/hr/settings", json={**BASE, **limits}, cookies=_auth(hr_token))


def _set_started_ago(minutes_ago, recorded=...):
    import app.database as database_module
    from app.models import Submission

    db = database_module.SessionLocal()
    submission = db.query(Submission).filter(Submission.round_number == 1).one()
    submission.started_at = datetime.utcnow() - timedelta(minutes=minutes_ago)
    if recorded is not ...:
        submission.time_limit_minutes_at_start = recorded
    db.commit()
    db.close()


def _submit_round1(client, cand_token):
    return client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
                       cookies=_auth(cand_token))


def test_without_a_round_setting_each_scenario_keeps_its_own_limit(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)  # conftest scenarios are 30 minutes
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    scenario = client.get("/candidate/round/1", cookies=_auth(cand_token)).json()["scenario"]
    assert scenario["time_limit_minutes"] == 30 and scenario["round_time_limit_minutes"] == 30
    started = client.post("/candidate/round/1/start", cookies=_auth(cand_token)).json()
    assert started["time_limit_minutes"] == 30


def test_round_setting_applies_to_the_live_scenario_and_is_recorded_at_start(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    assert _put_settings(client, hr_token, round1_time_limit_minutes=20).json()["round1_time_limit_minutes"] == 20

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    scenario = client.get("/candidate/round/1", cookies=_auth(cand_token)).json()["scenario"]
    assert scenario["round_time_limit_minutes"] == 20
    assert scenario["time_limit_minutes"] == 30  # the scenario's own value is untouched
    started = client.post("/candidate/round/1/start", cookies=_auth(cand_token)).json()
    assert started["time_limit_minutes"] == 20


def test_changing_the_setting_mid_round_never_moves_a_running_deadline(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    _put_settings(client, hr_token, round1_time_limit_minutes=20)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))

    _put_settings(client, hr_token, round1_time_limit_minutes=5)  # HR shortens it after the start
    _set_started_ago(10)
    state = client.get("/candidate/round/1", cookies=_auth(cand_token)).json()
    assert state["submission"]["time_limit_minutes"] == 20
    assert _submit_round1(client, cand_token).status_code == 201  # 10 of 20 minutes - still on time


def test_submitting_after_the_recorded_limit_is_rejected(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    _put_settings(client, hr_token, round1_time_limit_minutes=20)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    _set_started_ago(25)  # the scenario's own 30 would still allow this - the recorded 20 doesn't
    res = _submit_round1(client, cand_token)
    assert res.status_code == 400 and "Time limit" in res.json()["detail"]


def test_attempt_from_before_this_existed_keeps_its_scenarios_limit(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    _set_started_ago(25, recorded=None)  # started before limits were recorded
    _put_settings(client, hr_token, round1_time_limit_minutes=20)
    state = client.get("/candidate/round/1", cookies=_auth(cand_token)).json()
    assert state["submission"]["time_limit_minutes"] == 30
    assert _submit_round1(client, cand_token).status_code == 201


def test_expire_and_auto_close_use_the_recorded_limit(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {
        "coverage_score": 0, "misses": [], "final_score": 0, "feedback_text": "x", "concept_coverage": [],
        "_provenance": {"model": "m", "prompt_file": "f", "prompt_hash": "h"}})
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    _put_settings(client, hr_token, round1_time_limit_minutes=20)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    _set_started_ago(15)
    assert client.post("/candidate/round/1/expire", json={}, cookies=_auth(cand_token)).status_code == 400  # 15 < 20
    _set_started_ago(21)
    assert client.post("/candidate/round/1/expire", json={}, cookies=_auth(cand_token)).status_code == 200


def test_clearing_the_setting_restores_per_scenario_limits(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    _put_settings(client, hr_token, round1_time_limit_minutes=20)
    assert _put_settings(client, hr_token, round1_time_limit_minutes=None).json()["round1_time_limit_minutes"] is None
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert client.get("/candidate/round/1", cookies=_auth(cand_token)).json()["scenario"]["round_time_limit_minutes"] == 30


def test_round_limits_are_validated(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    assert _put_settings(client, hr_token, round2_time_limit_minutes=0).status_code == 422
    assert _put_settings(client, hr_token, round3_time_limit_minutes=481).status_code == 422
    assert _put_settings(client, hr_token, round4_time_limit_minutes=20).status_code == 200


def test_hr_sees_the_limit_candidates_will_get(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _publish_scenario(client, hr_token, monkeypatch)
    _put_settings(client, hr_token, round1_time_limit_minutes=20)
    got = client.get(f"/hr/scenarios/{scenario['id']}", cookies=_auth(hr_token)).json()
    assert got["round_time_limit_minutes"] == 20 and got["time_limit_minutes"] == 30


def test_reference_generation_is_sized_to_the_round_limit(client, monkeypatch):
    from app.services import llm_service
    seen = {}
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kw: (seen.update(kw), [])[1])
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _put_settings(client, hr_token, round1_time_limit_minutes=20)
    client.post("/hr/scenarios", json={"round_number": 1, "title": "t", "description": "d", "experience_band": "0-7", "time_limit_minutes": 7},
                cookies=_auth(hr_token))
    assert seen["time_limit_minutes"] == 20
