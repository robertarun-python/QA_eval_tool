"""
Round 3: AI-prompted coding - see
docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md. Not to be
confused with tests/test_round4.py (the renamed automation round).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from .conftest import HR_EMAIL, HR_PASSWORD, FAKE_ROUND3_CODING_REFERENCE, _login, _auth, _publish_scenario


def _create_draft_round3_scenario(client, hr_token, monkeypatch, title="Add two numbers"):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round3_reference", lambda **kwargs: dict(FAKE_ROUND3_CODING_REFERENCE))
    return client.post(
        "/hr/scenarios",
        json={"round_number": 3, "title": title, "description": "desc", "experience_band": "0-7", "time_limit_minutes": 30},
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
    res = client.post("/candidate/round/3/run", json={"stdin": ["2", "3"]}, cookies=_auth(cand_token))
    assert res.status_code == 400

    monkeypatch.setattr(llm_service, "round3_coding_turn", lambda **kwargs: {
        "response_kind": "code_edit", "response_message": "Added the two variables and printed their sum.",
        "code_after": "a = int(input())\nb = int(input())\nprint(a + b)",
    })
    turn_res = client.post("/candidate/round/3/turn", json={"candidate_prompt": "read two ints and print their sum"}, cookies=_auth(cand_token))
    assert turn_res.status_code == 201
    assert turn_res.json()["response_kind"] == "code_edit"
    assert "a + b" in turn_res.json()["code_after"]

    monkeypatch.setattr(execution_service, "run_code", lambda **kwargs: execution_service.ExecutionResult(
        stdout="5\n", stderr="", exit_code=0, timed_out=False, infra_error=False, duration_ms=42,
    ))
    run_res = client.post("/candidate/round/3/run", json={"stdin": ["2", "3"]}, cookies=_auth(cand_token))
    assert run_res.status_code == 201
    assert run_res.json()["stdout"] == "5\n"

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
