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


def test_updating_one_scenarios_time_limit_never_touches_another(client, monkeypatch):
    """Regression for a real reported bug: HR changing one round's time
    limit appeared to change every round's - traced to app.js's create-
    scenario form fields never being reset when switching rounds, not a
    backend issue, but this locks in that PATCH itself has always been
    correctly scoped to exactly the scenario_id in the URL."""
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    draft_a = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "Draft A", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    ).json()
    draft_b = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "Draft B", "description": "desc", "experience_band": "7+", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    ).json()

    res = client.patch(f"/hr/scenarios/{draft_a['id']}", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["time_limit_minutes"] == 45

    # draft_b is completely untouched.
    all_scenarios = client.get("/hr/scenarios", cookies=_auth(hr_token)).json()
    b_after = next(s for s in all_scenarios if s["id"] == draft_b["id"])
    assert b_after["time_limit_minutes"] == 30


def test_editing_reference_json_with_a_non_dict_row_gives_a_clean_error_not_a_raw_exception(client, monkeypatch):
    """A row that isn't a dict at all (a bare string, here) used to raise
    a plain TypeError inside TestCaseRow(**row) whose message is Python/
    pydantic internals ("...argument after ** must be a mapping, not
    str") - leaked straight into the HTTP response body. Confirms the
    error is now a clean, purpose-written message instead."""
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    draft = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "Draft", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    ).json()

    res = client.patch(f"/hr/scenarios/{draft['id']}", json={"reference_json": ["not", "a", "dict"]}, cookies=_auth(hr_token))
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "argument after **" not in detail
    assert "must be a mapping" not in detail
    assert "each row must be an object" in detail


def test_time_limit_can_be_changed_on_a_live_published_scenario(client, monkeypatch):
    """The real-world case that matters: HR adjusting the duration of the
    scenario candidates are actually taking right now, not an unpublished
    draft. Unlike title/description/reference_json (see update_scenario),
    the time limit has its own endpoint specifically because it's safe to
    change after publish - it doesn't invalidate anything a candidate was
    already scored against."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_scenario(client, hr_token, monkeypatch)
    # _publish_scenario returns the pre-publish creation response, not a
    # re-fetch - confirm the actual current state via a fresh read rather
    # than trusting that stale dict for status/is_live.
    fresh = client.get("/hr/scenarios", cookies=_auth(hr_token)).json()
    current = next(s for s in fresh if s["id"] == published["id"])
    assert current["status"] == "published"
    assert current["is_live"] is True  # first scenario for a round+band goes live automatically

    res = client.patch(f"/hr/scenarios/{published['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["time_limit_minutes"] == 45
    assert res.json()["status"] == "published"  # unaffected - still not editable via the draft-only endpoint

    # Reflected wherever the scenario is read from, including what a
    # candidate would actually see.
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    state = client.get("/candidate/round/1", cookies=_auth(cand_token)).json()
    assert state["scenario"]["time_limit_minutes"] == 45

    # The draft-only edit endpoint still correctly refuses this same
    # published scenario - the new endpoint is additive, not a bypass of
    # that rule for the other fields.
    res = client.patch(f"/hr/scenarios/{published['id']}", json={"title": "Sneaky rename"}, cookies=_auth(hr_token))
    assert res.status_code == 400


def test_time_limit_is_blocked_while_a_candidate_is_mid_round(client, monkeypatch):
    """A deadline shifting while someone's clock is already running isn't
    something they could reasonably plan around - unlike the "safe to
    change anytime" case above, this must be blocked outright, not just
    take effect live."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_scenario(client, hr_token, monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))

    res = client.patch(f"/hr/scenarios/{published['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 409
    assert CANDIDATE1_EMAIL in res.json()["detail"]
    assert " is actively taking" in res.json()["detail"]

    # The time limit genuinely didn't change.
    fresh = client.get("/hr/scenarios", cookies=_auth(hr_token)).json()
    assert next(s for s in fresh if s["id"] == published["id"])["time_limit_minutes"] == 30

    # Once they submit, HR is free to change it again.
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )
    res = client.patch(f"/hr/scenarios/{published['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["time_limit_minutes"] == 45


def test_time_limit_blocked_on_a_different_round_of_the_same_band_mid_assessment(client, monkeypatch):
    """A candidate actively taking round 1 could reach round 2 or 3
    within the same sitting - changing THOSE rounds' time limits while
    round 1 is still running is just as much a live change-out-from-under
    them as editing round 1 itself. The block has to cover the whole
    band, not just the exact scenario a candidate happens to be on."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    round1 = _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="Round 1 scenario")
    round2 = _publish_scenario(client, hr_token, monkeypatch, round_number=2, band="0-7", title="Round 2 scenario")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))

    res = client.patch(f"/hr/scenarios/{round2['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 409
    assert CANDIDATE1_EMAIL in res.json()["detail"]
    assert " is actively taking" in res.json()["detail"]

    fresh = client.get("/hr/scenarios", cookies=_auth(hr_token)).json()
    assert next(s for s in fresh if s["id"] == round2["id"])["time_limit_minutes"] == 30


def test_time_limit_allowed_between_rounds_of_the_same_band(client, monkeypatch):
    """Once a candidate has submitted round 1 and hasn't started round 2
    yet, no clock is actively running for them - HR can edit again even
    though they're still mid-assessment overall."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    round1 = _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="Round 1 scenario")
    round2 = _publish_scenario(client, hr_token, monkeypatch, round_number=2, band="0-7", title="Round 2 scenario")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )

    res = client.patch(f"/hr/scenarios/{round2['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["time_limit_minutes"] == 45


def test_time_limit_not_blocked_by_a_different_bands_in_progress_candidate(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    junior = _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="Junior scenario")
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="7+", title="Senior scenario")
    # CANDIDATE1 is seeded in band "0-7" - see conftest.
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))

    senior_scenarios = [s for s in client.get("/hr/scenarios", cookies=_auth(hr_token)).json() if s["experience_band"] == "7+"]
    res = client.patch(f"/hr/scenarios/{senior_scenarios[0]['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["time_limit_minutes"] == 45


def test_time_limit_endpoint_validates_minimum_and_scenario_existence(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_scenario(client, hr_token, monkeypatch)

    res = client.patch(f"/hr/scenarios/{published['id']}/time-limit", json={"time_limit_minutes": 0}, cookies=_auth(hr_token))
    assert res.status_code == 422
    res = client.patch(f"/hr/scenarios/{published['id']}/time-limit", json={"time_limit_minutes": -5}, cookies=_auth(hr_token))
    assert res.status_code == 422
    res = client.patch("/hr/scenarios/999999/time-limit", json={"time_limit_minutes": 30}, cookies=_auth(hr_token))
    assert res.status_code == 404


def test_time_limit_endpoint_requires_hr(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_scenario(client, hr_token, monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.patch(f"/hr/scenarios/{published['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(cand_token))
    assert res.status_code == 403


def test_time_limit_edit_requires_at_least_one_minute_and_draft_status(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_scenario(client, hr_token, monkeypatch)

    # Can't edit a published scenario's time limit.
    res = client.patch(f"/hr/scenarios/{published['id']}", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 400

    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))
    draft = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "Draft", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    ).json()

    res = client.patch(f"/hr/scenarios/{draft['id']}", json={"time_limit_minutes": 0}, cookies=_auth(hr_token))
    assert res.status_code == 400
    res = client.patch(f"/hr/scenarios/{draft['id']}", json={"time_limit_minutes": -5}, cookies=_auth(hr_token))
    assert res.status_code == 400


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
