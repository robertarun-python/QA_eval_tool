"""
Round 3: a complete approach of the candidate's own is written as stated, even when it would not produce
the task's answer (Batch 1b, 2026-10-08). Real-AI gate: "remove duplicates, sort descending, and return
the first element" got "What should the code do to get the second-largest distinct value?" - steering
toward the hidden goal; "...sorted() with reverse=True and print the first element" got "What should the
code do after sorting?"; "Find the maximum value with max(), then print how many times it appears" was
refused. Such a reply is never shown: one retry told to write their steps, else a neutral way forward.
Scripted model; no AI call.
"""
import json

import pytest

from app.services import llm_service, round3_policy

TASK = ("Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the list "
        "(duplicates of the largest value do not count as a separate value). If the list has fewer than two distinct "
        "values, print -1 instead.")
FIRST = "Write the program using my approach: remove duplicates, sort descending, and return the first element."
TOP = "Using my approach - drop repeated numbers, order them biggest first, return the top one - write the code."
SET_FIRST = "Use a set to remove duplicates from the numbers, then use sorted() with reverse=True and print the first element."
MAX_COUNT = "Find the maximum value with max(), then print how many times it appears in the list."
FRONT = "Get rid of repeats, put the numbers in order from the largest down, and print the one at the front."
TRACK = "Go through the numbers, keep the largest value seen, and print it."
KEEP_ONCE = "Read the comma-separated numbers, keep each value once, sort them from largest to smallest, and print the first one."

READ = "nums = [int(x) for x in input().split(',')]\n"
FIRST_CODE = READ + "nums = sorted(set(nums), reverse=True)\nprint(nums[0])\n"
COUNT_CODE = READ + "biggest = max(nums)\nprint(nums.count(biggest))\n"

STEERING = [  # gate replies, verbatim, plus near variants
    (FIRST, {"response_kind": "clarify", "response_message": "What should the code do to get the second-largest distinct value?"}),
    (TOP, {"response_kind": "clarify", "response_message": "What should the code do to get the second-largest distinct value?"}),
    (SET_FIRST, {"response_kind": "clarify", "response_message": "What should the code do after sorting?"}),
    (FRONT, {"response_kind": "clarify", "response_message": "What should the program do with those numbers?"}),
    (MAX_COUNT, {"response_kind": "refuse", "response_message": round3_policy.WHOLE_TASK_REFUSAL_MESSAGE}),
    (FIRST, {"response_kind": "clarify", "response_message": "Did you want the second element instead?"}),
    (FIRST, {"response_kind": "explain", "response_message": "The first element is the largest value, not the second largest."}),
    (SET_FIRST, {"response_kind": "clarify", "response_message": "How should the code get the distinct value you need?"}),
    # real-AI gate 2026-10-08, case 4 - both drafts, verbatim
    (TRACK, {"response_kind": "clarify", "response_message": "What should the code do with the values once it has them?"}),
    (TRACK, {"response_kind": "clarify", "response_message": "What should the program do with the values once it has them?"}),
]
NOT_STEERING = [
    # information their own step names but never gives
    ("Remove duplicates, keep the values greater than the threshold, and print them.",
     {"response_kind": "clarify", "response_message": "What threshold should the values be compared against?"}),
    ("Remove duplicates, keep the values greater than the threshold, and print them.",
     {"response_kind": "clarify", "response_message": "What is the threshold?"}),
    # no complete approach of their own - the model's other rules decide
    ("Keep values greater than the threshold.",
     {"response_kind": "clarify", "response_message": "What values should be kept, and what is the threshold?"}),
    ("Print the list I created earlier.",
     {"response_kind": "clarify", "response_message": "I need a specific instruction - what exactly should the code do?"}),
    ("Find the second-largest distinct value and print it, or -1 if there isn't one.",
     {"response_kind": "refuse", "response_message": round3_policy.WHOLE_TASK_REFUSAL_MESSAGE}),
    ("Sort the numbers descending.",  # no output step: not a complete approach
     {"response_kind": "clarify", "response_message": "What should the code do after sorting?"}),
    ("Remove the duplicates and sort them descending.",  # steps, but none to an output yet
     {"response_kind": "clarify", "response_message": "What should the code do after sorting?"}),
    # the task's words are fine once the candidate used them
    ("Remove the duplicates, sort the distinct values from largest to smallest, and print them.",
     {"response_kind": "explain", "response_message": "The current code already prints the distinct values from largest to smallest."}),
    # other refusals and code are never touched
    (FIRST, {"response_kind": "refuse", "response_message": "That's for you to identify - decide which cases matter, then tell me what to write."}),
    (FIRST, {"response_kind": "code_edit", "response_message": "Done.", "code_after": FIRST_CODE}),
    (FIRST, {"response_kind": "explain", "response_message": "The current code already does exactly that."}),
]


@pytest.mark.parametrize("prompt,reply", STEERING)
def test_steering_replies_are_recognised(prompt, reply):
    assert llm_service.r3_steers_complete_approach(reply, prompt, TASK)


