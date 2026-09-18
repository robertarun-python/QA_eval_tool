"""
Round 1 tests use a fake llm_service (monkeypatched) rather than calling the
real Claude API - keeps tests free, fast, and deterministic.
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD,
    CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,  # 0-7 band
    CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD,  # 0-7 band
    CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD,
    FAKE_REFERENCE, _login, _auth, _publish_scenario, _publish_round4_scenario, _complete_rounds_1_through_3,
    _create_round4_test_case,
)


def test_candidate_sees_only_matching_band_scenario(client, monkeypatch):
    # Experience band is a hidden feature now (see app.js's DEFAULT_BAND) -
    # every seeded candidate gets the same band by default, so promote
    # candidate3 via the still-functional (API-only) band endpoint to
    # prove the underlying _live_scenario band filter still works.
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    candidates = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    candidate3_id = next(c["id"] for c in candidates if c["email"] == CANDIDATE3_EMAIL)
    client.patch(f"/hr/candidates/{candidate3_id}/band", json={"experience_band": "7+"}, cookies=_auth(hr_token))

    _publish_scenario(client, hr_token, monkeypatch, band="7+", title="Senior-only scenario")

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)  # 0-7 band
    res = client.get("/candidate/round/1", cookies=_auth(cand_token))
    assert res.status_code == 200
    assert res.json()["scenario"] is None

    senior_token = _login(client, CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD)  # promoted to 7+ above
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


def test_time_limit_cannot_be_changed_once_published(client, monkeypatch):
    """Rounds 1-3 now follow the same rule as title/description/
    reference_json (see update_scenario) - a published scenario's time
    limit is locked, full stop, regardless of whether anyone's mid-round.
    Fixing a wrong live time limit means publishing a corrected scenario,
    not editing this one in place - see hr.py's update_scenario_time_limit."""
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
    assert res.status_code == 400

    # The time limit genuinely didn't change, anywhere it's read from,
    # including what a candidate would actually see.
    fresh = client.get("/hr/scenarios", cookies=_auth(hr_token)).json()
    assert next(s for s in fresh if s["id"] == published["id"])["time_limit_minutes"] == 30
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    state = client.get("/candidate/round/1", cookies=_auth(cand_token)).json()
    assert state["scenario"]["time_limit_minutes"] == 30

    # The draft-only edit endpoint refuses this same published scenario
    # too - time_limit_minutes isn't a special case anymore.
    res = client.patch(f"/hr/scenarios/{published['id']}", json={"title": "Sneaky rename"}, cookies=_auth(hr_token))
    assert res.status_code == 400


