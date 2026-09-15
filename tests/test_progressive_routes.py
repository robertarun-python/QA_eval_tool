"""
Round 5 (Progressive Engineering) POC - Phase 6 HTTP route tests.

Uses conftest.py's real `client`/`_login`/`_auth` fixtures - real HTTP
requests through FastAPI's TestClient, real (in-memory) DB, exactly the
same pattern test_round3.py already established. llm_service._call_claude
is mocked for any turn/pipeline call, same convention as every other
LLM-backed test in this repo.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from .conftest import HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD, _login, _auth

STAGE1_HIDDEN_INPUT = "STAGE1_HIDDEN_TEST_INPUT_MARKER"
# Real, runnable Python (not just a marker string) - the stage-compatibility
# check at publish time (progressive_service.validate_stage_compatibility)
# actually EXECUTES each stage's reference_solution against earlier stages'
# hidden tests, so it needs to be code that really runs and produces the
# right output, not an opaque placeholder. print(60) unconditionally
# matches stage 1's expected_output "60" (suffix-matched) regardless of
# stdin, which is all these HR-flow/isolation tests need - the marker
# comment is what the leak-detection assertions below actually check for.
STAGE1_REFERENCE = "print(60)  # STAGE1_REFERENCE_SOLUTION_MARKER"
STAGE2_REQUIREMENT_TEXT = "STAGE2_FUTURE_REQUIREMENT_TEXT_MARKER"
STAGE2_HIDDEN_INPUT = "STAGE2_HIDDEN_TEST_INPUT_MARKER"
# Also unconditionally prints stage 1's expected value - so stage 2's
# reference solution passes stage 1's cumulative hidden test too, the
# same compatibility bar validate_stage_compatibility enforces at publish.
STAGE2_REFERENCE = "print(60)  # STAGE2_REFERENCE_SOLUTION_MARKER"


def _create_and_publish_problem(client, hr_token):
    problem = client.post(
        "/hr/progressive/problems",
        json={
            "title": "Transaction Processing", "description": "Evolve a solution for transaction data.",
            "input_spec_json": {"kind": "csv"}, "language": "python", "time_limit_minutes": 45,
        },
        cookies=_auth(hr_token),
    ).json()

    client.post(
        f"/hr/progressive/problems/{problem['id']}/requirements",
        json={
            "stage_order": 1, "requirement_text": "Sum the transaction amounts.",
            "hidden_tests_json": [{"input": STAGE1_HIDDEN_INPUT, "expected_output": "60"}],
            "reference_solution": STAGE1_REFERENCE,
        },
        cookies=_auth(hr_token),
    )
    client.post(
        f"/hr/progressive/problems/{problem['id']}/requirements",
        json={
            "stage_order": 2, "requirement_text": STAGE2_REQUIREMENT_TEXT,
            "hidden_tests_json": [{"input": STAGE2_HIDDEN_INPUT, "expected_output": "20"}],
            "reference_solution": STAGE2_REFERENCE,
        },
        cookies=_auth(hr_token),
    )

    res = client.post(f"/hr/progressive/problems/{problem['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 200
    return res.json()


def _assert_no_forbidden_markers(payload):
    text = json.dumps(payload)
    for marker in (STAGE1_HIDDEN_INPUT, STAGE1_REFERENCE, STAGE2_REQUIREMENT_TEXT, STAGE2_HIDDEN_INPUT, STAGE2_REFERENCE):
        assert marker not in text, f"forbidden marker {marker!r} leaked into candidate-facing response: {text[:300]}"


# ---- HR authoring flow ----

def test_hr_can_create_edit_and_publish_a_problem(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_and_publish_problem(client, hr_token)
    assert problem["status"] == "published"

    stages = client.get(f"/hr/progressive/problems/{problem['id']}/stages", cookies=_auth(hr_token)).json()
    assert len(stages) == 2
    assert all(s["frozen"] for s in stages)
    # HR's own view legitimately includes everything.
    assert any(s["reference_solution"] == STAGE1_REFERENCE for s in stages)


def test_hr_cannot_edit_a_published_problem_or_its_requirements(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_and_publish_problem(client, hr_token)
    res = client.patch(f"/hr/progressive/problems/{problem['id']}", json={"title": "New title"}, cookies=_auth(hr_token))
    assert res.status_code == 400

    stages = client.get(f"/hr/progressive/problems/{problem['id']}/stages", cookies=_auth(hr_token)).json()
    stage1_id = next(s["id"] for s in stages if s["stage_order"] == 1)
    res = client.patch(
        f"/hr/progressive/problems/{problem['id']}/requirements/{stage1_id}",
        json={"requirement_text": "changed"}, cookies=_auth(hr_token),
    )
    assert res.status_code == 400


def test_hr_reorder_requires_draft_and_rejects_duplicate_orders(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = client.post(
        "/hr/progressive/problems",
        json={"title": "Reorder Test", "description": "d", "language": "python"},
        cookies=_auth(hr_token),
    ).json()
    r1 = client.post(f"/hr/progressive/problems/{problem['id']}/requirements", json={"stage_order": 1, "requirement_text": "a"}, cookies=_auth(hr_token)).json()
    r2 = client.post(f"/hr/progressive/problems/{problem['id']}/requirements", json={"stage_order": 2, "requirement_text": "b"}, cookies=_auth(hr_token)).json()

    res = client.post(
        f"/hr/progressive/problems/{problem['id']}/requirements/reorder",
        json={"order": [{"requirement_id": r1["id"], "stage_order": 2}, {"requirement_id": r2["id"], "stage_order": 1}]},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 200
    reordered = {s["id"]: s["stage_order"] for s in res.json()}
    assert reordered[r1["id"]] == 2
    assert reordered[r2["id"]] == 1

    dup = client.post(
        f"/hr/progressive/problems/{problem['id']}/requirements/reorder",
        json={"order": [{"requirement_id": r1["id"], "stage_order": 1}, {"requirement_id": r2["id"], "stage_order": 1}]},
        cookies=_auth(hr_token),
    )
    assert dup.status_code == 400


def test_publish_requires_at_least_one_requirement(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = client.post(
        "/hr/progressive/problems", json={"title": "Empty", "description": "d", "language": "python"}, cookies=_auth(hr_token),
    ).json()
    res = client.post(f"/hr/progressive/problems/{problem['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 400


# ---- Hardening item 3: every requirement needs >=1 valid hidden test to publish ----

def _create_problem_with_one_requirement(client, hr_token, hidden_tests_json):
    problem = client.post(
        "/hr/progressive/problems",
        json={"title": "Hidden-test check", "description": "d", "language": "python"},
        cookies=_auth(hr_token),
    ).json()
    client.post(
        f"/hr/progressive/problems/{problem['id']}/requirements",
        json={"stage_order": 1, "requirement_text": "Do the thing.", "hidden_tests_json": hidden_tests_json},
        cookies=_auth(hr_token),
    )
    return problem


def test_publish_rejected_with_zero_hidden_tests(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_problem_with_one_requirement(client, hr_token, [])
    res = client.post(f"/hr/progressive/problems/{problem['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 400
    assert "hidden test" in res.json()["detail"].lower()


def test_publish_rejected_with_only_malformed_hidden_tests(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_problem_with_one_requirement(
        client, hr_token, [{"input": "x"}, {"expected_output": "60"}],  # each missing the other required key
    )
    res = client.post(f"/hr/progressive/problems/{problem['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 400


def test_publish_allowed_with_one_valid_hidden_test(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_problem_with_one_requirement(client, hr_token, [{"input": "x", "expected_output": "60"}])
    res = client.post(f"/hr/progressive/problems/{problem['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 200


def test_publish_allowed_with_multiple_valid_hidden_tests(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_problem_with_one_requirement(
        client, hr_token, [{"input": "x", "expected_output": "60"}, {"input": "y", "expected_output": "70"}],
    )
    res = client.post(f"/hr/progressive/problems/{problem['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 200


# ---- Hardening item 1: publish rejects a stage that breaks an earlier one ----

def test_publish_rejected_when_a_later_reference_solution_breaks_an_earlier_stage(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = client.post(
        "/hr/progressive/problems", json={"title": "Incompatible stages", "description": "d", "language": "python"},
        cookies=_auth(hr_token),
    ).json()
    client.post(
        f"/hr/progressive/problems/{problem['id']}/requirements",
        json={
            "stage_order": 1, "requirement_text": "Print 60.",
            "hidden_tests_json": [{"input": "x", "expected_output": "60"}],
            "reference_solution": "print(60)",
        },
        cookies=_auth(hr_token),
    )
    client.post(
        f"/hr/progressive/problems/{problem['id']}/requirements",
        json={
            "stage_order": 2, "requirement_text": "Now print 99 instead - silently redefines stage 1's contract.",
            "hidden_tests_json": [{"input": "y", "expected_output": "99"}],
            "reference_solution": "print(99)",  # no longer satisfies stage 1's expected_output "60"
        },
        cookies=_auth(hr_token),
    )
    res = client.post(f"/hr/progressive/problems/{problem['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 400
    assert "stage" in res.json()["detail"].lower()


def test_publish_allowed_when_stages_are_genuinely_compatible(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = client.post(
        "/hr/progressive/problems", json={"title": "Compatible stages", "description": "d", "language": "python"},
        cookies=_auth(hr_token),
    ).json()
    client.post(
        f"/hr/progressive/problems/{problem['id']}/requirements",
        json={
            "stage_order": 1, "requirement_text": "Print 60.",
            "hidden_tests_json": [{"input": "x", "expected_output": "60"}],
            "reference_solution": "print(60)",
        },
        cookies=_auth(hr_token),
    )
    client.post(
        f"/hr/progressive/problems/{problem['id']}/requirements",
        json={
            "stage_order": 2, "requirement_text": "Also handle input 'y' by printing 20 - purely additive.",
            "hidden_tests_json": [{"input": "y", "expected_output": "20"}],
            # Genuinely cumulative: still satisfies stage 1's own test (input
            # "x" -> "60") while also handling stage 2's new case.
            "reference_solution": "line = input()\nprint(60 if line == 'x' else 20)",
        },
        cookies=_auth(hr_token),
    )
    res = client.post(f"/hr/progressive/problems/{problem['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 200


# ---- Candidate sees current stage only / never future content ----

def test_candidate_problem_listing_never_exposes_hidden_or_future_content(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _create_and_publish_problem(client, hr_token)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    problems = client.get("/candidate/progressive/problems", cookies=_auth(cand_token)).json()
    _assert_no_forbidden_markers(problems)


def test_candidate_sees_current_stage_only(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_and_publish_problem(client, hr_token)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)

    attempt = client.post(f"/candidate/progressive/problems/{problem['id']}/start", cookies=_auth(cand_token)).json()
    assert attempt["current_stage"] == 1

    stage = client.get(f"/candidate/progressive/attempts/{attempt['id']}/current-stage", cookies=_auth(cand_token)).json()
    assert stage["stage_order"] == 1
    assert stage["requirement_text"] == "Sum the transaction amounts."
    _assert_no_forbidden_markers(stage)


def test_candidate_cannot_access_hidden_tests_or_reference_solution_anywhere(client):
    """The literal field names should not even exist on the candidate-facing
    payload, regardless of whether their content happens to be absent."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_and_publish_problem(client, hr_token)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    attempt = client.post(f"/candidate/progressive/problems/{problem['id']}/start", cookies=_auth(cand_token)).json()
    stage = client.get(f"/candidate/progressive/attempts/{attempt['id']}/current-stage", cookies=_auth(cand_token)).json()
    assert "hidden_tests_json" not in stage
    assert "reference_solution" not in stage