@pytest.mark.parametrize("prompt,reply", NOT_STEERING)
def test_other_replies_are_not(prompt, reply):
    assert llm_service.r3_steers_complete_approach(reply, prompt, TASK) is None


def _reply(kind, msg, code=None):
    return json.dumps({"response_kind": kind, "response_message": msg, "code_after": code, "category_status": {}})


def _turn(monkeypatch, replies, prompt, conversation=None):
    calls, queue = [], list(replies)

    def fake(p, max_tokens=4096):
        calls.append(p)
        return queue.pop(0) if queue else _reply("clarify", "Anything else?")
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = llm_service.round3_coding_turn(scenario_description=TASK, language="python", conversation_so_far=conversation or [],
                                            current_code=None, candidate_prompt=prompt, turn_number=len(conversation or []) + 1)
    return result, calls


def _shown(result):
    return json.dumps(result).lower()


GOAL_QUESTION = _reply("clarify", "What should the code do to get the second-largest distinct value?")


# 1. An approach that differs from the task's goal is followed.
@pytest.mark.parametrize("prompt", [FIRST, TOP])
def test_an_approach_that_misses_the_goal_is_still_written(monkeypatch, prompt):
    result, calls = _turn(monkeypatch, [GOAL_QUESTION, _reply("code_edit", "Done.", FIRST_CODE)], prompt)
    assert result["response_kind"] == "code_edit" and result["code_after"] == FIRST_CODE
    assert len(calls) == 2 and llm_service._R3_APPROACH_NOTE in calls[1]


# 2. "first" where the task wants the second: the goal is never named to them.
def test_first_is_not_steered_to_second(monkeypatch):
    result, _ = _turn(monkeypatch, [GOAL_QUESTION, _reply("code_edit", "Done.", FIRST_CODE)], FIRST)
    assert "second" not in _shown(result) and "distinct" not in _shown(result)
    assert "print(nums[0])" in result["code_after"]


def test_steering_twice_ends_in_a_way_forward_that_names_no_goal(monkeypatch):
    result, calls = _turn(monkeypatch, [GOAL_QUESTION, GOAL_QUESTION], FIRST)
    assert result["response_kind"] == "explain" and result["response_message"] in llm_service.R3_PROGRESS_MESSAGES
    assert "second" not in _shown(result) and len(calls) == 2


def test_the_retry_note_names_nothing_of_this_task():
    note = llm_service._R3_APPROACH_NOTE.lower()
    assert not {"second", "largest", "distinct", "-1", "duplicate"} & set(note.replace(",", " ").replace(".", " ").split())


# 3. max() + count is never refused.
def test_max_and_count_is_not_refused(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("refuse", round3_policy.WHOLE_TASK_REFUSAL_MESSAGE),
                                        _reply("code_edit", "Done.", COUNT_CODE)], MAX_COUNT)
    assert not round3_policy.is_continuation_of_whole_task([], MAX_COUNT, TASK)
    assert result["response_kind"] == "code_edit" and result["code_after"] == COUNT_CODE and len(calls) == 2


def test_a_refusal_twice_is_never_shown_for_their_own_approach(monkeypatch):
    refusal = _reply("refuse", round3_policy.WHOLE_TASK_REFUSAL_MESSAGE)
    result, _ = _turn(monkeypatch, [refusal, refusal], MAX_COUNT)
    assert result["response_kind"] == "explain" and result["response_message"] in llm_service.R3_PROGRESS_MESSAGES


# 4. set + sorted(reverse=True) + first gets code.
def test_set_sorted_reverse_first_gets_code(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("clarify", "What should the code do after sorting?"),
                                        _reply("code_edit", "Done.", FIRST_CODE)], SET_FIRST)
    assert result["response_kind"] == "code_edit" and result["code_after"] == FIRST_CODE and len(calls) == 2


