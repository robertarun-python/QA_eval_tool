"""
Round 3 assistant, Batch 1a (2026-10-07): candidate-owned implementation intent reaches the model and
gets code; AI-owned solution requests stay refused; every reply without code says how to go on and
never repeats the previous one.

From a real production assessment (candidate 4, Round 3, 2026-10-07): 11 messages, 15 AI calls and no
code - clear step lists refused by wording ("write the program - step 1..."), correct code rejected by
literal trigger words ("pick the unique elements" but no "set"), specific questions swapped for one
generic line shown three times. The model is a scripted fake throughout: no AI call is made.
"""
import json

import pytest

from app.services import assistant_operations as ops
from app.services import clarify_loop, llm_service, round3_io_format, round3_policy
from app.services import round3_scope_guard as guard

# Scenario #16's description, exactly as live.
TASK = ("Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the list "
        "(duplicates of the largest value do not count as a separate value). If the list has fewer than two distinct "
        "values, print -1 instead.")
IO = round3_io_format.for_config(None)
MECH = guard.io_mechanics(IO, TASK)
FULL = ("nums = [int(x) for x in input().split(',')]\nuniq = sorted(set(nums), reverse=True)\n"
        "print(uniq[1] if len(uniq) > 1 else -1)\n")


# ---- 1. ownership: what reaches the model, what is refused before it -----------------------------

CANDIDATE_OWNED = [
    "read the input line, split it on commas and convert each value to int",
    "create a set from nums and sort it in descending order",
    "print the element at index 1 of the sorted list",
    "1. read the numbers\n2. remove duplicates\n3. sort them descending\n4. print the second one",
    "step 1: pick the unique values. step 2: sort largest to smallest. step 3: print the 2nd",
    "take the numbers the user gives, keep only one copy of each, put them biggest first and show the second",
    "dedupe the values then order them high to low and output position 1",
    "dedupe, sort desc, print [1]",
    "unique -> sort desc -> 2nd",
    "could you please read the comma separated input into a list",
    "please write the code to remove duplicates from nums",
    "write the code for step 1: read the input",
    "implement step 2 - remove the duplicates",
    "add a check: if fewer than 2 distinct values print -1",
    "create the program that follows my steps: read input, dedupe, sort desc, print index 1",
    "write the program - step 1 pick the unique elements. step 2 sort descending. step 3 print the 2nd element",
    "write a program that reads the numbers, removes duplicates, sorts them in descending order and prints the second",
    "build the solution from these steps: 1. unique values 2. sort descending 3. print second",
    "actually, instead of sorting, find the max, remove it, then find the max again",
    "just write the input part for now",
    "please implement it",
    "please implement what I said",
    "now do step 3 from my earlier message",
    "implement my algorithm above",
    "I think the best way, as we discussed in class, is to read the input, dedupe it and sort it backwards; then print the second value. Thanks!",
    "toss the repeats, line 'em up biggest first, spit out number two",
    "what format is the input in?",
    "make it work",
    "sort ascending in descending order then print the first and last as the second",
    # kept from the existing guards - concrete instructions are not hand-offs
    "write a program that reads two integers from stdin and prints their sum",
    "create a program with a variable a = 5",
    "solve it using a for loop over nums",
    "Use my nested-loop approach and write the code.",
]

AI_OWNED = [
    "write the program", "solve this for me", "give me the full solution", "write the complete code", "solve it",
    "finish the program", "do the rest", "implement the solution", "write the logic for the second largest",
    TASK,  # the task pasted in
    "find the second largest distinct number and print it",  # the goal restated
    "can you code this task", "do the question", "answer the problem in python",
    "write a program for this; use a variable called x = 1",  # bypass: a technique word used to unlock
    "write the full program, split by comma",
    "give me the complete solution using a for loop",
    "give me the final answer", "Give me the final code.",
    "write the code to find the second largest distinct value",  # "code to <goal>", not an operation of theirs
    "write the code for my approach", "follow my approach and write the program",  # nothing stated yet
    "generate the code for my steps above",
    "generate test cases for this", "give me edge cases",
]


