"""
Round 3: AI-prompted coding - see
docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md. Not to be
confused with tests/test_round4.py (the renamed automation round).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from .conftest import HR_EMAIL, HR_PASSWORD, FAKE_ROUND3_CODING_REFERENCE, _login, _auth, _publish_scenario


def _create_draft_round3_scenario(client, hr_token, monkeypatch, title="Add two numbers", band="0-7"):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round3_reference", lambda **kwargs: dict(FAKE_ROUND3_CODING_REFERENCE))
    return client.post(
        "/hr/scenarios",
        json={"round_number": 3, "title": title, "description": "desc", "experience_band": band, "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    ).json()


def test_hr_can_edit_a_draft_round3_scenarios_reference_json(client, monkeypatch):
    """Fix 2 (final whole-branch review): ScenarioUpdate.reference_json
    used to be typed list[TestCaseRow], so PATCHing a round-3 scenario's
    reference (a {"test_cases": [...], "expected_approach": "..."} dict)
    422'd before it ever reached the handler. Confirms the dict shape now
    round-trips through PATCH -> GET unchanged."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _create_draft_round3_scenario(client, hr_token, monkeypatch)

    new_reference = {
        "test_cases": [
            {"input": "10 20", "expected_output": "30", "description": "bigger sum"},
        ],
        "expected_approach": "Read two integers, one per line, and print their sum.",
    }
    res = client.patch(
        f"/hr/scenarios/{scenario['id']}",
        json={"reference_json": new_reference},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 200
    assert res.json()["reference_json"] == new_reference

    fetched = client.get(f"/hr/scenarios/{scenario['id']}", cookies=_auth(hr_token)).json()
    assert fetched["reference_json"] == new_reference


def test_hr_editing_round3_reference_json_rejects_wrong_shapes(client, monkeypatch):
    """A round-1/2-shaped list, or a dict missing test_cases/expected_approach,
    must be rejected with a clear 4xx rather than silently corrupting the
    scenario or blowing up later at scoring time."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _create_draft_round3_scenario(client, hr_token, monkeypatch)

    # A round-1/2-shaped list is wrong for round 3.
    res = client.patch(
        f"/hr/scenarios/{scenario['id']}",
        json={"reference_json": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 400

    # A dict missing test_cases is wrong.
    res = client.patch(
        f"/hr/scenarios/{scenario['id']}",
        json={"reference_json": {"expected_approach": "only this"}},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 400

    # Untouched by the rejected attempts above.
    fetched = client.get(f"/hr/scenarios/{scenario['id']}", cookies=_auth(hr_token)).json()
    assert fetched["reference_json"] == dict(FAKE_ROUND3_CODING_REFERENCE)


def test_hr_can_create_and_publish_a_round3_coding_scenario(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Add two numbers")
    assert scenario["round_number"] == 3

    # _publish_scenario returns the pre-publish creation response, not a
    # re-fetch (see test_round1.py's edit_time_limit_after_publish test) -
    # confirm the actual current state via a fresh read rather than
    # trusting that stale dict for status.
    fetched = client.get(f"/hr/scenarios/{scenario['id']}", cookies=_auth(hr_token)).json()
    assert fetched["status"] == "published"
    assert fetched["reference_json"]["test_cases"][0]["expected_output"] == "5"
    assert "expected_approach" in fetched["reference_json"]


def test_round3_coding_full_happy_path(client, monkeypatch):
    from app.services import llm_service, execution_service
    from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2")
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Add two numbers")

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))

    # Round 3 isn't reachable without a language.
    res = client.post("/candidate/round/3/start", json={}, cookies=_auth(cand_token))
    assert res.status_code == 422

    res = client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))
    assert res.status_code == 201
    # Idempotent - a second call with the same body doesn't reset anything.
    res2 = client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))
    assert res2.json()["id"] == res.json()["id"]

    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert state["language"] == "python"
    assert state["turns"] == []

    # Can't run before any code exists.
    res = client.post("/candidate/round/3/run/start", cookies=_auth(cand_token))
    assert res.status_code == 400

    monkeypatch.setattr(llm_service, "round3_coding_turn", lambda **kwargs: {
        "response_kind": "code_edit", "response_message": "Added the two variables and printed their sum.",
        "code_after": "a = int(input())\nb = int(input())\nprint(a + b)",
    })
    turn_res = client.post("/candidate/round/3/turn", json={"candidate_prompt": "read two ints and print their sum"}, cookies=_auth(cand_token))
    assert turn_res.status_code == 201
    assert turn_res.json()["response_kind"] == "code_edit"
    assert "a + b" in turn_res.json()["code_after"]

    # Two DIFFERENT execution_service entry points need mocking here,
    # not one - start_interactive (the candidate's own Run button, a
    # real live subprocess - see InteractiveSession) and run_code (the
    # separate BATCH call scoring_service makes at submit time against
    # HR's fixed reference test suite). Mocking only one and letting the
    # other actually execute would run this test's deliberately-simple
    # candidate code ("a = int(input()); b = int(input())", two separate
    # lines) against FAKE_ROUND3_CODING_REFERENCE's single-line "2 3"
    # input at scoring time and get a real ValueError - not what this
    # test is checking.
    async def fake_start_interactive(language, code):
        session = execution_service.InteractiveSession()
        session.stdout_buffer = "5\n"
        session.exit_code = 0
        session.exited = True
        return session

    monkeypatch.setattr(execution_service, "start_interactive", fake_start_interactive)
    monkeypatch.setattr(execution_service, "run_code", lambda **kwargs: execution_service.ExecutionResult(
        stdout="5\n", stderr="", exit_code=0, timed_out=False, infra_error=False, duration_ms=42,
    ))
    run_res = client.post("/candidate/round/3/run/start", cookies=_auth(cand_token))
    assert run_res.status_code == 201
    assert run_res.json()["stdout"] == "5\n"
    assert run_res.json()["exited"] is True

    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert len(state["turns"]) == 1
    assert len(state["runs"]) == 1

    monkeypatch.setattr(llm_service, "score_round3_coding", lambda **kwargs: {
        "correctness_score": 100, "precision_score": 90, "efficiency_score": 80,
        "independent_judgment_score": 90, "final_score": 90,
        "misses": [], "guardrail_violations": [], "feedback_text": "Great work.",
    })
    submit_res = client.post("/candidate/round/3/submit", cookies=_auth(cand_token))
    assert submit_res.status_code == 201
    assert submit_res.json()["status"] == "submitted"

    # HR's report shows the per-test-case breakdown behind coverage_score
    # (see models.Score.test_results_json), not just the aggregate
    # percentage - same promotion concept_coverage_json already got for
    # round 1 (see test_round1.py's equivalent assertion).
    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_round3 = next(s for s in res.json() if s["round_number"] == 3)
    assert report_round3["score"]["test_results_json"] == [
        {"input": "2 3", "expected_output": "5", "description": "basic sum", "actual_output": "5", "passed": True},
    ]
    # The sub-score breakdown (see models.Score.correctness_score and its
    # siblings) round-trips through the same report endpoint - HR's
    # report renders these as a marks-split breakdown alongside
    # final_score, not just the single blended number.
    assert report_round3["score"]["correctness_score"] == 100
    assert report_round3["score"]["precision_score"] == 90
    assert report_round3["score"]["efficiency_score"] == 80
    assert report_round3["score"]["independent_judgment_score"] == 90


def test_round3_coding_run_is_genuinely_interactive_end_to_end(client, monkeypatch):
    """The whole point of the interactive Run endpoints
    (start/poll/input/stop) - a real subprocess actually waits on its
    own input() call until the candidate sends a real value through
    /run/input, exactly like running the program in a terminal. No
    pre-supplied or guessed stdin anywhere in this flow. Exercises the
    real execution_service.start_interactive (not mocked) end-to-end
    over the actual HTTP endpoints, not just at the service level (see
    test_execution_service.py for that layer)."""
    import time as time_module
    from app.services import llm_service
    from .conftest import CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Echo", band="7+")

    cand_token = _login(client, CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "round3_coding_turn", lambda **kwargs: {
        "response_kind": "code_edit", "response_message": "Echoes whatever the user types.",
        "code_after": "name = input('What is your name? ')\nprint('Hello, ' + name)",
    })
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "read a name and print a greeting"}, cookies=_auth(cand_token))

    start_res = client.post("/candidate/round/3/run/start", cookies=_auth(cand_token))
    assert start_res.status_code == 201
    assert start_res.json()["exited"] is False  # blocked on input(), not guessed/pre-fed anything

    def poll():
        return client.get("/candidate/round/3/run/poll", cookies=_auth(cand_token)).json()

    # Give the real subprocess a moment to actually start and print its
    # prompt - poll a few times rather than a single fixed sleep.
    for _ in range(20):
        state = poll()
        if "What is your name?" in state["stdout"]:
            break
        time_module.sleep(0.1)
    else:
        raise AssertionError("prompt never appeared - process didn't actually run/block on input()")
    assert state["exited"] is False

    input_res = client.post("/candidate/round/3/run/input", json={"line": "Ada"}, cookies=_auth(cand_token))
    assert input_res.status_code == 204

    for _ in range(20):
        state = poll()
        if state["exited"]:
            break
        time_module.sleep(0.1)
    else:
        raise AssertionError("session never exited after real input was sent")
    assert "Hello, Ada" in state["stdout"]
    assert state["exit_code"] == 0

    # Persisted once the poll observed it exited - visible in state
    # history exactly like a batch run, and carries the real typed line.
    full_state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert len(full_state["runs"]) == 1
    assert full_state["runs"][0]["stdin_json"] == ["Ada"]
    assert "Hello, Ada" in full_state["runs"][0]["stdout"]


def test_round3_coding_turn_asking_which_loop_is_correct_gets_refused(client, monkeypatch):
    from app.services import llm_service
    from .conftest import CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="0-7")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="0-7")
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Add two numbers", band="0-7")

    cand_token = _login(client, CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "round3_coding_turn", lambda **kwargs: {
        "response_kind": "refuse",
        "response_message": "I can't make that call for you - tell me specifically what you want (which loop, which data structure, which approach), and I'll write it.",
        "code_after": None,
    })
    res = client.post("/candidate/round/3/turn", json={"candidate_prompt": "which loop is correct here?"}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["response_kind"] == "refuse"
    assert res.json()["code_after"] is None


def test_round3_coding_current_code_threads_between_turns(client, monkeypatch):
    """Verifies THIS APP's own code correctly passes the prior turn's
    code_after as current_code into the next LLM call - not a claim
    about what the (mocked) LLM does with it, which is a prompt-design
    property audited at scoring time instead (see
    test_scoring_service_round3_coding.py's guardrail-violation test)."""
    from app.services import llm_service
    from .conftest import CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Add two numbers", band="7+")

    cand_token = _login(client, CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "round3_coding_turn", lambda **kwargs: {
        "response_kind": "code_edit", "response_message": "ok", "code_after": "a=1",
    })
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "first"}, cookies=_auth(cand_token))

    captured = {}

    def fake_turn(**kwargs):
        captured.update(kwargs)
        return {"response_kind": "code_edit", "response_message": "ok", "code_after": "a=1\nb=2"}

    monkeypatch.setattr(llm_service, "round3_coding_turn", fake_turn)
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "second"}, cookies=_auth(cand_token))

    assert captured["current_code"] == "a=1"
    assert captured["turn_number"] == 2
    assert len(captured["conversation_so_far"]) == 1
    assert captured["conversation_so_far"][0]["candidate_prompt"] == "first"