def test_time_limit_can_still_be_changed_on_a_draft(client, monkeypatch):
    """The one case time_limit_minutes remains mutable for rounds 1-3:
    before publishing, same as title/description/reference_json."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))
    draft = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "Draft", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    ).json()

    res = client.patch(f"/hr/scenarios/{draft['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["time_limit_minutes"] == 45


def test_time_limit_is_blocked_while_a_candidate_is_mid_round4(client, monkeypatch):
    """Round 4 is the one exception to the draft-only rule above - it has
    no draft phase to defer edits into (its scenarios go live immediately
    on creation), so it keeps the old always-mutable-but-guarded
    behavior: blocked outright while a candidate's clock is actively
    running, since a deadline shifting mid-round isn't something they
    could reasonably plan around."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    published = _publish_round4_scenario(client, hr_token, monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    # seed_upto=1: this test STARTS the automation round itself, which
    # only needs round 1 behind it since the 2<->4 swap.
    _complete_rounds_1_through_3(client, hr_token, cand_token, monkeypatch, seed_upto=1)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    res = client.patch(f"/hr/scenarios/{published['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 409
    assert CANDIDATE1_EMAIL in res.json()["detail"]
    assert " is actively taking" in res.json()["detail"]

    # The time limit genuinely didn't change.
    fresh = client.get("/hr/scenarios", cookies=_auth(hr_token)).json()
    assert next(s for s in fresh if s["id"] == published["id"])["time_limit_minutes"] == 30

    # Once they submit, HR is free to change it again. Submitting requires
    # at least one test case with a turn (see candidate.py's round4_submit).
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round4_respond", lambda **kwargs: {
        "response_text": "ok", "steps": [{"description": "Did a thing", "status": "pass"}],
        "observed_result": "It worked.", "status": "pass",
    })
    monkeypatch.setattr(llm_service, "score_round4_conversation", lambda **kwargs: {
        "coverage_score": 0, "misses": [], "final_score": 0, "feedback_text": "Nothing submitted.",
    })
    tc = _create_round4_test_case(client, cand_token, title="A test case")
    client.post("/candidate/round/2/turn", json={"test_case_id": tc["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))
    res = client.post("/candidate/round/2/submit", cookies=_auth(cand_token))
    assert res.status_code == 201
    res = client.patch(f"/hr/scenarios/{published['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["time_limit_minutes"] == 45


def test_time_limit_blocked_on_round4_by_a_different_rounds_in_progress_candidate(client, monkeypatch):
    """The in-progress guard covers the whole band, not just the exact
    scenario a candidate happens to be on - a candidate mid-round-1 blocks
    a round 4 time-limit edit too."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="Round 1 scenario")
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch, band="0-7")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))

    res = client.patch(f"/hr/scenarios/{round4['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 409
    assert CANDIDATE1_EMAIL in res.json()["detail"]
    assert " is actively taking" in res.json()["detail"]

    fresh = client.get("/hr/scenarios", cookies=_auth(hr_token)).json()
    assert next(s for s in fresh if s["id"] == round4["id"])["time_limit_minutes"] == 30


def test_time_limit_allowed_on_round4_between_rounds_of_the_same_band(client, monkeypatch):
    """Once a candidate has submitted round 1 and hasn't started round 2
    yet, no clock is actively running for them - HR can edit round 4's
    time limit again even though the candidate is still mid-assessment
    overall."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="Round 1 scenario")
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch, band="0-7")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )

    res = client.patch(f"/hr/scenarios/{round4['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["time_limit_minutes"] == 45


def test_time_limit_not_blocked_by_a_different_bands_in_progress_candidate(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="Junior scenario")
    senior_round4 = _publish_round4_scenario(client, hr_token, monkeypatch, band="7+")
    # CANDIDATE1 is seeded in band "0-7" - see conftest.
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))

    res = client.patch(f"/hr/scenarios/{senior_round4['id']}/time-limit", json={"time_limit_minutes": 45}, cookies=_auth(hr_token))
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


# ---- Test data / expected-result specificity (see schemas.TestCaseRow's
# test_data field and prompts/round1_scoring.txt's specificity rubric).
# The field is additive and defaults to "": every assertion below about
# backward compatibility is guarding real stored data written before it
# existed, not a hypothetical. ----

def _score_stub(**extra):
    base = {
        "coverage_score": 80, "misses": [], "final_score": 75,
        "feedback_text": "ok", "concept_coverage": [],
    }
    base.update(extra)
    return base


def test_round1_test_data_persists_end_to_end_and_reaches_hr(client, monkeypatch):
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Transfer funds")
    captured = {}

    def _fake_score(**kwargs):
        captured.update(kwargs)
        return _score_stub()

    monkeypatch.setattr(llm_service, "score_round1_submission", _fake_score)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    res = client.post(
        "/candidate/round/1/submit",
        json={"content": [{
            "title": "Transfer with zero amount",
            "steps": "Enter 0.00 and submit the transfer form",
            "test_data": "amount = 0.00; from = SAV-1001 (balance 50.00)",
            "expected_result": "Transfer is rejected and 'Amount must be greater than zero' is shown",
        }]},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201

    # Stored on the submission...
    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_round1 = next(s for s in res.json() if s["round_number"] == 1)
    assert report_round1["content"][0]["test_data"] == "amount = 0.00; from = SAV-1001 (balance 50.00)"

    # ...and actually handed to the scorer, not silently dropped.
    assert "amount = 0.00" in captured["candidate_submission"]


def test_round1_row_without_test_data_still_submits_and_defaults_to_empty(client, monkeypatch):
    """Backward compatibility: the field is additive, so a client (or a
    direct API call) that predates it must still be accepted, and the
    submit gate must be unchanged - title/steps/expected_result only."""
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Legacy shape")
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: _score_stub())

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    res = client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "No data field", "steps": "do the thing", "expected_result": "it happens"}]},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201

    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_round1 = next(s for s in res.json() if s["round_number"] == 1)
    assert report_round1["content"][0]["test_data"] == ""


def test_round1_specificity_is_surfaced_to_hr_when_the_scorer_returns_it(client, monkeypatch):
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Specificity")
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: _score_stub(specificity_score=42, specificity_notes="'valid data' is not a value."),
    )

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "Vague", "steps": "do it", "test_data": "valid data", "expected_result": "works"}]},
        cookies=_auth(cand_token),
    )

    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_round1 = next(s for s in res.json() if s["round_number"] == 1)
    assert report_round1["score"]["specificity"] == {"score": 42, "notes": "'valid data' is not a value."}

    # Candidates still never see any of it.
    res = client.get("/candidate/submissions", cookies=_auth(cand_token))
    assert "score" not in next(s for s in res.json() if s["round_number"] == 1)


def test_round1_score_without_specificity_reports_none_not_an_error(client, monkeypatch):
    """A Score written before the specificity rubric existed (scorer
    returns no specificity_score) must read back as None, not raise."""
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Pre-rubric")
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: _score_stub())

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "y", "expected_result": "z"}]},
        cookies=_auth(cand_token),
    )

    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_round1 = next(s for s in res.json() if s["round_number"] == 1)
    assert report_round1["score"]["specificity"] is None


def test_round1_prompts_actually_ask_for_and_grade_test_data():
    """Guards the two prompt files against a future edit quietly dropping
    the field - the schema/UI can carry test_data all day, but if the
    prompts stop asking for and grading it, the round silently stops
    assessing specificity at all."""
    from app.services.llm_service import _load_prompt

    reference_prompt = _load_prompt("round1_reference_generation.txt")
    assert "test_data" in reference_prompt
    assert "test_data" in reference_prompt.split("Respond with ONLY")[1]  # present in the required output shape

    scoring_prompt = _load_prompt("round1_scoring.txt")
    assert "test_data" in scoring_prompt
    assert "specificity_score" in scoring_prompt
    assert "specificity_notes" in scoring_prompt