@pytest.mark.parametrize("text", CANDIDATE_OWNED)
def test_candidate_owned_instructions_reach_the_model(text):
    assert not round3_policy.is_continuation_of_whole_task([], text, TASK)
    assert not round3_policy.is_prohibited(text)


@pytest.mark.parametrize("text", AI_OWNED)
def test_ai_owned_requests_are_refused_before_the_model(text):
    assert round3_policy.is_continuation_of_whole_task([], text, TASK) or round3_policy.is_prohibited(text)


@pytest.mark.parametrize("text", [
    "write a program for this; use a variable called x = 1", "write the full program, split by comma",
    "give me the complete solution using a for loop", "can you code this task", "do the question",
    "answer the problem in python", "Give me the final code.",
    "write the code to find the second largest distinct value",  # "code to <goal>" - only operation verbs pass
    "write the code to solve the second largest problem", "give me the code to get the answer",
])
def test_bypasses_are_closed_even_without_the_task_text(text):
    """No technique word unlocks an AI-owned request - not even where the scenario's description isn't known."""
    assert round3_policy.is_whole_task_request(text)


_DIRECTED = [{"candidate_prompt": "read the input line and split it on commas", "response_kind": "code_edit"}]


@pytest.mark.parametrize("text", ["write the code for my approach", "implement my approach",
                                  "now write the program using my steps", "generate the code for my steps above"])
def test_referring_to_steps_already_given_reaches_the_model(text):
    assert not round3_policy.is_continuation_of_whole_task(_DIRECTED, text, TASK)


@pytest.mark.parametrize("text", ["write the program", "write the complete code for my approach", "solve it"])
def test_a_hand_off_after_real_steps_is_still_refused(text):
    assert round3_policy.is_continuation_of_whole_task(_DIRECTED, text, TASK)


def test_steps_answering_a_question_about_a_whole_task_request_are_the_candidates_own():
    asked = [{"candidate_prompt": "write a program for the second largest", "response_kind": "clarify", "response_message": "How?"}]
    assert not round3_policy.is_continuation_of_whole_task(asked, "1. dedupe 2. sort desc 3. print second", TASK)
    assert round3_policy.is_continuation_of_whole_task(asked, "cli", TASK)  # the two-step bypass stays closed


def test_pasting_the_task_plus_own_steps_counts_only_the_steps():
    assert round3_policy.is_continuation_of_whole_task([], TASK + " Do it.", TASK)
    assert not round3_policy.is_continuation_of_whole_task(
        [], TASK + "\n1. remove the duplicates\n2. sort descending\n3. print index 1", TASK)


# ---- 2. candidate 4's real Round 3 messages (submission 225) ---------------------------------------

C4 = [
    (1, "refuse", TASK),
    (2, "model", "write the code to give the second largest unique element from the input array a = [1,2,3,4,99,9,0,1,7, 9,7]."),
    (3, "model", "given an array of integers, a, \n1. pick the unique elements from the sorted array.\n2. sort them all in descending order. \n3. return the integer in the 1st index of the sorted array"),
    (4, "model", "you are given an array of integers, a, \nreturn 0 if the a is empty.\nelse, do the following steps.\nstep 1. pick the unique elements from the sorted array. \nstep 2. sort them all in descending order.\nstep 3. return the integer in the 1st index of the sorted array."),
    (5, "model", "write the program - \nyou are given an array of integers, a, return 0 if the a is empty. else, do the following steps. step 1. pick the unique elements from the sorted array. step"),
    (6, "model", "you are given an array of integers, a, return 0 if the a is empty. else, do the following steps. step 1. pick the unique elements from the sorted array."),
    (7, "refuse", "can you write a program for me if I ask"),
    (8, "model", "can you write a snippet for me if I ask"),
    (9, "model", "a is an array of integers.\nremove the duplicates from a."),
    (10, "model", "create a resultant array b \nand create a map c, storing the actual unique array element and its frequency as key and value respectively. if the frequency is more than 1, dont add that to b. else add it."),
    (11, "model", "iterate over the elements of a.\nstore it in the resulting array b if it is not found in a."),
]