def test_code_the_first_time_costs_one_call(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", FIRST_CODE)], SET_FIRST)
    assert result["code_after"] == FIRST_CODE and len(calls) == 1


# 5. A genuinely missing threshold is still asked for.
@pytest.mark.parametrize("prompt", ["Keep values greater than the threshold.",
                                    "Remove duplicates, keep the values greater than the threshold, and print them."])
def test_a_missing_threshold_is_still_asked(monkeypatch, prompt):
    question = "What threshold should the values be compared against?"
    result, calls = _turn(monkeypatch, [_reply("clarify", question)], prompt)
    assert result["response_kind"] == "clarify" and result["response_message"] == question and len(calls) == 1


def test_a_goal_only_message_may_still_be_asked_about(monkeypatch):
    question = "What should the code do with the numbers?"
    result, calls = _turn(monkeypatch, [_reply("clarify", question)], "Work with the numbers next.")
    assert result["response_message"] == question and len(calls) == 1


# 6. Whole-task requests are still refused - before the AI, untouched by this check.
@pytest.mark.parametrize("prompt", ["Write the complete solution for this problem.",
                                    "Work out the second largest distinct number in the list and print it.",
                                    "Can you solve this task for me?", TASK])
def test_whole_task_requests_are_still_refused_before_the_ai(monkeypatch, prompt):
    result, calls = _turn(monkeypatch, [], prompt)
    assert result["response_kind"] == "refuse" and result["code_after"] is None and calls == []


def test_a_restated_goal_is_refused_before_the_ai(monkeypatch):
    """Policy A (2026-10-08): this goal-only wording used to reach the model; now it is refused with no call."""
    prompt = "Find the second-largest distinct value and print it, or -1 if there isn't one."
    result, calls = _turn(monkeypatch, [], prompt)
    assert result["response_kind"] == "refuse" and result["response_message"] == round3_policy.WHOLE_TASK_REFUSAL_MESSAGE
    assert calls == []


def test_a_model_refusal_of_a_message_without_an_approach_stands(monkeypatch):
    """The steering check never overrides a model refusal when the message states no approach of its own."""
    result, calls = _turn(monkeypatch, [_reply("refuse", round3_policy.WHOLE_TASK_REFUSAL_MESSAGE)], "Print the answer.")
    assert result["response_kind"] == "refuse" and result["response_message"] == round3_policy.WHOLE_TASK_REFUSAL_MESSAGE
    assert len(calls) == 1


# 7. Hidden edge-case questions are still blocked, also on the approach retry.
def test_an_edge_case_question_is_still_never_shown(monkeypatch):
    leak = _reply("clarify", "What should the program do if there aren't enough distinct values?")
    result, calls = _turn(monkeypatch, [leak, _reply("code_edit", "Done.", FIRST_CODE)], FIRST)
    assert result["code_after"] == FIRST_CODE and "enough" not in _shown(result) and len(calls) == 2
    assert llm_service._R3_NO_UNRAISED_CASES_NOTE in calls[1]  # the edge-case guard keeps its own retry
    result, _ = _turn(monkeypatch, [GOAL_QUESTION, leak], FIRST)
    assert result["response_message"] in llm_service.R3_PROGRESS_MESSAGES and "enough" not in _shown(result)


def test_a_leaking_retry_explanation_is_replaced_without_a_complete_approach_too(monkeypatch):
    leak = _reply("clarify", "What should the program do if there aren't enough distinct values?")
    explain = _reply("explain", "I need to know what to print when there are fewer than two values.")
    result, _ = _turn(monkeypatch, [leak, explain], "Remove the duplicates and sort them descending.")
    assert result["response_message"] in llm_service.R3_PROGRESS_MESSAGES and "fewer" not in _shown(result)


def test_the_approach_retry_still_forbids_unraised_cases():
    assert "input case they have not raised" in llm_service._R3_APPROACH_NOTE


# 8. "keep each value once" is still kept.
def test_keep_each_value_once_is_still_preserved(monkeypatch):
    dropped = READ + "nums.sort(reverse=True)\nprint(nums[0])\n"
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", dropped), _reply("code_edit", "Done.", FIRST_CODE)], KEEP_ONCE)
    assert len(calls) == 2 and result["code_after"] == FIRST_CODE


def test_the_prompt_states_the_rule():
    prompt = llm_service._load_prompt("round3_coding_turn.txt")
    assert "The candidate's own approach is followed as stated, even when it would not produce the answer" in prompt
    assert "never steer them toward the problem's goal" in prompt


def test_a_pasted_task_that_reads_like_steps_keeps_its_refusal(monkeypatch):
    """A task whose own statement lists operations, pasted back: refused before the AI, and this check
    never turns that refusal into anything else."""
    task = "Remove the duplicates from the list of integers, sort them in descending order, and print the largest value."
    calls = []
    monkeypatch.setattr(llm_service, "_call_claude", lambda p, max_tokens=4096: calls.append(p))
    result = llm_service.round3_coding_turn(scenario_description=task, language="python", conversation_so_far=[],
                                            current_code=None, candidate_prompt=task, turn_number=1)
    assert result["response_kind"] == "refuse" and result["response_message"] == round3_policy.WHOLE_TASK_REFUSAL_MESSAGE
    assert calls == []


def test_the_copyable_step_question_is_gone_from_the_prompt():
    """Case 4's drafts reproduced the prompt's own example question almost word for word (2026-10-08)."""
    prompt = llm_service._load_prompt("round3_coding_turn.txt").lower()
    assert "what should the code do with the values" not in prompt
    assert "a question about two missing things can be answered with only one of them" in prompt


def test_the_complete_approach_exception_directly_follows_rule_1():
    lines = llm_service._load_prompt("round3_coding_turn.txt").splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith('1. "clarify" - use this whenever writing the code requires YOU'))
    exception = lines[header + 1].strip()
    assert exception.startswith("Exception - a complete approach of the candidate's own:")
    assert '"go through the numbers, keep the largest value seen, and print it"' in exception
    assert "Keeping or tracking a value as you go is a method, not a goal." in exception
    assert "Never answer such a message with a question about what the code should do" in exception
    assert "such as a threshold or a value" in exception  # missing information may still be asked for
    assert not [w for w in ("second", "distinct", "-1", "fewer", "empty") if w in exception.lower()]
