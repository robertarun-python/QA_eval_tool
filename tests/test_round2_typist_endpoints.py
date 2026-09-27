"""
A Round 2 built on the real practice environment: the candidate's messages go
to the typing assistant (which is never shown the application) and Run goes
to the practice environment, with a test that has gaps shown as incomplete.
Older practice apps keep their own flow. No AI calls, no browser.
"""
import json
from pathlib import Path

from app.routers import candidate as candidate_router
from app.services import execution_service, llm_service
from app.services.practice_engine import practice_run

from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login
from .test_round2_automation import _reach_automation_round, _select

SPEC = json.loads((Path(__file__).parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())


def _environment_round(client, monkeypatch):
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    cand = _reach_automation_round(client, hr, monkeypatch)
    _select(client, cand, (0,))
    monkeypatch.setattr(candidate_router, "_practice_spec", lambda scenario: SPEC)
    return cand


def _generate(client, cand, monkeypatch):
    """The candidate asks for code - the Run button unlocks after the first version."""
    monkeypatch.setattr(llm_service, "_call_claude_json",
                        lambda prompt, max_tokens=None, schema=None: {"reply": "Here's the code for what you said.",
                                                                     "code": "incomplete('log in as jordan')"})
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "Log in as jordan. Generate the code.", "row_index": 0},
                      cookies=_auth(cand))
    assert res.status_code == 201 and res.json()["response_kind"] == "code_edit", res.text


def test_messages_go_to_the_typist_which_never_sees_the_app(client, monkeypatch):
    cand = _environment_round(client, monkeypatch)
    prompts = []

    def model(prompt, max_tokens=None, schema=None):
        prompts.append(prompt)
        return {"reply": "Got it, you want to log in as jordan. Anything else, or shall I generate the code?", "code": None}
    monkeypatch.setattr(llm_service, "_call_claude_json", model)
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "Log in as jordan", "row_index": 0}, cookies=_auth(cand))
    assert res.status_code == 201, res.text
    assert res.json()["response_kind"] == "clarify" and res.json()["code_after"] is None
    assert len(prompts) == 1 and "typing assistant" in prompts[0]
    for secret in ("testuser@library.test", "available_copies", "No copies available", "borrow_book"):
        assert secret not in prompts[0]


def test_run_uses_the_practice_environment_and_a_test_with_gaps_is_incomplete(client, monkeypatch):
    cand = _environment_round(client, monkeypatch)
    _generate(client, cand, monkeypatch)
    used = []

    def fake_run(language, code, spec):
        used.append((language, spec["app_name"]))
        return execution_service.ExecutionResult(stdout="INCOMPLETE: log in as jordan\n", stderr="", exit_code=3, timed_out=False,
                                                 infra_error=False, duration_ms=900)
    monkeypatch.setattr(practice_run, "run", fake_run)
    monkeypatch.setattr(execution_service, "run_code", lambda *a, **k: (_ for _ in ()).throw(AssertionError("old runner used")))
    res = client.post("/candidate/round/2/auto/run", json={"code": "incomplete('log in as jordan')", "row_index": 0}, cookies=_auth(cand))
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "incomplete" and used and used[0][1] == SPEC["app_name"]


def test_an_environment_that_cannot_start_is_an_infrastructure_error_not_the_candidates(client, monkeypatch):
    cand = _environment_round(client, monkeypatch)
    _generate(client, cand, monkeypatch)

    def broken(language, code, spec):
        raise practice_run.EnvironmentUnavailable("browser tools are not installed")
    monkeypatch.setattr(practice_run, "run", broken)
    res = client.post("/candidate/round/2/auto/run", json={"code": "print(1)", "row_index": 0}, cookies=_auth(cand))
    assert res.status_code == 201 and res.json()["infra_error"] is True and res.json()["status"] == "error"
