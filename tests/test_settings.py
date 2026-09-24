"""
HR's runtime-editable pass-criteria settings (see routers/hr.py's
GET/PUT /hr/settings, models.AppSettings). No LLM involved.
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    _login, _auth, _publish_scenario,
)


# Per-round time limits (see test_round_time_limits.py) - unset unless a test sets them.
_NO_ROUND_LIMITS = {f"round{n}_time_limit_minutes": None for n in (1, 2, 3, 4)}


def test_default_settings_row_exists(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    res = client.get("/hr/settings", cookies=_auth(hr_token))
    assert res.status_code == 200
    body = res.json()
    assert body == {
        "round1_passing_score": 70,
        "round2_passing_score": 70,
        "round3_passing_score": 70,
        "round4_passing_score": 70,
        "final_passing_score": 280,
        "reapplication_window_months": 6,
        "assessment_window_days": 1,
        "round1_time_limit_minutes": None,
        "round2_time_limit_minutes": None,
        "round3_time_limit_minutes": None,
        "round4_time_limit_minutes": None,
    }


def test_settings_round_trip(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    payload = {
        "round1_passing_score": 60,
        "round2_passing_score": 80,
        "round3_passing_score": 75,
        "round4_passing_score": 75,
        "final_passing_score": 200,
        "reapplication_window_months": 3,
        "assessment_window_days": 21,
    }
    res = client.put("/hr/settings", json=payload, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json() == {**payload, **_NO_ROUND_LIMITS}

    res = client.get("/hr/settings", cookies=_auth(hr_token))
    assert res.json() == {**payload, **_NO_ROUND_LIMITS}


def test_settings_reject_out_of_range_values(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    base = {
        "round1_passing_score": 70, "round2_passing_score": 70, "round3_passing_score": 70, "round4_passing_score": 70,
        "final_passing_score": 210, "reapplication_window_months": 6, "assessment_window_days": 14,
    }
    res = client.put("/hr/settings", json={**base, "final_passing_score": 401}, cookies=_auth(hr_token))
    assert res.status_code == 422
    res = client.put("/hr/settings", json={**base, "round1_passing_score": 101}, cookies=_auth(hr_token))
    assert res.status_code == 422
    res = client.put("/hr/settings", json={**base, "reapplication_window_months": 0}, cookies=_auth(hr_token))
    assert res.status_code == 422
    res = client.put("/hr/settings", json={**base, "assessment_window_days": 0}, cookies=_auth(hr_token))
    assert res.status_code == 422


def test_settings_endpoints_require_hr(client):
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert client.get("/hr/settings", cookies=_auth(cand_token)).status_code == 403
    assert client.put(
        "/hr/settings",
        json={"round1_passing_score": 70, "round2_passing_score": 70, "round3_passing_score": 70, "round4_passing_score": 70,
              "final_passing_score": 210, "reapplication_window_months": 6, "assessment_window_days": 14},
        cookies=_auth(cand_token),
    ).status_code == 403


def test_scenario_history_reacts_to_independently_changed_round_thresholds(client, monkeypatch):
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    # Lower round 1's bar to 50, leave round 2 at the default 70.
    client.put(
        "/hr/settings",
        json={"round1_passing_score": 50, "round2_passing_score": 70, "round3_passing_score": 70, "round4_passing_score": 70,
              "final_passing_score": 210, "reapplication_window_months": 6, "assessment_window_days": 14},
        cookies=_auth(hr_token),
    )

    scenario1 = _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1 scenario")
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 60, "misses": [], "final_score": 60, "feedback_text": "ok"},
    )
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )

    res = client.get("/hr/history", cookies=_auth(hr_token))
    entry = next(h for h in res.json() if h["scenario_id"] == scenario1["id"])
    # 60 >= round1's lowered bar of 50 -> cleared, even though 60 < the
    # (unchanged) default of 70 that round 2 still uses.
    assert entry["passing_score"] == 50
    assert entry["cleared_count"] == 1
    assert entry["not_cleared_count"] == 0
