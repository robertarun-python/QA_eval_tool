"""
Round 1 tests use a fake llm_service (monkeypatched) rather than calling the
real Claude API - keeps tests free, fast, and deterministic.
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD,
    CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,  # 0-7 band
    CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD,  # 7+ band
)

FAKE_REFERENCE = [
    {"title": "ref case", "preconditions": "", "steps": "...", "expected_result": "...", "priority": "High", "type": "Positive"},
]


def _login(client, email, password):
    return client.post("/auth/login", json={"email": email, "password": password}).json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="Login form"):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))

    scenario = client.post(
        "/hr/scenarios",
        json={"round_number": round_number, "title": title, "description": "desc", "experience_band": band, "time_limit_minutes": 30},
        headers=_auth(hr_token),
    ).json()
    client.post(f"/hr/scenarios/{scenario['id']}/publish", headers=_auth(hr_token))
    return scenario


def test_candidate_sees_only_matching_band_scenario(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, band="7+", title="Senior-only scenario")

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)  # 0-7 band
    res = client.get("/candidate/round/1", headers=_auth(cand_token))
    assert res.status_code == 200
    assert res.json()["scenario"] is None

    senior_token = _login(client, CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD)  # 7+ band
    res = client.get("/candidate/round/1", headers=_auth(senior_token))
    assert res.json()["scenario"]["title"] == "Senior-only scenario"


def test_draft_scenario_not_visible_until_published(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "Draft only", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        headers=_auth(hr_token),
    )

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.get("/candidate/round/1", headers=_auth(cand_token))
    assert res.json()["scenario"] is None


def test_round2_locked_until_round1_submitted(client, monkeypatch):
    _publish_scenario(client, _login(client, HR_EMAIL, HR_PASSWORD), monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.get("/candidate/round/2", headers=_auth(cand_token))
    assert res.status_code == 403


def test_round1_submission_scored_via_background_task(client, monkeypatch):
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _publish_scenario(client, hr_token, monkeypatch, title="Search box")

    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 80, "misses": ["boundary case"], "final_score": 75, "feedback_text": "Solid start."},
    )

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", headers=_auth(cand_token))

    res = client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "Empty search", "steps": "Search with no input", "expected_result": "No results shown"}]},
        headers=_auth(cand_token),
    )
    assert res.status_code == 201
    submission_id = res.json()["id"]

    # TestClient runs BackgroundTasks synchronously before returning the
    # response context, so scoring should already be done here.
    res = client.get("/candidate/submissions", headers=_auth(cand_token))
    scored = next(s for s in res.json() if s["id"] == submission_id)
    assert scored["score"]["final_score"] == 75
    assert scored["score"]["misses_json"] == ["boundary case"]

    # Round 2 should now be reachable (not published yet, but not 403'd).
    res = client.get("/candidate/round/2", headers=_auth(cand_token))
    assert res.status_code == 200
    assert res.json()["scenario"] is None

    # HR dashboard reflects the scored result.
    res = client.get("/hr/candidates", headers=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    round1 = next(r for r in candidate_row["rounds"] if r["round_number"] == 1)
    assert round1["final_score"] == 75

    # And the drill-down report includes the candidate's structured content.
    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", headers=_auth(hr_token))
    report_round1 = next(s for s in res.json() if s["round_number"] == 1)
    assert report_round1["content"][0]["title"] == "Empty search"
    assert report_round1["scenario"]["title"] == "Search box"