def test_round3_coding_declared_constructs_persist_and_thread_between_turns(client, monkeypatch):
    """Verifies THIS APP's own code correctly reads the prior turn's
    declared_constructs_json and threads it into the next LLM call - not
    a claim about what the (mocked) LLM does with it, which is
    llm_service's own concern (see test_llm_service_round3_coding.py)."""
    from app.services import llm_service
    from .conftest import CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="7+")

    scenario = _create_draft_round3_scenario(client, hr_token, monkeypatch, title="Find the highest salary", band="7+")
    client.patch(
        f"/hr/scenarios/{scenario['id']}",
        json={"reference_json": {**FAKE_ROUND3_CODING_REFERENCE, "required_constructs": ["collection", "iteration"]}},
        cookies=_auth(hr_token),
    )
    client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))

    cand_token = _login(client, CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    captured_first = {}

    def fake_turn_1(**kwargs):
        captured_first.update(kwargs)
        return {
            "response_kind": "clarify", "response_message": "How do you want to represent that information?",
            "code_after": None, "declared_constructs": {"collection": "a list called salaries"},
        }

    monkeypatch.setattr(llm_service, "round3_coding_turn", fake_turn_1)
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "I need to store the salaries"}, cookies=_auth(cand_token))
    assert captured_first["required_constructs"] == ["collection", "iteration"]
    assert captured_first["declared_constructs"] == {}

    captured_second = {}

    def fake_turn_2(**kwargs):
        captured_second.update(kwargs)
        return {
            "response_kind": "code_edit", "response_message": "Added the loop.",
            "code_after": "for s in salaries: print(s)",
            "declared_constructs": {"collection": "a list called salaries", "iteration": "a for loop"},
        }

    monkeypatch.setattr(llm_service, "round3_coding_turn", fake_turn_2)
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "loop through them"}, cookies=_auth(cand_token))
    # The previous turn's declared state is read back and threaded into
    # the next call - the candidate never has to repeat "a list called
    # salaries" for it to still count as settled.
    assert captured_second["declared_constructs"] == {"collection": "a list called salaries"}

    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert len(state["turns"]) == 2


