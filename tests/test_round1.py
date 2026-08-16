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


def test_scenario_list_shows_only_current_per_round_and_band(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    for title in ("First attempt", "Second attempt", "Third attempt"):
        client.post(
            "/hr/scenarios",
            json={"round_number": 1, "title": title, "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
            headers=_auth(hr_token),
        )

    res = client.get("/hr/scenarios", headers=_auth(hr_token))
    matching = [s for s in res.json() if s["round_number"] == 1 and s["experience_band"] == "0-7"]
    assert len(matching) == 1
    assert matching[0]["title"] == "Third attempt"  # the most recently created one


def test_scenario_list_shows_live_plus_one_in_progress_draft(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_scenario(client, hr_token, monkeypatch, title="Live one")

    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))
    # Two draft attempts after publishing - only the newest should show,
    # never both (that would be the abandoned-attempts clutter again).
    client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "Abandoned idea", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        headers=_auth(hr_token),
    )
    draft = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "Draft replacement", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        headers=_auth(hr_token),
    ).json()

    res = client.get("/hr/scenarios", headers=_auth(hr_token))
    matching = [s for s in res.json() if s["round_number"] == 1 and s["experience_band"] == "0-7"]
    assert {s["id"] for s in matching} == {published["id"], draft["id"]}
    assert next(s for s in matching if s["id"] == published["id"])["status"] == "published"
    assert next(s for s in matching if s["id"] == draft["id"])["status"] == "draft"


def test_deleted_scenario_id_is_never_reused(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)

    def create(title):
        return client.post(
            "/hr/scenarios",
            json={"round_number": 1, "title": title, "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
            headers=_auth(hr_token),
        ).json()

    first = create("First")
    client.delete(f"/hr/scenarios/{first['id']}", headers=_auth(hr_token))  # frees up the highest id
    second = create("Second")

    assert second["id"] > first["id"]  # not recycled, even though first's id was the current max


def test_can_delete_a_draft_but_not_a_published_scenario(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_scenario(client, hr_token, monkeypatch)

    res = client.delete(f"/hr/scenarios/{published['id']}", headers=_auth(hr_token))
    assert res.status_code == 400  # can't delete something a candidate might already be scored against

    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))
    draft = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "Bad draft", "description": "desc", "experience_band": "7+", "time_limit_minutes": 30},
        headers=_auth(hr_token),
    ).json()

    res = client.delete(f"/hr/scenarios/{draft['id']}", headers=_auth(hr_token))
    assert res.status_code == 204

    res = client.get("/hr/scenarios", headers=_auth(hr_token))
    assert draft["id"] not in [s["id"] for s in res.json()]


def test_candidate_round_never_exposes_reference_answer(client, monkeypatch):
    # The reference answer is the answer key - it must never reach a
    # candidate's response, even though HR's own view of the same
    # scenario legitimately includes it.
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _publish_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.get("/candidate/round/1", headers=_auth(cand_token))
    assert "reference_json" not in res.json()["scenario"]

    res = client.post("/candidate/round/1/start", headers=_auth(cand_token))
    assert "reference_json" not in (res.json().get("scenario") or {})

    # Sanity check the reference genuinely exists (HR's own view has it) -
    # otherwise this test would trivially pass by testing nothing.
    hr_scenarios = client.get("/hr/scenarios", headers=_auth(hr_token)).json()
    assert next(s for s in hr_scenarios if s["id"] == scenario["id"])["reference_json"]


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