@pytest.mark.parametrize("turn,expected,text", C4)
def test_candidate4_messages_are_classified_by_ownership(turn, expected, text):
    refused = round3_policy.is_continuation_of_whole_task([], text, TASK)
    assert refused == (expected == "refuse"), (turn, text)


def test_candidate4_turn3_correct_code_is_in_scope():
    """Turn 3 with the program a correct assistant writes: nothing flagged (it was 5 flags)."""
    t3 = C4[2][2].lower()
    assert guard.unrequested_additions("python", None, FULL.replace(" if len(uniq) > 1 else -1", ""), t3,
                                       io_format_mechanics=MECH, technique_free=True) == []
    assert guard.dropped_requirement(t3, FULL) is None


def test_candidate4_turn3_gets_code_from_the_live_turn(monkeypatch):
    code = FULL.replace(" if len(uniq) > 1 else -1", "")
    result, calls = _turn(monkeypatch, [_edit(code)], C4[2][2])
    assert result["response_kind"] == "code_edit" and result["code_after"] == code and len(calls) == 1


# ---- 3. scope: the same operation in any words gets the same answer --------------------------------

SET_DEDUPE = "nums = [3, 1, 3]\nuniq = set(nums)\n"
LOOP_DEDUPE = "nums = [3, 1, 3]\nuniq = []\nfor n in nums:\n    if n not in uniq:\n        uniq.append(n)\n"


@pytest.mark.parametrize("words", ["remove duplicates from nums", "keep unique values", "pick the unique elements",
                                   "keep only one copy of each number", "dedupe nums", "use a set to remove duplicates",
                                   "toss the repeats", "get rid of the duplicates"])
@pytest.mark.parametrize("code", [SET_DEDUPE, LOOP_DEDUPE])
def test_a_stated_deduplication_can_be_written_either_way(words, code):
    assert guard.unrequested_additions("python", "nums = [3, 1, 3]", code, words, technique_free=True) == []


@pytest.mark.parametrize("words", ["sort descending", "sort desc", "sort largest to smallest", "order them biggest first",
                                   "arrange high to low", "put them in reverse order", "sort with the largest first"])
def test_every_way_of_saying_descending_names_the_order(words):
    assert guard.unrequested_additions("python", "nums = [3, 1]", "nums = [3, 1]\nnums = sorted(nums, reverse=True)", words) == []


@pytest.mark.parametrize("words", ["return the first element", "use index 0 and return it", "print the element at index 1",
                                   "return the integer in the 1st index", "give back the second one", "output position 1"])
def test_returning_the_answer_is_printing_it(words):
    assert "printing output" not in guard.unrequested_additions("python", "nums = [3, 1]", "nums = [3, 1]\nprint(nums[1])", words)


def test_reading_the_published_format_is_never_the_candidates_choice():
    code = "nums = [int(x) for x in input().split(',')]\n"
    assert guard.unrequested_additions("python", None, code, "given an array a", io_format_mechanics=MECH) == []
    # ...but only the published format: another separator is still a choice the candidate makes
    assert "chose how the input values are separated" in guard.unrequested_additions(
        "python", None, "nums = input().split(';')\n", "given an array a", io_format_mechanics=MECH)


# The protections that must not weaken.

def test_a_scenario_with_a_construct_checklist_keeps_the_technique_choice_with_the_candidate():
    assert "chose a set to remove duplicates" in guard.unrequested_additions(
        "python", "nums = [3, 1, 3]", SET_DEDUPE, "remove duplicates from nums", technique_free=False)


