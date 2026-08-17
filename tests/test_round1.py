"""
Round 1 tests use a fake llm_service (monkeypatched) rather than calling the
real Claude API - keeps tests free, fast, and deterministic.
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD,
    CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,  # 0-7 band
    CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD,  # 0-7 band
    CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD,  # 7+ band
    FAKE_REFERENCE, _login, _auth, _publish_scenario,
)


def test_candidate_sees_only_matching_band_scenario(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, band="7+", title="Senior-only scenario")

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)  # 0-7 band
    res = client.get("/candidate/round/1", cookies=_auth(cand_token))
    assert res.status_code == 200
    assert res.json()["scenario"] is None

    senior_token = _login(client, CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD)  # 7+ band
    res = client.get("/candidate/round/1", cookies=_auth(senior_token))
    assert res.json()["scenario"]["title"] == "Senior-only scenario"


def test_scenario_list_retains_every_scenario_with_a_reference(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    ids = []
    for title in ("First attempt", "Second attempt", "Third attempt"):
        scenario = client.post(
            "/hr/scenarios",
            json={"round_number": 1, "title": title, "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
            cookies=_auth(hr_token),
        ).json()
        ids.append(scenario["id"])
    client.post(f"/hr/scenarios/{ids[0]}/publish", cookies=_auth(hr_token))  # archives nothing yet, just goes live

    # A generation that never produced a reference (e.g. a failed call)
    # is noise, not a scenario HR meant to keep - excluded by default.
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        client.post(
            "/hr/scenarios",
            json={"round_number": 1, "title": "Failed generation", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
            cookies=_auth(hr_token),
        )
    except RuntimeError:
        pass  # the synchronous generation call raises; the scenario row exists with reference_json=None

    res = client.get("/hr/scenarios", cookies=_auth(hr_token))
    titles = {s["title"] for s in res.json()}
    assert titles == {"First attempt", "Second attempt", "Third attempt"}  # all three retained, failure excluded


def test_second_publish_in_a_slot_does_not_auto_take_the_live_spot(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    a = _publish_scenario(client, hr_token, monkeypatch, title="Scenario A")  # first one in - auto-live
    b = _publish_scenario(client, hr_token, monkeypatch, title="Scenario B")

    res = client.get(f"/hr/scenarios/{a['id']}", cookies=_auth(hr_token))
    assert res.json()["status"] == "published" and res.json()["is_live"] is True
    res = client.get(f"/hr/scenarios/{b['id']}", cookies=_auth(hr_token))
    assert res.json()["status"] == "published" and res.json()["is_live"] is False


def test_move_to_screening_switches_the_live_scenario(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    a = _publish_scenario(client, hr_token, monkeypatch, title="Scenario A")
    b = _publish_scenario(client, hr_token, monkeypatch, title="Scenario B")

    res = client.post(f"/hr/scenarios/{b['id']}/move-to-screening", cookies=_auth(hr_token))
    assert res.json()["is_live"] is True

    res = client.get(f"/hr/scenarios/{a['id']}", cookies=_auth(hr_token))
    assert res.json()["status"] == "published" and res.json()["is_live"] is False  # demoted, not archived

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.get("/candidate/round/1", cookies=_auth(cand_token))
    assert res.json()["scenario"]["title"] == "Scenario B"  # candidates now see the newly-live one


def test_move_to_screening_requires_published_status(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    draft = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "Still a draft", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    ).json()

    res = client.post(f"/hr/scenarios/{draft['id']}/move-to-screening", cookies=_auth(hr_token))
    assert res.status_code == 400


def test_deleted_scenario_id_is_never_reused(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)

    def create(title):
        return client.post(
            "/hr/scenarios",
            json={"round_number": 1, "title": title, "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
            cookies=_auth(hr_token),
        ).json()

    first = create("First")
    client.delete(f"/hr/scenarios/{first['id']}", cookies=_auth(hr_token))  # frees up the highest id
    second = create("Second")

    assert second["id"] > first["id"]  # not recycled, even though first's id was the current max


def test_can_delete_a_draft_but_not_a_published_scenario(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_scenario(client, hr_token, monkeypatch)

    res = client.delete(f"/hr/scenarios/{published['id']}", cookies=_auth(hr_token))
    assert res.status_code == 400  # can't delete something a candidate might already be scored against

    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))
    draft = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "Bad draft", "description": "desc", "experience_band": "7+", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    ).json()

    res = client.delete(f"/hr/scenarios/{draft['id']}", cookies=_auth(hr_token))
    assert res.status_code == 204

    res = client.get("/hr/scenarios", cookies=_auth(hr_token))
    assert draft["id"] not in [s["id"] for s in res.json()]


def test_candidate_round_never_exposes_reference_answer(client, monkeypatch):
    # The reference answer is the answer key - it must never reach a
    # candidate's response, even though HR's own view of the same
    # scenario legitimately includes it.
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _publish_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.get("/candidate/round/1", cookies=_auth(cand_token))
    assert "reference_json" not in res.json()["scenario"]

    res = client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    assert "reference_json" not in (res.json().get("scenario") or {})

    # Sanity check the reference genuinely exists (HR's own view has it) -
    # otherwise this test would trivially pass by testing nothing.
    hr_scenarios = client.get("/hr/scenarios", cookies=_auth(hr_token)).json()
    assert next(s for s in hr_scenarios if s["id"] == scenario["id"])["reference_json"]


def test_draft_scenario_not_visible_until_published(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "Draft only", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    )

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.get("/candidate/round/1", cookies=_auth(cand_token))
    assert res.json()["scenario"] is None


def test_submit_rejected_once_time_limit_has_passed(client, monkeypatch):
    """Server-side backstop for the timed assessment - the client-side
    countdown in app.js is what candidates see, but a direct API call
    submitting long after the deadline must still be rejected (see
    candidate.py's _require_within_time_limit). Round 2/3's dedicated
    submit endpoints share this exact same helper, so this one
    integration-level proof covers all three rather than tripling it up."""
    from datetime import datetime, timedelta
    import app.database as database_module
    from app.models import Submission

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Timed scenario")  # 30-minute default limit

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))

    db = database_module.SessionLocal()
    submission = db.query(Submission).filter(Submission.round_number == 1).one()
    submission.started_at = datetime.utcnow() - timedelta(minutes=35)  # past 30 min + grace
    db.commit()
    db.close()

    res = client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 400
    assert "Time limit" in res.json()["detail"]


def test_submit_rejects_empty_content_server_side(client, monkeypatch):
    """app.js's doSubmitRound1 already blocks an empty submission client-
    side, but the server must enforce this itself too (see
    schemas.SubmissionCreate's min_length=1) - a direct API call
    shouldn't be able to burn the candidate's one attempt on nothing."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))

    res = client.post("/candidate/round/1/submit", json={"content": []}, cookies=_auth(cand_token))
    assert res.status_code == 422


def test_round2_locked_until_round1_submitted(client, monkeypatch):
    _publish_scenario(client, _login(client, HR_EMAIL, HR_PASSWORD), monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.get("/candidate/round/2", cookies=_auth(cand_token))
    assert res.status_code == 403


def test_round1_submission_scored_via_background_task(client, monkeypatch):
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _publish_scenario(client, hr_token, monkeypatch, title="Search box")

    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {
            "coverage_score": 80, "misses": ["boundary case"], "final_score": 75, "feedback_text": "Solid start.",
            "concept_coverage": [
                {"category": "Positive", "total": 2, "covered": 2, "notes": "Both happy-path cases hit."},
                {"category": "Boundary", "total": 1, "covered": 0, "notes": "Empty-input case missed entirely."},
            ],
            "_provenance": {"model": "claude-sonnet-4-5", "prompt_file": "round1_scoring.txt", "prompt_hash": "abc123def456"},
        },
    )

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))

    res = client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "Empty search", "steps": "Search with no input", "expected_result": "No results shown"}]},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    submission_id = res.json()["id"]

    # TestClient runs BackgroundTasks synchronously before returning the
    # response context, so scoring should already be done here.
    res = client.get("/candidate/submissions", cookies=_auth(cand_token))
    scored = next(s for s in res.json() if s["id"] == submission_id)
    assert scored["status"] == "scored"
    assert "score" not in scored  # candidates never see their own score - HR's call to share

    # Round 2 should now be reachable (not published yet, but not 403'd).
    res = client.get("/candidate/round/2", cookies=_auth(cand_token))
    assert res.status_code == 200
    assert res.json()["scenario"] is None

    # HR dashboard reflects the scored result.
    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    round1 = next(r for r in candidate_row["rounds"] if r["round_number"] == 1)
    assert round1["final_score"] == 75

    # And the drill-down report includes the candidate's structured content.
    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_round1 = next(s for s in res.json() if s["round_number"] == 1)
    assert report_round1["content"][0]["title"] == "Empty search"
    assert report_round1["scenario"]["title"] == "Search box"

    # Provenance (which model/prompt-version produced this score) lands
    # on the Score row too - see scoring_service._apply_provenance.
    assert report_round1["score"]["scoring_model"] == "claude-sonnet-4-5"
    assert report_round1["score"]["scoring_prompt_file"] == "round1_scoring.txt"
    assert report_round1["score"]["scoring_prompt_hash"] == "abc123def456"
    assert report_round1["score"]["scored_at"] is not None

    # Per-category coverage breakdown (see models.Score.concept_coverage_json)
    # lands alongside the flat misses_json list, doesn't replace it.
    coverage_by_category = {c["category"]: c for c in report_round1["score"]["concept_coverage_json"]}
    assert coverage_by_category["Positive"] == {"category": "Positive", "total": 2, "covered": 2, "notes": "Both happy-path cases hit."}
    assert coverage_by_category["Boundary"]["covered"] == 0
    assert report_round1["score"]["misses_json"] == ["boundary case"]