def test_round3_coding_full_construct_checklist_flow_end_to_end(client, monkeypatch):
    """Drives the REAL llm_service.round3_coding_turn orchestration (only
    _call_claude is mocked, not round3_coding_turn itself) through a
    multi-turn conversation with a required_constructs checklist: an
    instruction that attempts one category vaguely gets asked about it,
    a bundled instruction with two more gaps gets asked about both
    together, and once nothing THIS turn is left vague the turn produces
    code - even without every checklist category ever being declared.
    Proves the router, the engine, the schema, and persistence all wire
    together correctly, not just each piece in isolation."""
    import json as json_module
    from app.services import llm_service
    from .conftest import CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="7+")

    scenario = _create_draft_round3_scenario(client, hr_token, monkeypatch, title="Find the highest salary", band="7+")
    client.patch(
        f"/hr/scenarios/{scenario['id']}",
        json={"reference_json": {**FAKE_ROUND3_CODING_REFERENCE, "required_constructs": ["collection", "iteration", "comparison"]}},
        cookies=_auth(hr_token),
    )
    client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))

    cand_token = _login(client, CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    responses = [
        # Turn 1: attempts "collection" but leaves it vague - the
        # checklist only ever forces clarify for a category THIS turn
        # actually attempted, never one it said nothing about (see
        # round3_construct_engine.decide).
        {
            "response_kind": "clarify", "response_message": "(unused - assembled from category_status)", "code_after": None,
            "category_status": {"collection": {"status": "attempted_but_vague", "neutral_question": "What information do you need to store first?"}},
        },
        # Turn 2: a bundled instruction leaving two gaps.
        {
            "response_kind": "clarify", "response_message": "(unused)", "code_after": None,
            "category_status": {
                "collection": {"status": "declared", "value": "a list called salaries"},
                "iteration": {"status": "attempted_but_vague", "neutral_question": "How will your program work through them, one at a time?"},
                "comparison": {"status": "attempted_but_vague", "neutral_question": "What should determine whether one value replaces another?"},
            },
        },
        # Turn 3: resolves both remaining gaps - everything is now
        # declared, so the engine allows code_edit.
        {
            "response_kind": "code_edit", "response_message": "Added the loop and the comparison.",
            "code_after": "highest = None\nfor s in salaries:\n    if highest is None or s > highest:\n        highest = s\nprint(highest)",
            "category_status": {
                "iteration": {"status": "declared", "value": "a for loop over salaries"},
                "comparison": {"status": "declared", "value": "greater than the current highest"},
            },
        },
    ]

    def fake_call_claude(prompt, max_tokens=4096):
        return json_module.dumps(responses.pop(0))

    monkeypatch.setattr(llm_service, "_call_claude", fake_call_claude)

    r1 = client.post("/candidate/round/3/turn", json={"candidate_prompt": "keep track of the salaries somewhere"}, cookies=_auth(cand_token))
    assert r1.json()["response_kind"] == "clarify"
    assert "What information do you need to store first?" in r1.json()["response_message"]

    r2 = client.post("/candidate/round/3/turn", json={"candidate_prompt": "store the salaries in a list, loop through them, and compare each to the current highest"}, cookies=_auth(cand_token))
    assert r2.json()["response_kind"] == "clarify"
    assert "How will your program work through them, one at a time?" in r2.json()["response_message"]
    assert "What should determine whether one value replaces another?" in r2.json()["response_message"]

    r3 = client.post("/candidate/round/3/turn", json={"candidate_prompt": "use a for loop, and if a salary is greater than the current highest, replace it"}, cookies=_auth(cand_token))
    assert r3.json()["response_kind"] == "code_edit"
    assert "highest = None" in r3.json()["code_after"]

    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert len(state["turns"]) == 3


def test_round3_coding_produces_code_for_a_fully_specified_instruction_even_with_other_categories_still_open(client, monkeypatch):
    """Regression test: a real candidate hit this exact bug - an
    instruction that fully covers input/validation (declaring
    "variable" and "type_conversion") got refused code because
    "comparison"/"arithmetic_operation" (needed for a LATER part of the
    solution) were still open. round3_construct_engine.decide() must
    only force clarify for a category THIS instruction attempted and
    left vague - never for one it hasn't gotten to yet."""
    import json as json_module
    from app.services import llm_service
    from .conftest import CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="7+")

    scenario = _create_draft_round3_scenario(client, hr_token, monkeypatch, title="Odd or even", band="7+")
    client.patch(
        f"/hr/scenarios/{scenario['id']}",
        json={"reference_json": {**FAKE_ROUND3_CODING_REFERENCE, "required_constructs": ["variable", "input", "type_conversion", "comparison"]}},
        cookies=_auth(hr_token),
    )
    client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))

    cand_token = _login(client, CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json_module.dumps({
        "response_kind": "code_edit",
        "response_message": "Added the input, validation, and conversion.",
        "code_after": "while True:\n    raw = input('Please enter the integer: ')\n    try:\n        odd_even = int(raw)\n        break\n    except ValueError:\n        print('Enter integer')",
        # "comparison" is never mentioned - this instruction never
        # touched it, and it must NOT block code_edit for what was
        # actually specified.
        "category_status": {
            "variable": {"status": "declared", "value": "odd_even"},
            "input": {"status": "declared", "value": "input() with a prompt message"},
            "type_conversion": {"status": "declared", "value": "int() inside a validation loop"},
        },
    }))

    res = client.post(
        "/candidate/round/3/turn",
        json={"candidate_prompt": "convert to int and store in odd_even; if it's not a valid integer, print an error and keep asking until it is"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    assert res.json()["response_kind"] == "code_edit"
    assert "int(raw)" in res.json()["code_after"]

    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert state["turns"][0]["response_kind"] == "code_edit"


def test_round3_coding_direct_edit_creates_a_turn_and_persists_declared_constructs(client, monkeypatch):
    from app.services import llm_service
    from .conftest import CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="7+")

    scenario = _create_draft_round3_scenario(client, hr_token, monkeypatch, title="Find the highest salary", band="7+")
    client.patch(
        f"/hr/scenarios/{scenario['id']}",
        json={"reference_json": {**FAKE_ROUND3_CODING_REFERENCE, "required_constructs": ["collection", "iteration"]}},
        cookies=_auth(hr_token),
    )
    client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))

    cand_token = _login(client, CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "round3_syntax_fix", lambda **kwargs: {
        "response_message": "No syntax issues found.",
        "code_after": "salaries = [1, 2, 3]\nhighest = None\nfor s in salaries:\n    if highest is None or s > highest:\n        highest = s\nprint(highest)",
        "declared_constructs": {"collection": "a list called salaries", "iteration": "a for loop over salaries"},
    })
    res = client.post(
        "/candidate/round/3/edit",
        json={"code": "salaries = [1, 2, 3]\nhighest = None\nfor s in salaries:\n    if highest is None or s > highest:\n        highest = s\nprint(highest)"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["response_kind"] == "direct_edit"
    assert body["turn_number"] == 1
    assert "highest = None" in body["code_after"]

    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert len(state["turns"]) == 1
    assert state["turns"][0]["response_kind"] == "direct_edit"

    # A follow-up instruction-based turn threads the direct edit's
    # declared_constructs forward, exactly like it would for any other
    # turn kind.
    captured = {}

    def fake_turn(**kwargs):
        captured.update(kwargs)
        return {"response_kind": "code_edit", "response_message": "ok", "code_after": "salaries = [1, 2, 3]\nprint(max(salaries))", "declared_constructs": kwargs["declared_constructs"]}

    monkeypatch.setattr(llm_service, "round3_coding_turn", fake_turn)
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "use max() instead"}, cookies=_auth(cand_token))
    assert captured["declared_constructs"] == {"collection": "a list called salaries", "iteration": "a for loop over salaries"}


