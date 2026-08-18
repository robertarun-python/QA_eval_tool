"""
Round 3 is conversational and open-ended: the candidate creates their own
self-titled test cases (no fixed UI/API/DB category, no forced ordering)
and autosaves an in-progress draft per test case - see ARCHITECTURE.md and
routers/candidate.py's round 3 section. Same fake-llm_service approach as
test_round1.py/test_round2.py: never a real Claude call.
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD,
    CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,  # 0-7 band
    CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD,  # 0-7 band, different account for ownership checks
    _login, _auth, _publish_scenario, _publish_round3_scenario, _create_round3_test_case,
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


def _complete_round1_and_2(client, cand_token, monkeypatch):
    # Submitting either round fires a background scoring call (see
    # candidate.py's _score_round1_in_background/_score_round2_in_background)
    # - without mocking these too (separate functions from the reference
    # generators _publish_scenario already mocks), every test that calls
    # this helper was silently making 2 real, paid Claude API calls in a
    # background thread, which is also where the rare "JSONDecodeError"
    # flakiness came from (a real response occasionally not being clean
    # JSON) - not a bug in round 3 itself.
    from app.services import llm_service
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"},
    )
    monkeypatch.setattr(
        llm_service, "score_round2_submission",
        lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"},
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


def test_round3_scenario_generates_environment_and_requires_it_to_publish(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round3_environment", lambda **kwargs: dict(FAKE_ENVIRONMENT))
    monkeypatch.setattr(llm_service, "generate_round3_ui_mockup", lambda **kwargs: dict(FAKE_UI_MOCKUP))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = client.post(
        "/hr/scenarios",
        json={"round_number": 3, "title": "Automation challenge", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
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


def test_round3_ui_mockup_failure_also_prevents_scenario_from_being_usable(client, monkeypatch):
    """_generate_reference's round 3 branch commits both environment_json
    and ui_mockup_json together at the end, in one transaction - so a
    failure in the second call (ui_mockup) rolls back the first
    (environment) too, same observable effect as test_round3_publish_
    blocked_without_environment below: the scenario is excluded from
    list_scenarios entirely, not left half-populated."""
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round3_environment", lambda **kwargs: dict(FAKE_ENVIRONMENT))

    def _blow_up(**kwargs):
        raise ValueError("simulated generation failure")

    monkeypatch.setattr(llm_service, "generate_round3_ui_mockup", _blow_up)

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    try:
        client.post(
            "/hr/scenarios",
            json={"round_number": 3, "title": "Automation challenge", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
            cookies=_auth(hr_token),
        )
    except ValueError:
        pass

    res = client.get("/hr/scenarios", cookies=_auth(hr_token))
    assert res.json() == []


def test_round3_environment_and_mockup_grounded_in_live_round1_scenario(client, monkeypatch):
    """Both generate_round3_environment and generate_round3_ui_mockup
    describe the actual app under test, which lives in round 1's
    scenario, not round 3's own (see hr.py's _generate_reference) - both
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

    monkeypatch.setattr(llm_service, "generate_round3_environment", _capture_env)
    monkeypatch.setattr(llm_service, "generate_round3_ui_mockup", _capture_mockup)

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)

    # No round 1 scenario published yet for this band - falls back to
    # round 3's own description.
    client.post(
        "/hr/scenarios",
        json={"round_number": 3, "title": "R3 fallback", "description": "r3 own description", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    )
    assert env_captured["app_description"] == "r3 own description"
    assert mockup_captured["app_description"] == "r3 own description"

    # Publish a round 1 scenario with a distinct description, then a new
    # round 3 scenario should ground both in THAT, not its own text.
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="Library app")
    all_scenarios = client.get("/hr/scenarios", cookies=_auth(hr_token)).json()
    live_round1_scenario = next(s for s in all_scenarios if s["round_number"] == 1 and s["is_live"])

    client.post(
        "/hr/scenarios",
        json={"round_number": 3, "title": "R3 grounded", "description": "r3 own description", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    )
    assert env_captured["app_description"] == live_round1_scenario["description"]
    assert mockup_captured["app_description"] == live_round1_scenario["description"]


def test_round3_publish_blocked_without_environment(client, monkeypatch):
    from app.services import llm_service

    def _blow_up(**kwargs):
        raise ValueError("simulated generation failure")

    monkeypatch.setattr(llm_service, "generate_round3_environment", _blow_up)

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    # create_scenario's synchronous generation call raises - the scenario
    # row itself was already committed before that, so it still exists as
    # a draft with environment_json=None (same pattern as round 1/2).
    try:
        client.post(
            "/hr/scenarios",
            json={"round_number": 3, "title": "Automation challenge", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
            cookies=_auth(hr_token),
        )
    except ValueError:
        pass

    res = client.get("/hr/scenarios", cookies=_auth(hr_token))
    # Not listed - list_scenarios filters out drafts with no generated content.
    assert res.json() == []


def test_round3_locked_until_rounds_1_and_2_submitted(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_round3_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.get("/candidate/round/3", cookies=_auth(cand_token))
    assert res.status_code == 403


def test_round3_state_requires_start_first(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round3_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, cand_token, monkeypatch)

    res = client.get("/candidate/round/3/state", cookies=_auth(cand_token))
    assert res.status_code == 404

    client.post("/candidate/round/3/start", cookies=_auth(cand_token))
    res = client.get("/candidate/round/3/state", cookies=_auth(cand_token))
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


def test_round3_test_cases_are_freeform_and_independently_addressable(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round3_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, cand_token, monkeypatch)
    client.post("/candidate/round/3/start", cookies=_auth(cand_token))

    tc1 = _create_round3_test_case(client, cand_token, title="Login happy path")
    tc2 = _create_round3_test_case(client, cand_token)  # untitled is allowed
    assert tc1["title"] == "Login happy path"
    assert tc2["title"] is None
    assert tc1["turn_count"] == 0

    res = client.get("/candidate/round/3/state", cookies=_auth(cand_token))
    assert [tc["id"] for tc in res.json()["test_cases"]] == [tc1["id"], tc2["id"]]


def test_round3_turn_is_scoped_to_its_test_case_with_no_turn_cap(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round3_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round3_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, cand_token, monkeypatch)
    client.post("/candidate/round/3/start", cookies=_auth(cand_token))

    tc1 = _create_round3_test_case(client, cand_token, title="Login happy path")
    tc2 = _create_round3_test_case(client, cand_token, title="Login negative case")

    # Nonexistent/foreign test case id -> 404.
    res = client.post("/candidate/round/3/turn", json={"test_case_id": 999999, "candidate_prompt": "go"}, cookies=_auth(cand_token))
    assert res.status_code == 404

    # No cap - keep sending turns well past the old default cap of 8.
    for expected_turn_number in range(1, 11):
        res = client.post("/candidate/round/3/turn", json={"test_case_id": tc1["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))
        assert res.status_code == 201
        assert res.json()["turn_number"] == expected_turn_number
        assert res.json()["test_case_id"] == tc1["id"]
        assert res.json()["model_response"]["observed_result"] == FAKE_TURN_RESPONSE["observed_result"]
        assert "code" not in res.json()["model_response"]  # never sent to the candidate - see Round3ExecutionStep

    # A different test case has its own independent turn numbering.
    res = client.post("/candidate/round/3/turn", json={"test_case_id": tc2["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["turn_number"] == 1


def test_round3_turn_rejects_a_malformed_llm_response_without_corrupting_state(client, monkeypatch):
    """A response that's valid JSON but doesn't match Round3TurnResponse
    (wrong-case status, missing observed_result) used to sail past the
    only check in round3_respond (isinstance(result, dict)), get
    persisted, and then permanently 500 every later read of this
    candidate's round 3 state - including the very next one the frontend
    makes after sending this same message. Patching _call_claude (the
    lowest-level seam) rather than round3_respond itself, so this
    actually exercises the real parsing + validation path."""
    import json
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round3_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, cand_token, monkeypatch)
    client.post("/candidate/round/3/start", cookies=_auth(cand_token))
    tc = _create_round3_test_case(client, cand_token, title="Login happy path")

    bad_response = json.dumps({
        "response_text": "Here's what happened.",
        "steps": [{"description": "Did a thing", "status": "pass"}],
        "status": "Pass",  # schema requires exactly "pass"/"fail"/"partial"
        # observed_result missing entirely
    })
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: bad_response)

    res = client.post(
        "/candidate/round/3/turn",
        json={"test_case_id": tc["id"], "candidate_prompt": "Try logging in"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 502
    assert "trouble responding" in res.json()["detail"]

    # Nothing was persisted - state is still readable and empty, not
    # permanently broken.
    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token))
    assert state.status_code == 200
    assert state.json()["turns"] == []

    # A retry with a well-formed response just works.
    monkeypatch.setattr(llm_service, "round3_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))
    res = client.post(
        "/candidate/round/3/turn",
        json={"test_case_id": tc["id"], "candidate_prompt": "Try logging in"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201


def test_round3_environment_generation_rejects_malformed_shape(client, monkeypatch):
    """Same class of gap as the turn-response one above, one step earlier
    in the pipeline: generate_round3_environment only checked for a
    'fields' key, not that it actually matched Round3EnvironmentOut
    (fields: dict[str, str]) - a nested object as a field value is valid
    JSON but the wrong shape, and used to only fail once a candidate's
    Round3StateOut read hit it live."""
    import json
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    bad_response = json.dumps({"fields": {"API base URL": {"nested": "not a string"}}})
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: bad_response)

    res = client.post(
        "/hr/scenarios",
        json={"round_number": 3, "title": "Automation challenge", "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 502

    # No half-populated scenario left behind (same rollback-on-failure
    # behavior as test_round3_ui_mockup_failure_also_prevents_scenario_from_being_usable).
    res = client.get("/hr/scenarios", cookies=_auth(hr_token))
    assert res.json() == []


def test_round3_draft_autosave_round_trips_and_is_ownership_checked(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round3_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round3_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, cand_token, monkeypatch)
    client.post("/candidate/round/3/start", cookies=_auth(cand_token))
    tc = _create_round3_test_case(client, cand_token, title="Login happy path")

    res = client.patch(
        f"/candidate/round/3/test-case/{tc['id']}/draft",
        json={"draft_prompt": "Log in with the test account and verify..."},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 204

    res = client.get("/candidate/round/3/state", cookies=_auth(cand_token))
    saved = next(t for t in res.json()["test_cases"] if t["id"] == tc["id"])
    assert saved["draft_prompt"] == "Log in with the test account and verify..."

    # Sending a turn clears the draft server-side.
    client.post("/candidate/round/3/turn", json={"test_case_id": tc["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))
    res = client.get("/candidate/round/3/state", cookies=_auth(cand_token))
    saved = next(t for t in res.json()["test_cases"] if t["id"] == tc["id"])
    assert saved["draft_prompt"] == ""

    # A second candidate (also on round 3) can't write to the first
    # candidate's test case.
    from .conftest import CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD
    cand2_token = _login(client, CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD)
    _complete_round1_and_2(client, cand2_token, monkeypatch)
    client.post("/candidate/round/3/start", cookies=_auth(cand2_token))
    res = client.patch(
        f"/candidate/round/3/test-case/{tc['id']}/draft",
        json={"draft_prompt": "hijack attempt"},
        cookies=_auth(cand2_token),
    )
    assert res.status_code == 404


def test_round3_submit_requires_at_least_one_test_case_with_a_turn(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round3_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))
    monkeypatch.setattr(llm_service, "score_round3_conversation", lambda **kwargs: dict(FAKE_SCORE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round3_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, cand_token, monkeypatch)
    client.post("/candidate/round/3/start", cookies=_auth(cand_token))

    # No test cases at all.
    res = client.post("/candidate/round/3/submit", cookies=_auth(cand_token))
    assert res.status_code == 400

    # A test case with no turns still isn't enough.
    tc = _create_round3_test_case(client, cand_token, title="Login happy path")
    res = client.post("/candidate/round/3/submit", cookies=_auth(cand_token))
    assert res.status_code == 400

    client.post("/candidate/round/3/turn", json={"test_case_id": tc["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))
    res = client.post("/candidate/round/3/submit", cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["content"] is None


def test_round3_full_session_scored_and_visible_to_hr(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round3_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))
    monkeypatch.setattr(llm_service, "score_round3_conversation", lambda **kwargs: dict(FAKE_SCORE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="Search box")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round3_scenario(client, hr_token, monkeypatch, title="Automate the search feature")

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, cand_token, monkeypatch)
    client.post("/candidate/round/3/start", cookies=_auth(cand_token))

    tc1 = _create_round3_test_case(client, cand_token, title="Search returns matching results")
    tc2 = _create_round3_test_case(client, cand_token, title="Search with no matches")
    client.post("/candidate/round/3/turn", json={"test_case_id": tc1["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/turn", json={"test_case_id": tc1["id"], "candidate_prompt": "go again"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/turn", json={"test_case_id": tc2["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token))

    res = client.post("/candidate/round/3/submit", cookies=_auth(cand_token))
    assert res.status_code == 201
    submission_id = res.json()["id"]

    # Background scoring already ran (TestClient runs BackgroundTasks synchronously).
    res = client.get("/candidate/submissions", cookies=_auth(cand_token))
    scored = next(s for s in res.json() if s["id"] == submission_id)
    assert scored["status"] == "scored"
    assert "score" not in scored  # same candidate-facing rule as every other round

    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    round3 = next(r for r in candidate_row["rounds"] if r["round_number"] == 3)
    assert round3["final_score"] == 68

    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_round3 = next(s for s in res.json() if s["round_number"] == 3)
    assert len(report_round3["test_cases"]) == 2
    assert len(report_round3["conversation_turns"]) == 3
    by_test_case = {}
    for t in report_round3["conversation_turns"]:
        by_test_case.setdefault(t["test_case_id"], []).append(t)
    assert len(by_test_case[tc1["id"]]) == 2
    assert len(by_test_case[tc2["id"]]) == 1


# ---- Trial feature: on-demand code-snippet rendering of a turn (see
# candidate.py's GET /round/3/turn/{id}/code, llm_service.
# generate_round3_code_snippet) ----

def test_round3_code_snippet_generates_for_the_owner(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round3_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round3_scenario(client, hr_token, monkeypatch)

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, cand_token, monkeypatch)
    client.post("/candidate/round/3/start", cookies=_auth(cand_token))
    tc = _create_round3_test_case(client, cand_token, title="Login happy path")
    turn = client.post(
        "/candidate/round/3/turn", json={"test_case_id": tc["id"], "candidate_prompt": "go"}, cookies=_auth(cand_token)
    ).json()

    captured = {}

    def _fake_snippet(**kwargs):
        captured.update(kwargs)
        return "driver.get('https://example.test/login')\n# ... captures what was observed, no assertions"

    monkeypatch.setattr(llm_service, "generate_round3_code_snippet", _fake_snippet)

    res = client.get(f"/candidate/round/3/turn/{turn['id']}/code?language=python", cookies=_auth(cand_token))
    assert res.status_code == 200
    body = res.json()
    assert body["language"] == "python"
    assert "driver.get" in body["code"]
    # Generated from the turn's OWN already-recorded trace, not fresh input.
    assert captured["steps"] == FAKE_TURN_RESPONSE["steps"]
    assert captured["observed_result"] == FAKE_TURN_RESPONSE["observed_result"]
    assert captured["language"] == "python"


def test_round3_code_snippet_rejects_invalid_language_and_foreign_turn(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "round3_respond", lambda **kwargs: dict(FAKE_TURN_RESPONSE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2)
    _publish_round3_scenario(client, hr_token, monkeypatch)

    cand1_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, cand1_token, monkeypatch)
    client.post("/candidate/round/3/start", cookies=_auth(cand1_token))
    tc = _create_round3_test_case(client, cand1_token, title="Login happy path")
    turn = client.post(
        "/candidate/round/3/turn", json={"test_case_id": tc["id"], "candidate_prompt": "go"}, cookies=_auth(cand1_token)
    ).json()

    # Invalid language.
    res = client.get(f"/candidate/round/3/turn/{turn['id']}/code?language=cobol", cookies=_auth(cand1_token))
    assert res.status_code == 400

    # A different candidate, with round 3 unlocked in their own right, still
    # can't fetch candidate 1's turn as code - isolates the ownership check
    # from the round-gating check (which would also block an un-unlocked
    # candidate2, but for the wrong reason).
    cand2_token = _login(client, CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD)
    _complete_round1_and_2(client, cand2_token, monkeypatch)
    client.post("/candidate/round/3/start", cookies=_auth(cand2_token))
    res = client.get(f"/candidate/round/3/turn/{turn['id']}/code?language=python", cookies=_auth(cand2_token))
    assert res.status_code == 404

    # Nonexistent turn.
    res = client.get("/candidate/round/3/turn/999999/code?language=python", cookies=_auth(cand1_token))
    assert res.status_code == 404


def test_generate_round3_code_snippet_strips_accidental_code_fences(monkeypatch):
    """The prompt tells the model not to wrap the snippet in markdown code
    fences, but models sometimes do it anyway out of habit - this is the
    one part of the feature that's deterministic Python, not LLM judgment,
    so it gets a direct unit test rather than going through the API."""
    from app.services import llm_service

    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: "```python\ndriver.get('https://x')\nprint(driver.title)\n```")
    code = llm_service.generate_round3_code_snippet(
        test_case_title="Login happy path",
        steps=[{"description": "Navigated to the login page", "status": "pass"}],
        observed_result="The login page loaded.",
        language="python",
    )
    assert code == "driver.get('https://x')\nprint(driver.title)"
    assert "```" not in code
