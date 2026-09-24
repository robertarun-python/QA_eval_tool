"""
Round 4 is conversational and open-ended: the candidate creates their own
self-titled test cases (no fixed UI/API/DB category, no forced ordering)
and autosaves an in-progress draft per test case - see ARCHITECTURE.md and
routers/candidate.py's round 4 section. Same fake-llm_service approach as
test_round1.py/test_round2.py: never a real Claude call.
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD,
    CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,  # 0-7 band
    _login, _auth, _publish_scenario, _publish_round4_scenario,
    FAKE_ENVIRONMENT, FAKE_UI_MOCKUP,
)

FAKE_TURN_RESPONSE = {
    "response_text": "Here's a starting point for that.",
    "steps": [
        {"description": "Opened the login page and located the email field", "status": "pass"},
        {"description": "Entered the email address and submitted", "status": "pass", "detail": "HTTP 200"},
    ],
    "observed_result": "Login succeeded and the dashboard loaded.",
    "status": "pass",
}

FAKE_SCORE = {
    "coverage_score": 72,
    "misses": ["Login happy path: never verified the locator against a real page state"],
    "final_score": 68,
    "feedback_text": "Solid independent coverage across two distinct test cases, asked good follow-ups but accepted the first answer once.",
}


def _complete_round1_and_2(client, hr_token, cand_token, monkeypatch):
    # Submitting either round fires a background scoring call (see
    # candidate.py's _score_round1_in_background/_score_round2_in_background)
    # - without mocking these too (separate functions from the reference
    # generators _publish_scenario already mocks), every test that calls
    # this helper was silently making 2 real, paid Claude API calls in a
    # background thread, which is also where the rare "JSONDecodeError"
    # flakiness came from (a real response occasionally not being clean
    # JSON) - not a bug in round 4 itself.
    #
    # Also completes round 3 (AI-prompted coding): round 4 now requires
    # it (see ROUND_SEQUENCE in routers/candidate.py - round 3 was
    # reintroduced as a real round after this helper was first written),
    # the same way it already required rounds 1/2. Round 3 needs its own
    # scenario published too, unlike rounds 1/2 (published by each test
    # itself, before calling this helper) - no caller of this helper ever
    # needed a live round 3 scenario for anything else, so publishing it
    # here as a side effect keeps every existing call site a one-line
    # change (passing hr_token) instead of a several-line one.
    from app.services import llm_service, execution_service
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"},
    )
    monkeypatch.setattr(
        llm_service, "score_round2_submission",
        lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"},
    )
    monkeypatch.setattr(
        llm_service, "round3_coding_turn",
        lambda **kwargs: {"response_kind": "code_edit", "response_message": "ok", "code_after": "print(1)"},
    )
    monkeypatch.setattr(
        llm_service, "score_round3_coding",
        lambda **kwargs: {
            "correctness_score": 100, "precision_score": 100, "efficiency_score": 100,
            "independent_judgment_score": 100, "final_score": 100,
            "misses": [], "guardrail_violations": [], "feedback_text": "ok",
        },
    )
    monkeypatch.setattr(
        execution_service, "run_code",
        lambda **kwargs: execution_service.ExecutionResult(
            stdout="", stderr="", exit_code=0, timed_out=False, infra_error=False, duration_ms=1,
        ),
    )
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "Login works", "steps": "...", "expected_result": "..."}]},
        cookies=_auth(cand_token),
    )
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/4/submit",
        json={"investigation": [{"area": "Reproduced the issue"}], "root_cause": "..."},
        cookies=_auth(cand_token),
    )
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Coding challenge")
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "solve it"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/submit", cookies=_auth(cand_token))


def test_round4_environment_and_mockup_grounded_in_live_round1_scenario(client, monkeypatch):
    """Both generate_round2_automation_environment and generate_round2_automation_ui_mockup
    describe the actual app under test, which lives in round 1's
    scenario, not round 4's own (see hr.py's _generate_reference) - both
    must receive the identical resolved app_description."""
    from app.services import llm_service

    env_captured = {}
    mockup_captured = {}

    def _capture_env(**kwargs):
        env_captured.update(kwargs)
        return dict(FAKE_ENVIRONMENT)

    def _capture_mockup(**kwargs):
        mockup_captured.update(kwargs)
        return dict(FAKE_UI_MOCKUP)

    monkeypatch.setattr(llm_service, "generate_round2_automation_environment", _capture_env)
    monkeypatch.setattr(llm_service, "generate_round2_automation_ui_mockup", _capture_mockup)

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)

    # No round 1 scenario published yet for this band - falls back to
    # round 4's own description.
    client.post(
        "/hr/scenarios",
        json={"round_number": 2, "title": "R3 fallback", "description": "r3 own description", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    )
    assert env_captured["app_description"] == "r3 own description"
    assert mockup_captured["app_description"] == "r3 own description"

    # Publish a round 1 scenario with a distinct description, then a new
    # round 4 scenario should ground both in THAT, not its own text.
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="Library app")
    all_scenarios = client.get("/hr/scenarios", cookies=_auth(hr_token)).json()
    live_round1_scenario = next(s for s in all_scenarios if s["round_number"] == 1 and s["is_live"])

    client.post(
        "/hr/scenarios",
        json={"round_number": 2, "title": "R3 grounded", "description": "r3 own description", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    )
    assert env_captured["app_description"] == live_round1_scenario["description"]
    assert mockup_captured["app_description"] == live_round1_scenario["description"]


def test_round4_locked_until_rounds_1_and_2_submitted(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.get("/candidate/round/4", cookies=_auth(cand_token))
    assert res.status_code == 403


def test_round4_state_requires_start_first(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)

    res = client.get("/candidate/round/2/state", cookies=_auth(cand_token))
    assert res.status_code == 404

    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    res = client.get("/candidate/round/2/state", cookies=_auth(cand_token))
    assert res.status_code == 200
    body = res.json()
    assert body["round1_context"]["submitted_rows"][0]["title"] == "Login works"
    assert body["test_cases"] == []
    assert body["turns"] == []
    assert body["environment"]["fields"]["Test account email"] == "qa.tester@example.com"
    assert body["ui_mockup"]["screens"][0]["name"] == "Login"
    # No fixed-phase concept survives this redesign, and no turn cap either.
    assert "turn_cap_per_test_case" not in body
    assert "current_phase" not in body
    assert "phases" not in body


def test_round4_environment_generation_rejects_malformed_shape(client, monkeypatch):
    """Same class of gap as the turn-response one above, one step earlier
    in the pipeline: generate_round2_automation_environment only checked for a
    'fields' key, not that it actually matched Round2AutomationEnvironmentOut
    (fields: dict[str, str]) - a nested object as a field value is valid
    JSON but the wrong shape, and used to only fail once a candidate's
    Round2EntryStateOut read hit it live."""
    import json
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    bad_response = json.dumps({"fields": {"API base URL": {"nested": "not a string"}}})
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: bad_response)

    res = client.post(
        "/hr/scenarios",
        json={"round_number": 2, "title": "Automation challenge", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 502

    # No half-populated scenario left behind (same rollback-on-failure
    # behavior as test_round4_ui_mockup_failure_also_prevents_scenario_from_being_usable).
    res = client.get("/hr/scenarios", cookies=_auth(hr_token))
    assert res.json() == []


def test_round4_reference_resyncs_when_round1_scenario_changes(client, monkeypatch):
    """Real bug this fixes: round4's environment/mockup are grounded in
    whichever round1 scenario is live AT GENERATION TIME (see hr.py's
    _generate_reference_unsafe) - a one-time snapshot, not a live link.
    If HR later promotes a different round1 scenario for the same band,
    round4's reference must be regenerated automatically, or every
    candidate would see test data describing a completely different app
    than the one their own round1 answer was actually about."""
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="First app")
    # _publish_round4_scenario monkeypatches generate_round2_automation_environment/
    # ui_mockup itself for round4's own creation - the call-tracking mock
    # below is installed AFTER, so it only observes what happens next.
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch, band="0-7")

    calls = []

    def fake_env(**kwargs):
        calls.append(kwargs.get("app_description"))
        return {"fields": {"call number": str(len(calls))}, "notes": ""}

    monkeypatch.setattr(llm_service, "generate_round2_automation_environment", fake_env)
    monkeypatch.setattr(llm_service, "generate_round2_automation_ui_mockup", lambda **kwargs: dict(FAKE_UI_MOCKUP))

    # A second round1 scenario for the same band, published but not yet
    # promoted live (the first one is still live) - no resync should
    # fire just from publishing it.
    second_round1 = _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="Second app")
    assert len(calls) == 0  # still not live - no resync yet

    # Promoting it live is what triggers the resync.
    res = client.post(f"/hr/scenarios/{second_round1['id']}/move-to-screening", cookies=_auth(hr_token))
    assert res.status_code == 200
    assert len(calls) == 1

    fresh = client.get(f"/hr/scenarios/{round4['id']}", cookies=_auth(hr_token)).json()
    assert fresh["environment_json"]["fields"]["call number"] == "1"


def test_round4_resync_skipped_while_a_candidate_is_mid_round4(client, monkeypatch):
    """Same "don't change the rules mid-round" guard every other
    round 2 scenario mutation already has (see _require_round2_automation_not_in_progress)
    must also apply here - promoting a new round1 scenario live is an
    action about round 1, but it's exactly what triggers this resync, so
    without the guard a candidate's environment/screens could silently
    change out from under them mid-round-4."""
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="First app")
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, band="0-7")
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch, band="0-7")

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    calls = []
    monkeypatch.setattr(llm_service, "generate_round2_automation_environment", lambda **kwargs: calls.append(1) or {"fields": {}, "notes": ""})
    monkeypatch.setattr(llm_service, "generate_round2_automation_ui_mockup", lambda **kwargs: dict(FAKE_UI_MOCKUP))

    second_round1 = _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="Second app")
    res = client.post(f"/hr/scenarios/{second_round1['id']}/move-to-screening", cookies=_auth(hr_token))
    assert res.status_code == 200
    assert len(calls) == 0  # resync must not run while candidate1 is mid-round-4

    # Genuinely unchanged - not just "no new call happened to differ".
    fresh = client.get(f"/hr/scenarios/{round4['id']}", cookies=_auth(hr_token)).json()
    assert fresh["environment_json"] == FAKE_ENVIRONMENT
    assert fresh["ui_mockup_json"] == FAKE_UI_MOCKUP


def test_round4_instructions_editable_regardless_of_status(client, monkeypatch):
    """Unlike round1/2's title/description (draft-only, see
    test_updating_one_scenarios_time_limit_never_touches_another and
    friends in test_round1.py), round4 has no fixed reference answer
    gating a review-before-publish step, so this is editable on the
    live scenario directly."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch)

    fresh = client.get(f"/hr/scenarios/{round4['id']}", cookies=_auth(hr_token)).json()
    assert fresh["status"] == "published"
    assert fresh["is_live"] is True

    res = client.patch(
        f"/hr/scenarios/{round4['id']}/round4-instructions",
        json={"title": "New title", "description": "New instructions for the candidate."},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 200
    assert res.json()["title"] == "New title"
    assert res.json()["description"] == "New instructions for the candidate."
    assert res.json()["status"] == "published"  # unaffected


def test_round4_instructions_blocked_while_a_candidate_is_mid_round4(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4)
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    res = client.patch(
        f"/hr/scenarios/{round4['id']}/round4-instructions",
        json={"title": "New title", "description": "New instructions."},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 409

    fresh = client.get(f"/hr/scenarios/{round4['id']}", cookies=_auth(hr_token)).json()
    assert fresh["title"] != "New title"


def test_round4_instructions_validates_round_number_and_requires_hr(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch)
    round1 = _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="7+", title="A round 1 scenario")

    res = client.patch(
        f"/hr/scenarios/{round1['id']}/round4-instructions",
        json={"title": "x", "description": "y"},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 400

    res = client.patch("/hr/scenarios/999999/round4-instructions", json={"title": "x", "description": "y"}, cookies=_auth(hr_token))
    assert res.status_code == 404

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.patch(
        f"/hr/scenarios/{round4['id']}/round4-instructions",
        json={"title": "x", "description": "y"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 403


def test_round4_regenerate_reference_works_on_a_live_scenario(client, monkeypatch):
    """Round 1/2's regenerate-reference stays draft-only (it's rewriting
    a fixed answer key candidates get scored against), but round 4
    scenarios go live immediately on creation and have no such answer
    key - this needs to work on the live scenario or it could never be
    used again after creation."""
    from app.services import llm_service
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch)

    fresh = client.get(f"/hr/scenarios/{round4['id']}", cookies=_auth(hr_token)).json()
    assert fresh["status"] == "published"

    monkeypatch.setattr(llm_service, "generate_round2_automation_environment", lambda **kwargs: {"fields": {"regenerated": "yes"}, "notes": ""})
    res = client.post(f"/hr/scenarios/{round4['id']}/regenerate-reference", cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["environment_json"]["fields"]["regenerated"] == "yes"


def test_round4_regenerate_reference_blocked_while_a_candidate_is_mid_round4(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4)
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    res = client.post(f"/hr/scenarios/{round4['id']}/regenerate-reference", cookies=_auth(hr_token))
    assert res.status_code == 409


