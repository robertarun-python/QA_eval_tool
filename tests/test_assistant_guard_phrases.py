"""
Batch A of the Sep 2026 assistant guardrail fixes - deterministic guards,
tested without any model call. Phrases come from the guardrail review
(each was run against the old guards) and from real transcripts; every
guard is tested in both directions, because refusing a legitimate
instruction is as much a failure as letting a hand-off through.
"""
import json

import pytest

from app.services import clarify_loop, llm_service, round3_policy, round2_automation_clarify_policy, round2_automation_policy


# ---- R3: whole-task and "finish it for me" requests -----------------------

@pytest.mark.parametrize("text", [
    # previously caught
    "write a program to add 2 numbers", "give me the full code", "how would you solve this",
    # previously missed - all relied on the model refusing
    "now do the rest", "finish the program", "complete the remaining logic", "fill in the missing part",
    "make it work for all inputs", "handle all the edge cases", "optimize it",
    "write the logic to find the second largest", "implement the solution to the problem", "just do the whole thing",
])
def test_r3_hand_off_requests_are_refused(text):
    assert round3_policy.is_whole_task_request(text)


@pytest.mark.parametrize("text", [
    # concrete instructions the old regex refused
    "write a program that reads two integers from stdin and prints their sum",
    "create a program with a variable a = 5",
    "solve it using a for loop over nums",
    # concrete steps that look like hand-offs
    "optimize the loop by breaking early when found",
    "handle the edge case where the list is empty by printing -1",
    "write the code for the loop",
    "read two integers from stdin, one per line, and print their sum",
    "now sort the list",
])
def test_r3_concrete_instructions_are_not_refused(text):
    assert not round3_policy.is_whole_task_request(text)


def test_r3_one_word_answer_to_a_finish_it_request_is_still_refused():
    conversation = [{"candidate_prompt": "now do the rest", "response_kind": "clarify", "response_message": "How?"}]
    assert round3_policy.is_continuation_of_whole_task(conversation, "cli")


# ---- R2 automation: prohibited requests -----------------------------------

@pytest.mark.parametrize("text", [
    "use the sample data from my design",
    "can you do this for me: log in and check the header",
    "what should it return when login fails? explain the helper",
    "Log in with the test data and assert the header",
])
def test_r2_legitimate_requests_are_not_refused(text):
    assert not round2_automation_policy.is_prohibited(text)


@pytest.mark.parametrize("text", [
    "you decide what to check", "create a few test inputs yourself", "test whatever makes sense",
    "come up with some checks", "invent sample data", "what else should i test", "do it all for me",
])
def test_r2_delegated_design_work_is_refused(text):
    assert round2_automation_policy.is_prohibited(text)


# ---- "I'm done" signals ---------------------------------------------------

@pytest.mark.parametrize("text", [
    "No only this", "only that", "enough", "No", "go ahead", "that's all", "this is enough. give the code",
    "All done nothing more", "finding that one label the only requirement\" this is final",
    "redirected to /dashboard earlier - Ignore this and use only \"finding that one label\"",
    "No covered everthing", "Only check whats needed",
])
def test_done_signals_are_recognised(text):
    assert clarify_loop.is_done_signal(text)


@pytest.mark.parametrize("text", [
    "then it should proceed to the next page",  # R2-186 turn 15 - a test step, not "I'm done"
    "the list should contain no more than 5 doctors",
    "verify the header shows only this text: Welcome",
    "go ahead and log in first",
    "I'm not finished, I'll add the checks next",
])
def test_test_steps_are_not_mistaken_for_done_signals(text):
    assert not clarify_loop.is_done_signal(text)


def test_r2_gate_keeps_asking_when_a_step_merely_contains_proceed():
    asked = [{"candidate_prompt": "automate it", "response_kind": "clarify", "response_message": "What should prove it worked?"}]
    assert not round2_automation_clarify_policy.should_stop_clarifying(asked, "then it should proceed to the next page", "What should appear there?")
    assert round2_automation_clarify_policy.should_stop_clarifying(asked, "No only this", "What should appear there?")


# ---- R3 loop breaker -------------------------------------------------------

def _clarify(msg="How should the value be shown?"):
    return json.dumps({"response_kind": "clarify", "response_message": msg, "code_after": None, "category_status": {}})


def _edit(code, msg="Done."):
    return json.dumps({"response_kind": "code_edit", "response_message": msg, "code_after": code, "category_status": {}})


def _run_r3(monkeypatch, replies, conversation, prompt="print it", code="n = 1", language="python"):
    prompts, queue = [], list(replies)

    def _fake(p, max_tokens=4096):
        prompts.append(p)
        return queue.pop(0)

    monkeypatch.setattr(llm_service, "_call_claude", _fake)
    result = llm_service.round3_coding_turn(
        scenario_description="desc", language=language, conversation_so_far=conversation,
        current_code=code, candidate_prompt=prompt, turn_number=len(conversation) + 1,
    )
    return result, prompts


