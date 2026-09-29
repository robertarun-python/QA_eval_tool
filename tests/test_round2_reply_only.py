"""
P1 item 2b (owner-approved 2026-09-28): when the assistant's program passes every check but its reply text
is rejected, only a new reply is asked for - the program is kept exactly as it is - instead of paying for
the whole program again. A failed reply-only redraft falls back to the usual full redraft. Every guard runs
on the final reply, and the spending limits are checked before every call. Scripted models only - no API.
"""
import json
from pathlib import Path

import pytest

from app.services import llm_service, round2_typist
from .test_ai_guardrails import CANDIDATE, make_paid


@pytest.fixture
def paid(tmp_path, monkeypatch):
    return make_paid(tmp_path, monkeypatch)

LEAK = "Should the booking be BK-009?"          # a reply the guard always rejects (a value nobody gave)
GOOD = "Here is the program for the steps you gave."
PROGRAM = 'login()\nincomplete("Log in as jordan")\n'
SAID = "Log in as jordan. Generate the code."


class Model:
    """Scripted replies in order (the last repeats); records every prompt."""
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt, max_tokens=None, schema=None):
        self.prompts.append(prompt)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def _turn(monkeypatch, model, prompt=SAID, conversation=(), language="python", reference=""):
    monkeypatch.setattr(llm_service, "_call_claude_json", model)
    return round2_typist.turn(language, {"title": "Sign in"}, list(conversation), "", prompt, app_reference=reference)


def _is_reply_only(prompt):
    return "<kept_program>" in prompt


def test_valid_code_with_a_rejected_reply_gets_a_reply_only_redraft(monkeypatch):
    model = Model({"reply": LEAK, "code": PROGRAM},
                  {"reply": GOOD, "code": 'something("else")\n'})   # any code in a reply-only answer is ignored
    out = _turn(monkeypatch, model)
    assert len(model.prompts) == 2 and _is_reply_only(model.prompts[1]) and PROGRAM in model.prompts[1]
    assert out["code_after"] == PROGRAM and out["response_message"].startswith(GOOD)
    assert "BK-009" in model.prompts[1]                                  # it's told what was wrong with the reply


def test_a_repeated_or_empty_reply_is_redone_reply_only_too(monkeypatch):
    earlier = [{"candidate_prompt": "x", "response_message": "Here it is."}]
    model = Model({"reply": "Here it is.", "code": PROGRAM}, {"reply": GOOD, "code": None})
    assert _turn(monkeypatch, model, conversation=earlier)["code_after"] == PROGRAM and _is_reply_only(model.prompts[1])
    model = Model({"reply": "", "code": PROGRAM}, {"reply": GOOD, "code": None})
    assert _turn(monkeypatch, model)["response_message"].startswith(GOOD) and len(model.prompts) == 2


def test_a_failed_reply_only_redraft_falls_back_to_a_full_redraft(monkeypatch):
    model = Model({"reply": LEAK, "code": PROGRAM}, {"reply": LEAK, "code": None},       # the reply-only answer leaks too
                  {"reply": GOOD, "code": PROGRAM})
    out = _turn(monkeypatch, model)
    assert len(model.prompts) == 3 and _is_reply_only(model.prompts[1]) and not _is_reply_only(model.prompts[2])
    assert "BK-009" in model.prompts[2]                                  # the usual full-redraft note
    assert out["code_after"] == PROGRAM and out["response_message"].startswith(GOOD)


def test_the_guards_run_on_the_final_reply(monkeypatch):
    out = _turn(monkeypatch, Model({"reply": LEAK, "code": PROGRAM}))   # every draft leaks, reply-only too
    assert "BK-009" not in out["response_message"] and out["code_after"] == PROGRAM
    offered = Model({"reply": LEAK, "code": PROGRAM}, {"reply": "Should I also check the total?", "code": None},
                    {"reply": GOOD, "code": PROGRAM})
    assert "check the total" not in _turn(monkeypatch, offered)["response_message"]


