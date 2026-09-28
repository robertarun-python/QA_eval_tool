"""
The simulated-candidate tester (tools/simulate_candidates.py) with scripted models - no AI calls. Its
judge must catch every kind of failure it claims to (a judge that never fires proves nothing), and a
well-behaved assistant must come out clean.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.services import execution_service, llm_service, round2_typist
from app.services.practice_engine import practice_run, reference

ROOT = Path(__file__).resolve().parent.parent
spec_ = importlib.util.spec_from_file_location("simulate_candidates", ROOT / "tools" / "simulate_candidates.py")
sim = importlib.util.module_from_spec(spec_)
sys.modules["simulate_candidates"] = sim  # dataclasses look their module up
spec_.loader.exec_module(sim)

SPEC = json.loads((Path(__file__).parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())
PANEL = reference.reference_panel(SPEC)
CASE = {"title": "Sign in", "steps": "Log in\nCheck the signed-in user", "test_data": "testuser@library.test", "expected_result": "Test User"}


def _script(*moves):
    moves = list(moves)
    return lambda prompt: (moves.pop(0) if moves else {"done": True}, 0.0)


def _assistant(monkeypatch, *replies):
    replies = list(replies)
    monkeypatch.setattr(llm_service, "_call_claude_json", lambda *a, **k: replies.pop(0) if len(replies) > 1 else replies[0])
    ok = execution_service.ExecutionResult(stdout="PASS: all steps and checks passed", stderr="", exit_code=0,
                                           timed_out=False, infra_error=False, duration_ms=1)
    monkeypatch.setattr(practice_run, "run", lambda *a, **k: ok)


def _findings(t):
    return {f["check"].split()[0] for f in sim.judge(t, sim.data_values(PANEL), lambda lang, code: None)}


def test_a_well_behaved_assistant_comes_out_clean(monkeypatch):
    s1 = [{"step": "Log in as the test account", "missing": ""}]
    s2 = s1 + [{"step": "Check the signed-in user is Test User", "missing": ""}]
    _assistant(monkeypatch, {"reply": "Got it: log in with the test account.", "steps": s1, "code": None},
               {"reply": "Added the check. Shall I generate the code?", "steps": s2, "code": None},
               {"reply": "Here it is.", "steps": s2,
                "code": 'login("testuser@library.test")\nprint("PASS: Check the signed-in user is Test User")\n'})
    t = sim.converse("drip", "python", CASE, SPEC, PANEL, _script(
        {"say": "Log in as the test account"}, {"say": "Check the signed-in user is Test User"},
        {"say": "Generate the code"}, {"run": True, "say": ""}, {"done": True}), 1.0)
    assert _findings(t) == set(), sim.judge(t, sim.data_values(PANEL), lambda lang, code: None)
    assert t.runs and t.runs[0]["status"] == "passed"


def test_the_judge_catches_standins_lost_steps_missing_code_and_hints(monkeypatch):
    s1 = [{"step": "Log in as the test account", "missing": ""}, {"step": "Borrow the book", "missing": "which book"}]
    hint = next(iter(sim.data_values(PANEL)))
    _assistant(monkeypatch, {"reply": "Noted.", "steps": s1, "code": None},
               {"reply": f"Should it be {hint}?", "steps": s1[:1], "code": None})  # blocked -> a stand-in
    t = sim.converse("drip", "python", CASE, SPEC, PANEL, _script(
        {"say": "Log in as the test account, then borrow the book"}, {"say": "Generate the code"}), 1.0)
    found = _findings(t)
    assert {"J1", "J3"} <= found, found
    # the judge on its own: a reply naming a data value, and a step gone
    t2 = sim.Transcript("drip", "python", "x", turns=[
        {"say": "Log in, borrow the book", "reply": "ok", "steps": s1},
        {"say": "Check the due date", "reply": f"The book is {hint}.", "steps": s1[:1]}])
    assert {"J2", "J6"} <= _findings(t2)


def test_a_wrong_value_that_passes_or_crashes_is_caught():
    t = sim.Transcript("wrong_data", "java", "x", runs=[{"after_turn": 3, "status": "passed", "summary": "PASS: all", "stderr_head": ""}])
    assert "J5" in _findings(t)
    t.runs[0].update(status="failed", summary="FAIL: step 2 - Enter the loan account: no such element")
    assert "J5" not in _findings(t)
    t.runs[0].update(status="error", summary="")
    assert "J7" in _findings(t)


def test_every_persona_has_a_style_and_the_prompt_formats():
    for name, style in sim.PERSONAS.items():
        assert len(style) > 40, name
    text = sim.SIM_PROMPT.format(persona="p", case="c", reference=sim.candidate_view(PANEL), conversation="", last_run="")
    assert '"say"' in text and "Test accounts" in text and "TABLE" in text


def test_the_test_account_is_no_hint_once_the_candidate_logs_in():
    acct = "testuser@library.test Test@123"
    t = sim.Transcript("drip", "python", "x", turns=[{"say": "After login, open Loans", "reply": "I'll log in with Test@123.", "steps": []}])
    assert "J6" not in {f["check"].split()[0] for f in sim.judge(t, {"Test@123"}, lambda lang, code: None, acct)}
    t.turns[0]["say"] = "Open Loans"
    assert "J6" in {f["check"].split()[0] for f in sim.judge(t, {"Test@123"}, lambda lang, code: None, acct)}


def test_an_unreadable_answer_from_the_simulated_candidate_is_asked_again():
    class Msg:
        def __init__(self, text):
            self.content = [type("B", (), {"type": "text", "text": text})()]
            self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()
    answers = [Msg("I think I'll say hello"), Msg('{"say": "Log in", "run": false, "done": false}')]
    client = type("C", (), {"messages": type("M", (), {"create": lambda self, **k: answers.pop(0)})()})()
    decision, _ = sim._sim_call(client, "p")
    assert decision["say"] == "Log in"