def test_scenario_history_aggregates_clear_rate_and_common_misses(client, monkeypatch):
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")

    # Untouched scenario: shouldn't show up in history at all yet.
    res = client.get("/hr/history", cookies=_auth(hr_token))
    assert res.json() == []

    # Candidate 1 clears; candidate 2 doesn't - both miss the same case,
    # so it should surface as a count-2 common miss.
    scored_results = iter([
        {"coverage_score": 90, "misses": ["boundary case: empty cart"], "final_score": 85, "feedback_text": "Strong."},
        {"coverage_score": 40, "misses": ["boundary case: empty cart", "negative case: expired card"], "final_score": 50, "feedback_text": "Needs work."},
    ])
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: next(scored_results))

    for email, password in ((CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD), (CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD)):
        cand_token = _login(client, email, password)
        client.post("/candidate/round/1/start", cookies=_auth(cand_token))
        client.post(
            "/candidate/round/1/submit",
            json={"content": [{"title": "Add item to cart", "steps": "...", "expected_result": "..."}]},
            cookies=_auth(cand_token),
        )

    res = client.get("/hr/history", cookies=_auth(hr_token))
    history = res.json()
    assert len(history) == 1
    entry = history[0]
    assert entry["scenario_id"] == scenario["id"]
    assert entry["title"] == "Checkout flow"
    assert entry["is_live"] is True
    assert entry["total_attempted"] == 2
    assert entry["scored_count"] == 2
    assert entry["cleared_count"] == 1   # 85 >= 70
    assert entry["not_cleared_count"] == 1  # 50 < 70
    assert entry["cleared_pct"] == 50.0
    assert entry["passing_score"] == 70
    misses_by_text = {m["text"]: m["count"] for m in entry["common_misses"]}
    assert misses_by_text["boundary case: empty cart"] == 2  # both candidates missed it
    assert misses_by_text["negative case: expired card"] == 1
