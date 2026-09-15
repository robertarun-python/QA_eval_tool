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
    CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD,  # 0-7 band, different account for ownership checks
    _login, _auth, _publish_scenario, _publish_round4_scenario, _create_round4_test_case,
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
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/2/submit",
        json={"investigation": [{"area": "Reproduced the issue"}], "root_cause": "..."},
        cookies=_auth(cand_token),
    )
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Coding challenge")
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "solve it"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/submit", cookies=_auth(cand_token))


def test_round4_scenario_generates_environment_and_requires_it_to_publish(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round4_environment", lambda **kwargs: dict(FAKE_ENVIRONMENT))
    monkeypatch.setattr(llm_service, "generate_round4_ui_mockup", lambda **kwargs: dict(FAKE_UI_MOCKUP))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = client.post(
        "/hr/scenarios",
        json={"round_number": 4, "title": "Automation challenge", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    ).json()
    assert scenario["reference_json"] is None
    assert scenario["environment_json"]["fields"]["Test account email"] == "qa.tester@example.com"
    assert scenario["ui_mockup_json"]["screens"][0]["name"] == "Login"

    res = client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["status"] == "published"

    res = client.get("/hr/scenarios", cookies=_auth(hr_token))
    assert scenario["id"] in [s["id"] for s in res.json()]


def test_round4_ui_mockup_failure_also_prevents_scenario_from_being_usable(client, monkeypatch):
    """_generate_reference's round 4 branch commits both environment_json
    and ui_mockup_json together at the end, in one transaction - so a
    failure in the second call (ui_mockup) rolls back the first
    (environment) too, same observable effect as test_round4_publish_
    blocked_without_environment below: the scenario is excluded from
    list_scenarios entirely, not left half-populated."""
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round4_environment", lambda **kwargs: dict(FAKE_ENVIRONMENT))

    def _blow_up(**kwargs):
        raise ValueError("simulated generation failure")

    monkeypatch.setattr(llm_service, "generate_round4_ui_mockup", _blow_up)

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    try:
        client.post(
            "/hr/scenarios",
            json={"round_number": 4, "title": "Automation challenge", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
            cookies=_auth(hr_token),
        )
    except ValueError:
        pass

    res = client.get("/hr/scenarios", cookies=_auth(hr_token))
    assert res.json() == []


def test_round4_environment_and_mockup_grounded_in_live_round1_scenario(client, monkeypatch):
    """Both generate_round4_environment and generate_round4_ui_mockup
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

    monkeypatch.setattr(llm_service, "generate_round4_environment", _capture_env)
    monkeypatch.setattr(llm_service, "generate_round4_ui_mockup", _capture_mockup)

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)

    # No round 1 scenario published yet for this band - falls back to
    # round 4's own description.
    client.post(
        "/hr/scenarios",
        json={"round_number": 4, "title": "R3 fallback", "description": "r3 own description", "experience_band": "0-7", "time_limit_minutes": 30},
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
        json={"round_number": 4, "title": "R3 grounded", "description": "r3 own description", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    )
    assert env_captured["app_description"] == live_round1_scenario["description"]
    assert mockup_captured["app_description"] == live_round1_scenario["description"]


def test_round4_publish_blocked_without_environment(client, monkeypatch):
    from app.services import llm_service

    def _blow_up(**kwargs):
        raise ValueError("simulated generation failure")

    monkeypatch.setattr(llm_service, "generate_round4_environment", _blow_up)

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    # create_scenario's synchronous generation call raises - the scenario
    # row itself was already committed before that, so it still exists as
    # a draft with environment_json=None (same pattern as round 1/2).
    try:
        client.post(
            "/hr/scenarios",
            json={"round_number": 4, "title": "Automation challenge", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
            cookies=_auth(hr_token),
        )
    except ValueError:
        pass

    res = client.get("/hr/scenarios", cookies=_auth(hr_token))
    # Not listed - list_scenarios filters out drafts with no generated content.
    assert res.json() == []


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
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)

    res = client.get("/candidate/round/4/state", cookies=_auth(cand_token))
    assert res.status_code == 404

    client.post("/candidate/round/4/start", cookies=_auth(cand_token))
    res = client.get("/candidate/round/4/state", cookies=_auth(cand_token))
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


def test_round4_test_cases_are_freeform_and_independently_addressable(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))

    tc1 = _create_round4_test_case(client, cand_token, title="Login happy path")
    tc2 = _create_round4_test_case(client, cand_token)  # untitled is allowed
    assert tc1["title"] == "Login happy path"
    assert tc2["title"] is None
    assert tc1["turn_count"] == 0

    res = client.get("/candidate/round/4/state", cookies=_auth(cand_token))
    assert [tc["id"] for tc in res.json()["test_cases"]] == [tc1["id"], tc2["id"]]


def test_round4_turn_is_scoped_to_its_test_case_with_no_turn_cap(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round4_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))

    tc1 = _create_round4_test_case(client, cand_token, title="Login happy path")
    tc2 = _create_round4_test_case(client, cand_token, title="Login negative case")

    # Nonexistent/foreign test case id -> 404.
    res = client.post("/candidate/round/4/turn", json={"test_case_id": 999999, "candidate_prompt": "go"}, cookies=_auth(cand_token))
    assert res.status_code == 404

    # No cap - keep sending turns well past the old default cap of 8.
    for expected_turn_number in range(1, 11):
        res = client.post("/candidate/round/4/turn", json={"test_case_id": tc1["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))
        assert res.status_code == 201
        assert res.json()["turn_number"] == expected_turn_number
        assert res.json()["test_case_id"] == tc1["id"]
        assert res.json()["model_response"]["observed_result"] == FAKE_TURN_RESPONSE["observed_result"]
        assert "code" not in res.json()["model_response"]  # never sent to the candidate - see Round4ExecutionStep

    # A different test case has its own independent turn numbering.
    res = client.post("/candidate/round/4/turn", json={"test_case_id": tc2["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["turn_number"] == 1


def test_round4_turn_rejects_a_malformed_llm_response_without_corrupting_state(client, monkeypatch):
    """A response that's valid JSON but doesn't match Round4TurnResponse
    (wrong-case status, missing observed_result) used to sail past the
    only check in round4_respond (isinstance(result, dict)), get
    persisted, and then permanently 500 every later read of this
    candidate's round 4 state - including the very next one the frontend
    makes after sending this same message. Patching _call_claude (the
    lowest-level seam) rather than round4_respond itself, so this
    actually exercises the real parsing + validation path."""
    import json
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))
    tc = _create_round4_test_case(client, cand_token, title="Login happy path")

    bad_response = json.dumps({
        "response_text": "Here's what happened.",
        "steps": [{"description": "Did a thing", "status": "pass"}],
        "status": "Pass",  # schema requires exactly "pass"/"fail"/"partial"
        # observed_result missing entirely
    })
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: bad_response)

    res = client.post(
        "/candidate/round/4/turn",
        json={"test_case_id": tc["id"], "candidate_prompt": "Try logging in"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 502
    assert "trouble responding" in res.json()["detail"]

    # Nothing was persisted - state is still readable and empty, not
    # permanently broken.
    state = client.get("/candidate/round/4/state", cookies=_auth(cand_token))
    assert state.status_code == 200
    assert state.json()["turns"] == []

    # A retry with a well-formed response just works.
    monkeypatch.setattr(llm_service, "round4_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))
    res = client.post(
        "/candidate/round/4/turn",
        json={"test_case_id": tc["id"], "candidate_prompt": "Try logging in"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201


def test_round4_forces_a_flaw_when_the_first_checkable_turn_comes_back_clean(client, monkeypatch):
    """round4_partial_response.txt asks the model to make the first turn
    with a genuine checkable outcome wrong on its own - verified live to
    not reliably comply, twice in a row. This is the deterministic
    backstop: llm_service._round4_needs_forced_flaw detects an
    all-"pass"-so-far test case and round4_respond issues a second,
    narrower call (round4_force_flaw.txt) to revise it. Patches
    _call_claude (the lowest-level seam) so this exercises the real
    two-call path, not just round4_respond's own return value."""
    import json
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))
    tc = _create_round4_test_case(client, cand_token, title="Login happy path")

    clean_response = json.dumps(dict(FAKE_TURN_RESPONSE))  # status "pass" - nothing for the candidate to catch
    forced_response = json.dumps({
        "response_text": "Logged in and landed on the dashboard.",
        "steps": [{"description": "Entered credentials and submitted", "status": "pass"}],
        "observed_result": "Dashboard loaded showing the member as logged out, despite the login call succeeding.",
        "status": "pass",
    })
    calls = []

    def _fake_call_claude(prompt, max_tokens=4096):
        calls.append(prompt)
        return forced_response if len(calls) == 2 else clean_response

    monkeypatch.setattr(llm_service, "_call_claude", _fake_call_claude)

    res = client.post(
        "/candidate/round/4/turn",
        json={"test_case_id": tc["id"], "candidate_prompt": "Log in with the test account and tell me if it worked"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    assert len(calls) == 2  # the clean first attempt, then the forced-flaw follow-up
    assert res.json()["model_response"]["observed_result"] == (
        "Dashboard loaded showing the member as logged out, despite the login call succeeding."
    )


def test_round4_does_not_force_a_flaw_once_an_earlier_turn_already_has_one(client, monkeypatch):
    """The early-mistake requirement is per test case, not per turn -
    once any turn has already come back non-"pass", later clean turns in
    the same test case must NOT trigger the forced-flaw follow-up call."""
    import json
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))
    tc = _create_round4_test_case(client, cand_token, title="Login happy path")

    flawed_first_turn = json.dumps({
        **FAKE_TURN_RESPONSE,
        "status": "fail",
        "steps": [{"description": "Entered credentials and submitted", "status": "fail", "detail": "HTTP 500"}],
    })
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: flawed_first_turn)
    res = client.post(
        "/candidate/round/4/turn",
        json={"test_case_id": tc["id"], "candidate_prompt": "Log in with the test account"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    assert res.json()["model_response"]["status"] == "fail"

    # Turn 2 comes back clean on the model's own first attempt - since
    # turn 1 already has a flaw, this must NOT trigger a second call.
    calls = []

    def _fake_call_claude(prompt, max_tokens=4096):
        calls.append(prompt)
        return json.dumps(dict(FAKE_TURN_RESPONSE))

    monkeypatch.setattr(llm_service, "_call_claude", _fake_call_claude)
    res = client.post(
        "/candidate/round/4/turn",
        json={"test_case_id": tc["id"], "candidate_prompt": "Retry the login"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    assert len(calls) == 1  # no forced-flaw follow-up needed
    assert res.json()["model_response"]["status"] == "pass"


def test_round4_environment_generation_rejects_malformed_shape(client, monkeypatch):
    """Same class of gap as the turn-response one above, one step earlier
    in the pipeline: generate_round4_environment only checked for a
    'fields' key, not that it actually matched Round4EnvironmentOut
    (fields: dict[str, str]) - a nested object as a field value is valid
    JSON but the wrong shape, and used to only fail once a candidate's
    Round4StateOut read hit it live."""
    import json
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    bad_response = json.dumps({"fields": {"API base URL": {"nested": "not a string"}}})
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: bad_response)

    res = client.post(
        "/hr/scenarios",
        json={"round_number": 4, "title": "Automation challenge", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 502

    # No half-populated scenario left behind (same rollback-on-failure
    # behavior as test_round4_ui_mockup_failure_also_prevents_scenario_from_being_usable).
    res = client.get("/hr/scenarios", cookies=_auth(hr_token))
    assert res.json() == []


def test_round4_draft_autosave_round_trips_and_is_ownership_checked(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round4_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))
    tc = _create_round4_test_case(client, cand_token, title="Login happy path")

    res = client.patch(
        f"/candidate/round/4/test-case/{tc['id']}/draft",
        json={"draft_prompt": "Log in with the test account and verify..."},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 204

    res = client.get("/candidate/round/4/state", cookies=_auth(cand_token))
    saved = next(t for t in res.json()["test_cases"] if t["id"] == tc["id"])
    assert saved["draft_prompt"] == "Log in with the test account and verify..."

    # Sending a turn clears the draft server-side.
    client.post("/candidate/round/4/turn", json={"test_case_id": tc["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))
    res = client.get("/candidate/round/4/state", cookies=_auth(cand_token))
    saved = next(t for t in res.json()["test_cases"] if t["id"] == tc["id"])
    assert saved["draft_prompt"] == ""

    # A second candidate (also on round 4) can't write to the first
    # candidate's test case.
    from .conftest import CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD
    cand2_token = _login(client, CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand2_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand2_token))
    res = client.patch(
        f"/candidate/round/4/test-case/{tc['id']}/draft",
        json={"draft_prompt": "hijack attempt"},
        cookies=_auth(cand2_token),
    )
    assert res.status_code == 404


def test_round4_submit_requires_at_least_one_test_case_with_a_turn(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round4_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))
    monkeypatch.setattr(llm_service, "score_round4_conversation", lambda **kwargs: dict(FAKE_SCORE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))

    # No test cases at all.
    res = client.post("/candidate/round/4/submit", cookies=_auth(cand_token))
    assert res.status_code == 400

    # A test case with no turns still isn't enough.
    tc = _create_round4_test_case(client, cand_token, title="Login happy path")
    res = client.post("/candidate/round/4/submit", cookies=_auth(cand_token))
    assert res.status_code == 400

    client.post("/candidate/round/4/turn", json={"test_case_id": tc["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))
    res = client.post("/candidate/round/4/submit", cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["content"] is None


def test_round4_full_session_scored_and_visible_to_hr(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round4_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))
    monkeypatch.setattr(llm_service, "score_round4_conversation", lambda **kwargs: dict(FAKE_SCORE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="Search box")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch, title="Automate the search feature")

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))

    tc1 = _create_round4_test_case(client, cand_token, title="Search returns matching results")
    tc2 = _create_round4_test_case(client, cand_token, title="Search with no matches")
    client.post("/candidate/round/4/turn", json={"test_case_id": tc1["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))
    client.post("/candidate/round/4/turn", json={"test_case_id": tc1["id"], "candidate_prompt": "go again"}, cookies=_auth(cand_token))
    client.post("/candidate/round/4/turn", json={"test_case_id": tc2["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))

    res = client.post("/candidate/round/4/submit", cookies=_auth(cand_token))
    assert res.status_code == 201
    submission_id = res.json()["id"]

    # Background scoring already ran (TestClient runs BackgroundTasks synchronously).
    res = client.get("/candidate/submissions", cookies=_auth(cand_token))
    scored = next(s for s in res.json() if s["id"] == submission_id)
    assert scored["status"] == "scored"
    assert "score" not in scored  # same candidate-facing rule as every other round

    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    round4 = next(r for r in candidate_row["rounds"] if r["round_number"] == 4)
    assert round4["final_score"] == 68

    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_round4 = next(s for s in res.json() if s["round_number"] == 4)
    assert len(report_round4["test_cases"]) == 2
    assert len(report_round4["conversation_turns"]) == 3
    by_test_case = {}
    for t in report_round4["conversation_turns"]:
        by_test_case.setdefault(t["test_case_id"], []).append(t)
    assert len(by_test_case[tc1["id"]]) == 2
    assert len(by_test_case[tc2["id"]]) == 1


# ---- Trial feature: on-demand code-snippet rendering of a turn (see
# candidate.py's GET /round/4/turn/{id}/code, llm_service.
# generate_round4_code_snippet) ----

def test_round4_code_snippet_generates_for_the_owner(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round4_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))
    tc = _create_round4_test_case(client, cand_token, title="Login happy path")
    turn = client.post(
        "/candidate/round/4/turn", json={"test_case_id": tc["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token)
    ).json()

    captured = {}

    def _fake_snippet(**kwargs):
        captured.update(kwargs)
        return "driver.get('https://example.test/login')\n# ... captures what was observed, no assertions"

    monkeypatch.setattr(llm_service, "generate_round4_code_snippet", _fake_snippet)

    res = client.get(f"/candidate/round/4/turn/{turn['id']}/code?language=python", cookies=_auth(cand_token))
    assert res.status_code == 200
    body = res.json()
    assert body["language"] == "python"
    assert "driver.get" in body["code"]
    # Generated from the turn's OWN already-recorded trace, not fresh input.
    assert captured["steps"] == FAKE_TURN_RESPONSE["steps"]
    assert captured["observed_result"] == FAKE_TURN_RESPONSE["observed_result"]
    assert captured["language"] == "python"


def test_round4_code_snippet_is_persisted_not_regenerated_on_repeat_views(client, monkeypatch):
    """The LLM isn't deterministic - without server-side persistence,
    revisiting the same turn's code (or switching back to a language
    already viewed) could show meaningfully different code each time,
    which defeats the point of it being a stable re-rendering of one
    already-made decision. A second call for the same (turn, language)
    must return the exact same code without calling the LLM again."""
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round4_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))
    tc = _create_round4_test_case(client, cand_token, title="Login happy path")
    turn = client.post(
        "/candidate/round/4/turn", json={"test_case_id": tc["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token)
    ).json()

    call_count = {"n": 0}

    def _fake_snippet(**kwargs):
        call_count["n"] += 1
        return f"# generated version {call_count['n']}"

    monkeypatch.setattr(llm_service, "generate_round4_code_snippet", _fake_snippet)

    first = client.get(f"/candidate/round/4/turn/{turn['id']}/code?language=python", cookies=_auth(cand_token)).json()
    second = client.get(f"/candidate/round/4/turn/{turn['id']}/code?language=python", cookies=_auth(cand_token)).json()
    assert first["code"] == second["code"] == "# generated version 1"
    assert call_count["n"] == 1  # the LLM was only ever called once

    # A different language for the SAME turn is a genuinely separate
    # generation - not served from the python cache entry.
    third = client.get(f"/candidate/round/4/turn/{turn['id']}/code?language=java", cookies=_auth(cand_token)).json()
    assert third["code"] == "# generated version 2"
    assert call_count["n"] == 2


def test_round4_code_snippet_rechecks_the_cache_after_acquiring_the_lock(client, monkeypatch):
    """round4_turn_code's check-then-generate-then-persist sequence used
    to have no locking, so two concurrent requests for the same (turn,
    language) - e.g. the candidate opening the same turn's code view in
    two tabs - could both see the cache empty and both call the LLM,
    whichever commits last silently winning. Verified here with a
    deterministic stand-in for the race rather than real threads: a
    real thread-based version of this test was flaky, because this test
    DB's shared single SQLite connection (StaticPool, needed so the
    in-memory DB persists across sessions - see conftest.py) isn't safe
    for genuinely concurrent multi-threaded access, independent of
    whether the app's own locking is correct.

    Simulates another request having already generated and committed a
    value for this exact (turn, language) WHILE this request was
    waiting to acquire _round4_code_lock, by writing it directly to the
    DB as a side effect of entering the lock. The generator must then
    never run - this is exactly the re-check round4_turn_code does
    right after acquiring the lock (see its db.refresh(turn) call)."""
    import app.database as database_module
    from app.models import ConversationTurn
    from app.routers import candidate as candidate_router
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round4_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))
    tc = _create_round4_test_case(client, cand_token, title="Login happy path")
    turn = client.post(
        "/candidate/round/4/turn", json={"test_case_id": tc["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token)
    ).json()

    real_lock = candidate_router._round4_code_lock(turn["id"])

    class _LockThatSimulatesAConcurrentWriter:
        def __enter__(self):
            real_lock.acquire()
            db = database_module.SessionLocal()
            row = db.get(ConversationTurn, turn["id"])
            row.generated_code_json = {"python": "# written by a 'concurrent' request"}
            db.commit()
            db.close()
            return self

        def __exit__(self, *exc):
            real_lock.release()
            return False

    monkeypatch.setattr(candidate_router, "_round4_code_lock", lambda turn_id: _LockThatSimulatesAConcurrentWriter())

    def _must_not_be_called(**kwargs):
        raise AssertionError(
            "generate_round4_code_snippet ran - the recheck under the lock should have found the cache already populated"
        )

    monkeypatch.setattr(llm_service, "generate_round4_code_snippet", _must_not_be_called)

    res = client.get(f"/candidate/round/4/turn/{turn['id']}/code?language=python", cookies=_auth(cand_token))
    assert res.status_code == 200
    assert res.json()["code"] == "# written by a 'concurrent' request"


def test_round4_code_snippet_rejects_invalid_language_and_foreign_turn(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round4_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand1_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand1_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand1_token))
    tc = _create_round4_test_case(client, cand1_token, title="Login happy path")
    turn = client.post(
        "/candidate/round/4/turn", json={"test_case_id": tc["id"], "candidate_prompt": "go"}, cookies=_auth(cand1_token)
    ).json()

    # Invalid language.
    res = client.get(f"/candidate/round/4/turn/{turn['id']}/code?language=cobol", cookies=_auth(cand1_token))
    assert res.status_code == 400

    # A different candidate, with round 4 unlocked in their own right, still
    # can't fetch candidate 1's turn as code - isolates the ownership check
    # from the round-gating check (which would also block an un-unlocked
    # candidate2, but for the wrong reason).
    cand2_token = _login(client, CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand2_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand2_token))
    res = client.get(f"/candidate/round/4/turn/{turn['id']}/code?language=python", cookies=_auth(cand2_token))
    assert res.status_code == 404

    # Nonexistent turn.
    res = client.get("/candidate/round/4/turn/999999/code?language=python", cookies=_auth(cand1_token))
    assert res.status_code == 404


def test_generate_round4_code_snippet_strips_accidental_code_fences(monkeypatch):
    """The prompt tells the model not to wrap the snippet in markdown code
    fences, but models sometimes do it anyway out of habit - this is the
    one part of the feature that's deterministic Python, not LLM judgment,
    so it gets a direct unit test rather than going through the API."""
    from app.services import llm_service

    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: "```python\ndriver.get('https://x')\nprint(driver.title)\n```")
    code = llm_service.generate_round4_code_snippet(
        test_case_title="Login happy path",
        steps=[{"description": "Navigated to the login page", "status": "pass"}],
        observed_result="The login page loaded.",
        language="python",
    )
    assert code == "driver.get('https://x')\nprint(driver.title)"
    assert "```" not in code


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
    # _publish_round4_scenario monkeypatches generate_round4_environment/
    # ui_mockup itself for round4's own creation - the call-tracking mock
    # below is installed AFTER, so it only observes what happens next.
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch, band="0-7")

    calls = []

    def fake_env(**kwargs):
        calls.append(kwargs.get("app_description"))
        return {"fields": {"call number": str(len(calls))}, "notes": ""}

    monkeypatch.setattr(llm_service, "generate_round4_environment", fake_env)
    monkeypatch.setattr(llm_service, "generate_round4_ui_mockup", lambda **kwargs: dict(FAKE_UI_MOCKUP))

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
    """Same "don't change the rules mid-conversation" guard every other
    round4-config mutation already has (see _require_round4_not_in_progress)
    must also apply here - promoting a new round1 scenario live is an
    action about round 1, but it's exactly what triggers this resync, so
    without the guard a candidate's environment/screens could silently
    change out from under them mid-round-4."""
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="First app")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, band="0-7")
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch, band="0-7")

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))

    calls = []
    monkeypatch.setattr(llm_service, "generate_round4_environment", lambda **kwargs: calls.append(1) or {"fields": {}, "notes": ""})
    monkeypatch.setattr(llm_service, "generate_round4_ui_mockup", lambda **kwargs: dict(FAKE_UI_MOCKUP))

    second_round1 = _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="Second app")
    res = client.post(f"/hr/scenarios/{second_round1['id']}/move-to-screening", cookies=_auth(hr_token))
    assert res.status_code == 200
    assert len(calls) == 0  # resync must not run while candidate1 is mid-round-4

    # Genuinely unchanged - not just "no new call happened to differ".
    fresh = client.get(f"/hr/scenarios/{round4['id']}", cookies=_auth(hr_token)).json()
    assert fresh["environment_json"] == FAKE_ENVIRONMENT
    assert fresh["ui_mockup_json"] == FAKE_UI_MOCKUP


def test_round4_config_can_be_changed_on_a_live_scenario(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch)
    assert round4.get("id") is not None

    fresh = client.get(f"/hr/scenarios/{round4['id']}", cookies=_auth(hr_token)).json()
    assert fresh["config_json"] == {}  # nothing set yet - falls back to the global default elsewhere

    res = client.patch(f"/hr/scenarios/{round4['id']}/round4-config", json={"assistance_pct": 45}, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["config_json"]["assistance_pct"] == 45

    fresh = client.get(f"/hr/scenarios/{round4['id']}", cookies=_auth(hr_token)).json()
    assert fresh["config_json"]["assistance_pct"] == 45


def test_round4_config_blocked_while_a_candidate_is_mid_round4(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))

    res = client.patch(f"/hr/scenarios/{round4['id']}/round4-config", json={"assistance_pct": 45}, cookies=_auth(hr_token))
    assert res.status_code == 409
    assert CANDIDATE1_EMAIL in res.json()["detail"] or "1 candidate is" in res.json()["detail"]

    # Genuinely unchanged.
    fresh = client.get(f"/hr/scenarios/{round4['id']}", cookies=_auth(hr_token)).json()
    assert fresh["config_json"] == {}


def test_round4_config_not_blocked_by_a_different_round4_scenario(client, monkeypatch):
    """Scoped to the exact scenario, not band-wide like the time-limit
    block - a candidate mid-round-1 or -2 hasn't touched round 4's own
    behavior at all yet, so it shouldn't block editing it."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))  # mid-round-1 only, never reached round 4

    res = client.patch(f"/hr/scenarios/{round4['id']}/round4-config", json={"assistance_pct": 45}, cookies=_auth(hr_token))
    assert res.status_code == 200


def test_round4_config_validates_bounds_and_round_number(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch)
    round1 = _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="7+", title="A round 1 scenario")

    res = client.patch(f"/hr/scenarios/{round4['id']}/round4-config", json={"assistance_pct": 5}, cookies=_auth(hr_token))
    assert res.status_code == 422
    res = client.patch(f"/hr/scenarios/{round4['id']}/round4-config", json={"assistance_pct": 99}, cookies=_auth(hr_token))
    assert res.status_code == 422

    res = client.patch(f"/hr/scenarios/{round1['id']}/round4-config", json={"assistance_pct": 50}, cookies=_auth(hr_token))
    assert res.status_code == 400

    res = client.patch("/hr/scenarios/999999/round4-config", json={"assistance_pct": 50}, cookies=_auth(hr_token))
    assert res.status_code == 404


def test_round4_config_requires_hr(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.patch(f"/hr/scenarios/{round4['id']}/round4-config", json={"assistance_pct": 50}, cookies=_auth(cand_token))
    assert res.status_code == 403


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
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))

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

    monkeypatch.setattr(llm_service, "generate_round4_environment", lambda **kwargs: {"fields": {"regenerated": "yes"}, "notes": ""})
    res = client.post(f"/hr/scenarios/{round4['id']}/regenerate-reference", cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["environment_json"]["fields"]["regenerated"] == "yes"


def test_round4_regenerate_reference_blocked_while_a_candidate_is_mid_round4(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    round4 = _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))

    res = client.post(f"/hr/scenarios/{round4['id']}/regenerate-reference", cookies=_auth(hr_token))
    assert res.status_code == 409


def test_round4_scoring_drops_unsupported_findings_and_restores_their_points(client, monkeypatch):
    """End-to-end version of test_round4_evidence_audit.py's unit tests:
    a structured `findings`-shaped scorer response (see
    prompts/round4_scoring.txt, schemas.Round4Finding) goes through the
    deterministic auditor (round4_evidence_audit.py) before it becomes a
    persisted Score - an unevidenced finding must not survive into
    misses_json, and final_score must be adjusted back up rather than
    silently keeping the LLM's original deduction."""
    from app.services import llm_service
    from app.services.round4_evidence_audit import SEVERITY_WEIGHTS

    observed_result = "Login succeeded and the dashboard loaded."
    fake_score_with_findings = {
        "coverage_score": 72,
        "findings": [
            # No evidence at all - must be dropped, not scored as a weakness.
            {
                "claim": "Search with no matches: never verified an empty-results message appeared",
                "severity": "medium",
                "evidence": [],
            },
            # Cites a quote that's actually in the transcript - must survive.
            {
                "claim": "Search returns matching results: confirmed the page loaded before checking results",
                "severity": "low",
                "evidence": [{"turn": 1, "quote": observed_result}],
            },
        ],
        "final_score": 60,
        "feedback_text": "Solid independent coverage across two distinct test cases.",
    }
    monkeypatch.setattr(llm_service, "round4_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))
    monkeypatch.setattr(llm_service, "score_round4_conversation", lambda **kwargs: dict(fake_score_with_findings))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="Search box")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch, title="Automate the search feature")

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))

    tc1 = _create_round4_test_case(client, cand_token, title="Search returns matching results")
    tc2 = _create_round4_test_case(client, cand_token, title="Search with no matches")
    client.post("/candidate/round/4/turn", json={"test_case_id": tc1["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))
    client.post("/candidate/round/4/turn", json={"test_case_id": tc2["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))

    res = client.post("/candidate/round/4/submit", cookies=_auth(cand_token))
    assert res.status_code == 201

    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    round4_summary = next(r for r in candidate_row["rounds"] if r["round_number"] == 4)
    # 60 (LLM's own number) + the "medium" weight given back for the
    # finding the auditor rejected - see round4_evidence_audit.SEVERITY_WEIGHTS.
    assert round4_summary["final_score"] == 60 + SEVERITY_WEIGHTS["medium"]

    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_round4 = next(s for s in res.json() if s["round_number"] == 4)
    assert report_round4["score"]["misses_json"] == [
        "Search returns matching results: confirmed the page loaded before checking results"
    ]


def test_round4_full_pipeline_rejects_the_actual_hallucinated_findings(client, monkeypatch):
    """Golden regression test for the Round 4 hallucinated-evaluation bug
    actually encountered: replays the exact transcript (see
    tests/fixtures/round4_hallucination_regression.py) that previously
    let an evaluator produce "Welcome label was missing" and "zero
    follow-up questions" findings, through the REAL candidate flow
    (start round 4, create test cases, send turns, submit) and a scorer
    mock that reproduces both hallucinations plus one genuine finding -
    then asserts neither hallucination survives into the persisted
    Score visible to HR. No live Claude call anywhere in this test; see
    test_round4_hallucination_regression.py for the equivalent pure
    (non-HTTP) unit tests of the auditor itself against this same fixture."""
    from app.services import llm_service
    from app.services.round4_evidence_audit import SEVERITY_WEIGHTS
    from .fixtures.round4_hallucination_regression import (
        GENUINE_FOLLOWUP_FINDING,
        HALLUCINATED_WELCOME_LABEL_FINDING,
        HALLUCINATED_ZERO_FOLLOWUP_FINDING,
        TEST_CASES,
    )

    # One real turn_response per fixture turn, returned in the same
    # order the turns are submitted below - so the actual conversation_turns
    # this test creates end up textually identical to the fixture.
    turn_responses = iter(turn["model_response"] for tc in TEST_CASES for turn in tc["turns"])
    monkeypatch.setattr(llm_service, "round4_respond", lambda **kwargs: dict(next(turn_responses)))
    fake_score = {
        "coverage_score": 70,
        "findings": [HALLUCINATED_WELCOME_LABEL_FINDING, HALLUCINATED_ZERO_FOLLOWUP_FINDING, GENUINE_FOLLOWUP_FINDING],
        "final_score": 40,
        "feedback_text": "Automated a meaningful negative-path scenario and pushed back on an unexpected result.",
    }
    monkeypatch.setattr(llm_service, "score_round4_conversation", lambda **kwargs: dict(fake_score))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round4_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))

    for tc in TEST_CASES:
        created = _create_round4_test_case(client, cand_token, title=tc["title"])
        for turn in tc["turns"]:
            res = client.post(
                "/candidate/round/4/turn",
                json={"test_case_id": created["id"], "candidate_prompt": turn["candidate_prompt"]},
                cookies=_auth(cand_token),
            )
            assert res.status_code == 201

    res = client.post("/candidate/round/4/submit", cookies=_auth(cand_token))
    assert res.status_code == 201

    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    round4_summary = next(r for r in candidate_row["rounds"] if r["round_number"] == 4)

    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_round4 = next(s for s in res.json() if s["round_number"] == 4)
    misses = report_round4["score"]["misses_json"]

    assert not any("welcome" in m.lower() for m in misses)
    assert not any("zero follow" in m.lower() for m in misses)
    assert misses == [GENUINE_FOLLOWUP_FINDING["claim"]]
    # 40 (LLM's own number) + medium (welcome, rejected) + high (zero-followup, rejected).
    assert round4_summary["final_score"] == 40 + SEVERITY_WEIGHTS["medium"] + SEVERITY_WEIGHTS["high"]

    # ---- R4 hardening: the deterministic evidence-audit trail (which
    # findings were accepted/rejected and why) is now exposed via this
    # same HR-only report endpoint - see models.Score.evidence_audit /
    # schemas.ScoreOut.evidence_audit. ----
    audit = report_round4["score"]["evidence_audit"]
    assert audit["total_findings"] == 3
    assert audit["supported"] == 1
    assert audit["not_established"] + audit["contradicted"] == 2
    by_claim = {f["finding"]: f for f in audit["findings"]}
    assert by_claim[HALLUCINATED_WELCOME_LABEL_FINDING["claim"]]["evidence_status"] != "SUPPORTED"
    assert by_claim[HALLUCINATED_ZERO_FOLLOWUP_FINDING["claim"]]["evidence_status"] != "SUPPORTED"
    assert by_claim[GENUINE_FOLLOWUP_FINDING["claim"]]["evidence_status"] == "SUPPORTED"

    # Candidate can never reach this data: no candidate-facing endpoint
    # returns a "score" field at all (see schemas.SubmissionOut vs. the
    # HR-only SubmissionReportOut subclass that adds it), and the HR
    # report route itself rejects a candidate token outright.
    res = client.get("/candidate/submissions", cookies=_auth(cand_token))
    round4_candidate_view = next(s for s in res.json() if s["round_number"] == 4)
    assert "score" not in round4_candidate_view

    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(cand_token))
    assert res.status_code == 403