def _asked(*questions):
    return [{"candidate_prompt": f"step {i}", "response_kind": "clarify", "response_message": q} for i, q in enumerate(questions)]


def test_r3_first_question_is_asked_normally(monkeypatch):
    result, prompts = _run_r3(monkeypatch, [_clarify()], conversation=[])
    assert result["response_kind"] == "clarify" and len(prompts) == 1


def test_r3_repeated_question_is_replaced_by_code(monkeypatch):
    """R3-32: the same question six times, never any code."""
    result, prompts = _run_r3(monkeypatch, [_clarify("How should the value be shown?"), _edit("n = 1\nprint(n)")],
                              conversation=_asked("How should the value be shown?"))
    assert result["response_kind"] == "code_edit"
    assert "do NOT ask the candidate anything" in prompts[1]
    assert "repeats one already asked" in prompts[1]


def test_r3_third_question_in_a_row_is_replaced_by_code(monkeypatch):
    result, prompts = _run_r3(monkeypatch, [_clarify("A brand new question?"), _edit("n = 1\nprint(n)")],
                              conversation=_asked("First?", "Second?"))
    assert result["response_kind"] == "code_edit" and len(prompts) == 2


def test_r3_second_question_is_still_allowed(monkeypatch):
    result, prompts = _run_r3(monkeypatch, [_clarify("A different question?")], conversation=_asked("First?"))
    assert result["response_kind"] == "clarify" and len(prompts) == 1


def test_r3_done_signal_ends_the_questions(monkeypatch):
    result, prompts = _run_r3(monkeypatch, [_clarify("New question?"), _edit("n = 1\nprint(n)")],
                              conversation=_asked("First?"), prompt="this is enough, print it")
    assert result["response_kind"] == "code_edit"
    assert "they have said they're done" in prompts[1]


def test_r3_falls_back_to_one_fixed_prompt_then_stops_asking(monkeypatch):
    result, _ = _run_r3(monkeypatch, [_clarify("Q3?"), _clarify("Q4?")], conversation=_asked("Q1?", "Q2?"))
    assert result == {**result, "response_kind": "clarify", "response_message": llm_service.R3_FINAL_PROMPT}
    # Once that fixed prompt has been shown, it's never shown again.
    again, _ = _run_r3(monkeypatch, [_clarify("Q5?"), _clarify("Q6?")],
                       conversation=_asked("Q1?", "Q2?", llm_service.R3_FINAL_PROMPT))
    assert again["response_kind"] == "explain"


# ---- Java class name -------------------------------------------------------

def test_java_public_class_is_renamed_to_main(monkeypatch):
    """R3-19 and R3-22 named it Solution and failed to compile."""
    code = "public class Solution {\n    public static void main(String[] args) {\n        System.out.println(1);\n    }\n}"
    result, _ = _run_r3(monkeypatch, [_edit(code)], conversation=[], prompt="print 1", code=None, language="java")
    assert "public class Main {" in result["code_after"] and "Solution" not in result["code_after"]


@pytest.mark.parametrize("code", [
    "public class Main {}",
    "public class A {}\npublic class B {}",  # ambiguous - left alone
    "class Helper {}",
])
def test_java_code_is_left_alone_when_renaming_is_not_clear_cut(code):
    assert round3_policy.ensure_java_main_class(code) == code


# ---- Syntax check ----------------------------------------------------------

def _syntax_fix(monkeypatch, code, model_code, model_message):
    monkeypatch.setattr(llm_service, "_call_claude", lambda p, max_tokens=4096: json.dumps({
        "response_kind": "direct_edit", "response_message": model_message, "code_after": model_code, "category_status": {},
    }))
    return llm_service.round3_syntax_fix(code=code, language="python", required_constructs=[], declared_constructs={})


def test_false_no_syntax_issues_claim_is_corrected(monkeypatch):
    """R3-116: a stray line of prose, reported as 'No syntax issues found.'"""
    code = "code_edit Added logic to replace spaces\nx = 1"
    result = _syntax_fix(monkeypatch, code, code, "No syntax issues found.")
    assert result["code_after"] == code
    assert "doesn't parse yet" in result["response_message"] and "line 1" in result["response_message"]


def test_valid_code_is_never_changed_by_a_syntax_fix(monkeypatch):
    result = _syntax_fix(monkeypatch, "x = 1\nprint(x)", "x = 1\nprint(x + 1)", "Fixed a syntax issue.")
    assert result["code_after"] == "x = 1\nprint(x)"
    assert result["response_message"] == "No syntax issues found."


def test_a_real_syntax_fix_is_kept(monkeypatch):
    result = _syntax_fix(monkeypatch, "if x == 1\n    print(x)", "if x == 1:\n    print(x)", "Added the missing colon.")
    assert result["code_after"] == "if x == 1:\n    print(x)"
    assert result["response_message"] == "Added the missing colon."