@pytest.mark.parametrize("draft", [
    {"reply": LEAK, "code": 'book("BK-009")\n'},                        # the code itself names an unsaid value
    {"reply": LEAK, "code": None},                                        # no code at all
])
def test_the_normal_redraft_is_unchanged_when_the_code_is_not_valid(monkeypatch, draft):
    model = Model(draft, {"reply": GOOD, "code": PROGRAM})
    out = _turn(monkeypatch, model)
    assert not any(_is_reply_only(p) for p in model.prompts) and out["code_after"] == PROGRAM


def test_code_that_does_not_compile_is_not_kept(monkeypatch):
    broken = "def f(:\n    incomplete('Log in as jordan')\n"
    model = Model({"reply": LEAK, "code": broken}, {"reply": GOOD, "code": PROGRAM})
    _turn(monkeypatch, model)
    assert not _is_reply_only(model.prompts[1])


def test_no_extra_calls(monkeypatch):
    ok = Model({"reply": GOOD, "code": PROGRAM})
    _turn(monkeypatch, ok)
    assert len(ok.prompts) == 1                                          # the normal flow: one call
    once = Model({"reply": LEAK, "code": PROGRAM})                        # everything leaks: still at most 3 calls
    _turn(monkeypatch, once)
    assert len(once.prompts) == 3 and sum(map(_is_reply_only, once.prompts)) == 1


def test_the_spending_limit_is_checked_before_the_reply_only_redraft(paid, monkeypatch):
    """The real call path: the first draft is paid, then the candidate's round limit is reached - the
    reply-only redraft (and any fallback) is refused before anything is sent."""
    paid.spent_before(0.60, **CANDIDATE)
    replies = iter([json.dumps({"reply": LEAK, "code": PROGRAM})])
    orig = llm_service._call_claude

    def claude(prompt, max_tokens=4096):
        orig(prompt, max_tokens)          # limit check, the fake paid request ($0.30), the record
        return next(replies, json.dumps({"reply": GOOD, "code": None}))
    monkeypatch.setattr(llm_service, "_call_claude", claude)
    with llm_service.call_context(**CANDIDATE), pytest.raises(llm_service.AILimitReached):
        round2_typist.turn("python", {"title": "x"}, [], "", SAID)
    assert len(paid.sent) == 1                                           # 0.60 -> 0.90: the redraft was never sent


# The 47 real Java programs the assistant wrote for simulated candidates (runs 2-3): kept exactly as written
# when only the reply is redone. Compiling was checked when they were delivered; here it's taken as given.
REAL = json.loads((Path(__file__).parent / "fixtures" / "practice_engine" / "real_outputs" / "assistant" /
                   "simulated_java_programs.json").read_text())


@pytest.mark.parametrize("p", REAL["programs"], ids=lambda p: p["persona"])
def test_real_programs_are_kept_exactly_when_only_the_reply_is_redone(monkeypatch, p):
    monkeypatch.setattr(round2_typist, "compile_problem", lambda language, code: None)
    said = p["said"] + "\n" + round2_typist._reference_terms(REAL["app_reference"], p["said"])
    if round2_typist.unsaid(p["code"], said, code=True) or round2_typist._values_not_typed(p["said"], p["code"], p["own"]):
        pytest.skip("this recorded draft doesn't pass the code checks on its own - not a reply-only case")
    model = Model({"reply": LEAK, "code": p["code"]}, {"reply": GOOD, "code": "changed()"})
    out = _turn(monkeypatch, model, prompt=p["said"] + "\nGenerate the code.", language="java", reference=REAL["app_reference"])
    assert out["code_after"] == p["code"] and len(model.prompts) == 2 and _is_reply_only(model.prompts[1])


def test_most_real_programs_qualify_for_the_reply_only_redraft():
    said_ok = [p for p in REAL["programs"]
               if not round2_typist.unsaid(p["code"], p["said"] + "\n" + round2_typist._reference_terms(REAL["app_reference"], p["said"]), code=True)]
    assert len(said_ok) >= 30, f"only {len(said_ok)} of {len(REAL['programs'])} - the regression check would prove little"