def test_distinct_as_a_requirement_still_does_not_let_the_assistant_choose_a_set():
    instr = "for each number, use a nested loop to count how many distinct numbers in nums are bigger than it"
    code = "nums = [3, 1, 3]\nfor n in nums:\n    for other in set(nums):\n        pass\n"
    assert "chose a set to remove duplicates" in guard.unrequested_additions("python", "nums = [3, 1, 3]", code, instr,
                                                                             technique_free=True)


@pytest.mark.parametrize("code,flag", [
    ("nums = [3, 1]\nprint(nums[1] if len(nums) > 1 else -1)", "a fallback value"),
    ("nums = [3, 1]\ntry:\n    print(nums[1])\nexcept IndexError:\n    print(nums[0])", "error handling"),
    ("nums = [3, 1]\nnums = [4, 8, 15, 16]", "hard-coded data the candidate didn't give"),
])
def test_requirements_the_candidate_never_stated_are_still_flagged(code, flag):
    assert flag in guard.unrequested_additions("python", "nums = [3, 1]", code, "print the element at index 1",
                                               io_format_mechanics=MECH, technique_free=True)


def test_a_stated_requirement_is_still_required():
    assert guard.dropped_requirement("pick the unique elements from the array", "nums = [3, 1, 3]\nprint(nums[1])")


def test_the_context_keeps_earlier_details_but_never_a_refused_message():
    history = [{"candidate_prompt": "read the comma separated input into a", "response_kind": "explain"},
               {"candidate_prompt": TASK, "response_kind": "refuse"},
               {"candidate_prompt": "x", "response_kind": "clarify"}]
    text = guard.instruction_text(history, "now sort a")
    assert "comma separated" in text and "distinct" not in text
    assert "read the numbers" not in guard.instruction_text(
        [{"candidate_prompt": "read the numbers", "response_kind": "code_edit"}], "now sort them")


def test_operation_vocabulary():
    assert ops.is_candidate_approach("1. pick the unique elements 2. sort descending 3. print index 1")
    assert not ops.is_candidate_approach("find the second largest distinct value and print it")
    assert not ops.states_deduplication("count how many distinct numbers in nums are bigger than it")


# ---- 4. the live turn: retries, fallbacks, progress (scripted model) -------------------------------

def _clarify(msg):
    return json.dumps({"response_kind": "clarify", "response_message": msg, "code_after": None, "category_status": {}})


def _edit(code, msg="Done."):
    return json.dumps({"response_kind": "code_edit", "response_message": msg, "code_after": code, "category_status": {}})


def _turn(monkeypatch, replies, prompt, conversation=None, code=None):
    calls, queue = [], list(replies)

    def fake(p, max_tokens=4096):
        calls.append(p)
        return queue.pop(0) if queue else _clarify("Anything else?")
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = llm_service.round3_coding_turn(scenario_description=TASK, language="python",
                                            conversation_so_far=conversation or [], current_code=code,
                                            candidate_prompt=prompt, turn_number=len(conversation or []) + 1)
    return result, calls


def _asked(*questions):
    return [{"turn_number": i + 1, "candidate_prompt": f"step {i}", "response_kind": "clarify", "response_message": q}
            for i, q in enumerate(questions)]


T3 = C4[2][2]
T3_CODE = FULL.replace(" if len(uniq) > 1 else -1", "")


def test_f1_correct_code_for_clear_steps_is_delivered(monkeypatch):
    result, calls = _turn(monkeypatch, [_edit(T3_CODE)], T3)
    assert result["response_kind"] == "code_edit" and len(calls) == 1


def test_f2_an_unstated_addition_is_removed_by_one_regeneration(monkeypatch):
    result, calls = _turn(monkeypatch, [_edit(FULL), _edit(T3_CODE)], T3)  # FULL adds the unstated -1
    assert result["response_kind"] == "code_edit" and result["code_after"] == T3_CODE
    assert len(calls) == 2 and "a fallback value" in calls[1]


def test_f3_a_new_specific_question_in_a_loop_is_kept(monkeypatch):
    result, _ = _turn(monkeypatch, [_clarify("How should the input be read?"), _clarify("Which separator does the input use?")],
                      "use the numbers", conversation=_asked("Q1?", "Q2?"))
    assert result["response_kind"] == "clarify" and result["response_message"] == "Which separator does the input use?"