def test_round3_coding_direct_edit_rejects_empty_code(client, monkeypatch):
    from app.services import llm_service
    from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2")
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Add two numbers")

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    res = client.post("/candidate/round/3/edit", json={"code": ""}, cookies=_auth(cand_token))
    assert res.status_code == 422


def test_round3_coding_direct_edit_full_flow_end_to_end(client, monkeypatch):
    """Drives the REAL llm_service.round3_syntax_fix orchestration (only
    _call_claude is mocked, not round3_syntax_fix itself) through pasting
    a complete solution as the very first turn - proving the router, the
    engine's merge_declared, and persistence all wire together, and that
    classification genuinely reads the code rather than trusting a mock's
    say-so."""
    import json as json_module
    from app.services import llm_service
    from .conftest import CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1", band="7+")
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="R2", band="7+")

    scenario = _create_draft_round3_scenario(client, hr_token, monkeypatch, title="Find the highest salary", band="7+")
    client.patch(
        f"/hr/scenarios/{scenario['id']}",
        json={"reference_json": {**FAKE_ROUND3_CODING_REFERENCE, "required_constructs": ["collection", "iteration", "comparison"]}},
        cookies=_auth(hr_token),
    )
    client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))

    cand_token = _login(client, CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/submit", json={"investigation": [{"area": "x"}], "root_cause": "x"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))

    pasted_code = (
        "salaries = [50000, 72000, 61000]\n"
        "highest = None\n"
        "for s in salaries:\n"
        "    if highest is None or s > highest:\n"
        "        highest = s\n"
        "print(highest)"
    )

    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json_module.dumps({
        "response_kind": "direct_edit", "response_message": "No syntax issues found.",
        "code_after": pasted_code,
        "category_status": {
            "collection": {"status": "declared", "value": "a list called salaries"},
            "iteration": {"status": "declared", "value": "a for loop over salaries"},
            "comparison": {"status": "declared", "value": "greater than the current highest"},
        },
    }))

    res = client.post("/candidate/round/3/edit", json={"code": pasted_code}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["response_kind"] == "direct_edit"
    assert res.json()["code_after"] == pasted_code

    state = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()
    assert len(state["turns"]) == 1
    assert state["turns"][0]["candidate_prompt"] == pasted_code

    # Round3TurnOut never exposes declared_constructs_json over the API,
    # so the only way to prove the REAL merge_declared (not a stub)
    # actually persisted the right per-category values is to thread them
    # forward into a follow-up instruction turn and capture what
    # round3_coding_turn was called with - same technique as
    # test_round3_coding_direct_edit_creates_a_turn_and_persists_declared_constructs.
    captured = {}

    def fake_turn(**kwargs):
        captured.update(kwargs)
        return {"response_kind": "code_edit", "response_message": "ok", "code_after": pasted_code, "declared_constructs": kwargs["declared_constructs"]}

    monkeypatch.setattr(llm_service, "round3_coding_turn", fake_turn)
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "use max() instead"}, cookies=_auth(cand_token))
    assert captured["declared_constructs"] == {
        "collection": "a list called salaries",
        "iteration": "a for loop over salaries",
        "comparison": "greater than the current highest",
    }