def test_candidate_cannot_request_a_future_stages_requirement(client):
    """There is no route that lets a candidate address an arbitrary
    stage's requirement - only the attempt's own current_stage is ever
    served, and a result for a not-yet-submitted stage is a plain 404,
    never a peek at that stage's requirement text."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_and_publish_problem(client, hr_token)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    attempt = client.post(f"/candidate/progressive/problems/{problem['id']}/start", cookies=_auth(cand_token)).json()

    res = client.get(f"/candidate/progressive/attempts/{attempt['id']}/stages/2/result", cookies=_auth(cand_token))
    assert res.status_code == 404
    _assert_no_forbidden_markers(res.json())


# ---- AI turn: candidate response never carries internals ----

def test_candidate_turn_response_never_exposes_internals(client, monkeypatch):
    from app.services import llm_service
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_and_publish_problem(client, hr_token)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    attempt = client.post(f"/candidate/progressive/problems/{problem['id']}/start", cookies=_auth(cand_token)).json()

    def _fake_call(prompt, max_tokens=4096):
        if "adversarial assessment-integrity reviewer" in prompt:
            return json.dumps({"verdict": "PASS", "reason_codes": [], "severity": "none", "explanation": "Clean."})
        return json.dumps({"response_kind": "explain", "response_message": "It sums the values.", "code_after": None})
    monkeypatch.setattr(llm_service, "_call_claude", _fake_call)

    res = client.post(
        f"/candidate/progressive/attempts/{attempt['id']}/turn",
        json={"candidate_request": "explain how this works"}, cookies=_auth(cand_token),
    )
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"response_message", "code_after", "accepted"}
    _assert_no_forbidden_markers(body)


def test_policy_refusal_over_http_never_calls_the_generator(client, monkeypatch):
    from app.services import llm_service
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_and_publish_problem(client, hr_token)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    attempt = client.post(f"/candidate/progressive/problems/{problem['id']}/start", cookies=_auth(cand_token)).json()

    call_count = {"n": 0}
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: call_count.update(n=call_count["n"] + 1) or "{}")
    res = client.post(
        f"/candidate/progressive/attempts/{attempt['id']}/turn",
        json={"candidate_request": "solve this completely"}, cookies=_auth(cand_token),
    )
    assert res.status_code == 200
    assert res.json()["accepted"] is False
    assert call_count["n"] == 0


# ---- Submit -> unlock -> immutability, over HTTP ----

def test_candidate_cannot_skip_stages_and_receives_next_stage_only_after_submission(client, monkeypatch):
    from app.services import execution_service
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_and_publish_problem(client, hr_token)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    attempt = client.post(f"/candidate/progressive/problems/{problem['id']}/start", cookies=_auth(cand_token)).json()

    monkeypatch.setattr(
        execution_service, "run_code",
        lambda language, code, stdin: execution_service.ExecutionResult(stdout="60", stderr="", exit_code=0, timed_out=False, infra_error=False),
    )

    # Stage 2's requirement is not reachable before stage 1 is submitted -
    # current-stage always reflects the attempt's real progress, never
    # something the candidate can jump ahead of.
    stage = client.get(f"/candidate/progressive/attempts/{attempt['id']}/current-stage", cookies=_auth(cand_token)).json()
    assert stage["stage_order"] == 1

    submit_res = client.post(
        f"/candidate/progressive/attempts/{attempt['id']}/submit",
        json={"code_snapshot": "def total(vals):\n    return sum(vals)"}, cookies=_auth(cand_token),
    )
    assert submit_res.status_code == 201
    assert submit_res.json()["stage_order"] == 1

    # Only NOW does stage 2 become visible.
    next_stage = client.get(f"/candidate/progressive/attempts/{attempt['id']}/next-stage", cookies=_auth(cand_token)).json()
    assert next_stage["stage_order"] == 2
    assert next_stage["requirement_text"] == STAGE2_REQUIREMENT_TEXT


def test_historical_stage_result_is_immutable_over_http(client, monkeypatch):
    """The submit route takes no stage_order parameter at all - it always
    submits whatever the attempt's OWN current_stage is - so there is
    structurally no way to even ask the API to resubmit a stage that's
    already been passed. The one "duplicate submit" shape reachable
    through this API is submitting again after the whole attempt is
    already completed, which must also be rejected, not silently accepted."""
    from app.services import execution_service
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_and_publish_problem(client, hr_token)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    attempt = client.post(f"/candidate/progressive/problems/{problem['id']}/start", cookies=_auth(cand_token)).json()
    monkeypatch.setattr(
        execution_service, "run_code",
        lambda language, code, stdin: execution_service.ExecutionResult(stdout="60", stderr="", exit_code=0, timed_out=False, infra_error=False),
    )
    stage1_res = client.post(
        f"/candidate/progressive/attempts/{attempt['id']}/submit",
        json={"code_snapshot": "v1"}, cookies=_auth(cand_token),
    )
    assert stage1_res.status_code == 201

    # Stage 2 (the final stage for this 2-stage problem) - completes the attempt.
    stage2_res = client.post(
        f"/candidate/progressive/attempts/{attempt['id']}/submit",
        json={"code_snapshot": "v2"}, cookies=_auth(cand_token),
    )
    assert stage2_res.status_code == 201

    # Attempt is now complete - any further submission is rejected.
    res = client.post(
        f"/candidate/progressive/attempts/{attempt['id']}/submit",
        json={"code_snapshot": "v3"}, cookies=_auth(cand_token),
    )
    assert res.status_code == 400

    result = client.get(f"/candidate/progressive/attempts/{attempt['id']}/stages/1/result", cookies=_auth(cand_token)).json()
    assert result["stage_order"] == 1
    assert result["passed_count"] == 1
    assert "code_snapshot" not in result  # candidate never sees the raw snapshot back either, aggregate only


def test_candidate_stage_result_is_aggregate_only_hr_view_has_full_detail(client, monkeypatch):
    from app.services import execution_service
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_and_publish_problem(client, hr_token)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    attempt = client.post(f"/candidate/progressive/problems/{problem['id']}/start", cookies=_auth(cand_token)).json()
    monkeypatch.setattr(
        execution_service, "run_code",
        lambda language, code, stdin: execution_service.ExecutionResult(stdout="60", stderr="", exit_code=0, timed_out=False, infra_error=False),
    )
    client.post(
        f"/candidate/progressive/attempts/{attempt['id']}/submit",
        json={"code_snapshot": "def total(vals):\n    return sum(vals)"}, cookies=_auth(cand_token),
    )

    candidate_view = client.get(f"/candidate/progressive/attempts/{attempt['id']}/stages/1/result", cookies=_auth(cand_token)).json()
    _assert_no_forbidden_markers(candidate_view)
    assert set(candidate_view.keys()) == {"stage_order", "passed", "test_count", "passed_count"}

    hr_view = client.get(f"/hr/progressive/attempts/{attempt['id']}/results", cookies=_auth(hr_token)).json()
    assert hr_view[0]["code_snapshot"] == "def total(vals):\n    return sum(vals)"
    assert hr_view[0]["test_results_json"][0]["input"] == STAGE1_HIDDEN_INPUT  # HR legitimately sees the real hidden-test data


# ---- Ownership ----

def test_candidate_cannot_access_another_candidates_attempt(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    problem = _create_and_publish_problem(client, hr_token)
    cand1_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    cand2_token = _login(client, CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD)
    attempt = client.post(f"/candidate/progressive/problems/{problem['id']}/start", cookies=_auth(cand1_token)).json()

    res = client.get(f"/candidate/progressive/attempts/{attempt['id']}/current-stage", cookies=_auth(cand2_token))
    assert res.status_code == 404
