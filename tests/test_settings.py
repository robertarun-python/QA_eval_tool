"""
HR's runtime-editable pass-criteria settings (see routers/hr.py's
GET/PUT /hr/settings, models.AppSettings). No LLM involved.
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    _login, _auth, _publish_scenario,
)


def test_default_settings_row_exists(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    res = client.get("/hr/settings", cookies=_auth(hr_token))
    assert res.status_code == 200
    body = res.json()
    assert body == {
        "round1_passing_score": 70,
        "round2_passing_score": 70,
        "round3_passing_score": 70,
        "final_passing_score": 210,
        "reapplication_window_months": 6,
        "round3_default_assistance_pct": 60,
    }


def test_settings_exposes_the_round3_default_assistance_pct_read_only(client):
    """See config.py's round3_default_assistance_pct - an env-sourced,
    deployment-level fallback (not HR-editable through this form, unlike
    the passing-score fields above), exposed here purely so the HR round3
    settings card in app.js can read the real server default instead of
    hardcoding its own separate copy of it."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    payload = {
        "round1_passing_score": 60, "round2_passing_score": 80, "round3_passing_score": 75,
        "final_passing_score": 200, "reapplication_window_months": 3,
    }
    res = client.put("/hr/settings", json=payload, cookies=_auth(hr_token))
    assert res.status_code == 200
    # Unaffected by the PUT - it isn't part of AppSettingsUpdate.
    assert res.json()["round3_default_assistance_pct"] == 60


def test_settings_round_trip(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    payload = {
        "round1_passing_score": 60,
        "round2_passing_score": 80,
        "round3_passing_score": 75,
        "final_passing_score": 200,
        "reapplication_window_months": 3,
    }
    res = client.put("/hr/settings", json=payload, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json() == {**payload, "round3_default_assistance_pct": 60}

    res = client.get("/hr/settings", cookies=_auth(hr_token))
    assert res.json() == {**payload, "round3_default_assistance_pct": 60}


def test_settings_reject_out_of_range_values(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    base = {
        "round1_passing_score": 70, "round2_passing_score": 70, "round3_passing_score": 70,
        "final_passing_score": 210, "reapplication_window_months": 6,
    }
    res = client.put("/hr/settings", json={**base, "final_passing_score": 301}, cookies=_auth(hr_token))
    assert res.status_code == 422
    res = client.put("/hr/settings", json={**base, "round1_passing_score": 101}, cookies=_auth(hr_token))
    assert res.status_code == 422
    res = client.put("/hr/settings", json={**base, "reapplication_window_months": 0}, cookies=_auth(hr_token))
    assert res.status_code == 422


def test_settings_endpoints_require_hr(client):
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert client.get("/hr/settings", cookies=_auth(cand_token)).status_code == 403
    assert client.put(
        "/hr/settings",
        json={"round1_passing_score": 70, "round2_passing_score": 70, "round3_passing_score": 70,
              "final_passing_score": 210, "reapplication_window_months": 6},
        cookies=_auth(cand_token),
    ).status_code == 403


def test_scenario_history_reacts_to_independently_changed_round_thresholds(client, monkeypatch):
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    # Lower round 1's bar to 50, leave round 2 at the default 70.
    client.put(
        "/hr/settings",
        json={"round1_passing_score": 50, "round2_passing_score": 70, "round3_passing_score": 70,
              "final_passing_score": 210, "reapplication_window_months": 6},
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