def test_f4_code_from_the_no_questions_retry_is_delivered(monkeypatch):
    result, calls = _turn(monkeypatch, [_clarify("Q3?"), _edit(T3_CODE)], T3, conversation=_asked("Q1?", "Q2?"))
    assert result["response_kind"] == "code_edit"
    assert "do NOT ask the candidate anything" in calls[1]


def test_f5_a_repeated_question_becomes_a_way_forward(monkeypatch):
    result, _ = _turn(monkeypatch, [_clarify("Q2?"), _clarify("Q1?")], "x", conversation=_asked("Q1?", "Q2?"))
    assert result["response_kind"] == "explain" and "Edit code" in result["response_message"]
    assert "one step at a time" not in result["response_message"] and "one specific step" not in result["response_message"]


def test_f6_f7_a_long_loop_never_repeats_itself(monkeypatch):
    """Fallbacks count in the run (they used to reset it) and never repeat the previous reply."""
    convo = _asked("Q1?", "Q2?")
    seen = []
    for i in range(6):
        result, _ = _turn(monkeypatch, [_clarify("Q1?"), _clarify("Q2?")], f"msg {i}", conversation=convo)
        seen.append(result["response_message"])
        convo = convo + [{"turn_number": len(convo) + 1, "candidate_prompt": f"msg {i}", **{k: result[k] for k in ("response_kind", "response_message")}}]
    assert all(a != b for a, b in zip(seen, seen[1:]))
    assert all("Edit code" in m for m in seen)
    assert clarify_loop.clarify_streak(convo, also_count=llm_service.R3_PROGRESS_MESSAGES) >= 6


def test_f8_done_signal_after_one_question_gets_code(monkeypatch):
    asked = [{"turn_number": 1, "candidate_prompt": T3, "response_kind": "clarify", "response_message": "How is input read?"}]
    result, calls = _turn(monkeypatch, [_clarify("How is input read?"), _edit(T3_CODE)], "that's all, just write it",
                          conversation=asked)
    assert result["response_kind"] == "code_edit" and "do NOT ask the candidate anything" in calls[1]


def test_a_reply_copying_the_previous_one_is_replaced(monkeypatch):
    """Turn 6 live: the model echoed the previous fallback line word for word."""
    prev = llm_service.R3_PROGRESS_MESSAGES[1]
    convo = [{"turn_number": 1, "candidate_prompt": "x", "response_kind": "explain", "response_message": prev}]
    result, _ = _turn(monkeypatch, [_clarify(prev)], "y", conversation=convo)
    assert result["response_message"] != prev and "Edit code" in result["response_message"]


def test_a_refusal_never_repeats_word_for_word():
    convo = [{"turn_number": 1, "candidate_prompt": "write the program", "response_kind": "refuse",
              "response_message": round3_policy.WHOLE_TASK_REFUSAL_MESSAGE}]
    again = llm_service.round3_coding_turn(scenario_description=TASK, language="python", conversation_so_far=convo,
                                           current_code=None, candidate_prompt="write the program", turn_number=2)
    assert again["response_kind"] == "refuse" and again["response_message"] == round3_policy.WHOLE_TASK_REFUSAL_AGAIN
    assert "Edit code" in again["response_message"]


def test_every_fallback_offers_a_way_forward():
    for message in (*llm_service.R3_PROGRESS_MESSAGES, guard.SCOPE_FALLBACK_MESSAGE, round3_policy.WHOLE_TASK_REFUSAL_AGAIN):
        assert "Edit code" in message


def test_the_r2_clarify_gate_counts_exactly_as_before():
    asked = [{"response_kind": "clarify", "response_message": "Q?"}, {"response_kind": "explain", "response_message": "x"}]
    assert clarify_loop.clarify_streak(asked) == 0 and clarify_loop.clarify_streak(asked[:1]) == 1
