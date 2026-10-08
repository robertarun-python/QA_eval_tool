"""
Round 3: delivered code never handles an input case the candidate didn't raise, and a stated
"keep the largest value seen" approach gets code (2026-10-08 real-AI gate).

A - for "remove duplicates, sort descending, and return the first element" the model wrapped the read in
"if input_line: ... else: values = []" - empty-input behaviour nobody asked for. The question guard
already hid such QUESTIONS; now code that adds such BEHAVIOUR goes through the same no-questions retry,
and is never delivered if the retry adds it again.
B - "Go through the numbers, keep the largest value seen, and print it." got the generic progress
message: both replies were held (asking about None / an empty list as the starting value). The prompt
now says how such a step starts. Scripted model; no AI call.
"""
import json

import pytest

from app.services import llm_service, round3_io_format, round3_policy

TASK = ("Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the list "
        "(duplicates of the largest value do not count as a separate value). If the list has fewer than two distinct "
        "values, print -1 instead.")
FIRST = "Write the program using my approach: remove duplicates, sort descending, and return the first element."
TRACK = "Go through the numbers, keep the largest value seen, and print it."
READ = "nums = [int(x) for x in input().split(',')]\n"
FIRST_CODE = READ + "unique = sorted(set(nums), reverse=True)\nprint(unique[0])\n"
GATE_CODE = ("input_line = input()\nif input_line:\n    values = list(map(int, input_line.split(',')))\nelse:\n    values = []\n\n"
             "unique_values = list(set(values))\nunique_values.sort(reverse=True)\nprint(unique_values[0])\n")
# Gate case 3: the program's logic wrapped in the empty check - prints nothing for an empty line.
CASE3_CODE = ("input_line = input()\nif input_line:\n    numbers = [int(x) for x in input_line.split(',')]\n"
              "    unique = sorted(set(numbers), reverse=True)\n    print(unique[0])\n")
DEFAULT_INPUT = round3_io_format.DEFAULT_IO_FORMAT["input"]
NO_EMPTY_RULE = {**round3_io_format.DEFAULT_IO_FORMAT, "input": "One line of comma-separated values, read from standard input."}
TRACK_CODE = READ + "largest = nums[0]\nfor n in nums:\n    if n > largest:\n        largest = n\nprint(largest)\n"

UNRAISED = [  # (topic, code) - each adds behaviour for a case the candidate never raised
    ("empty input", GATE_CODE),
    ("empty input", READ.replace("nums = ", "line = input()\nnums = ").replace("input().split", "line.split") + "if not nums:\n    print('')\n"),
    ("empty input", "line = input()\nnums = [int(x) for x in line.split(',')] if line else []\nprint(max(nums))\n"),
    ("empty input", READ + "if len(nums) == 0:\n    exit()\nprint(max(nums))\n"),
    ("too few values", READ + "if len(nums) < 2:\n    print(nums[0])\nelse:\n    print(sorted(nums)[1])\n"),
    ("ties", READ + "largest = max(nums)\nfor n in nums:\n    if n != largest:\n        print(n)\n"),
    ("a fallback value", READ + "unique = sorted(nums, reverse=True)\nprint(unique[1] if unique[1:] else -1)\n"),
    ("a fallback value", READ + "answer = -1\nprint(answer)\n"),
    ("a fallback value", "def first(nums):\n    return None\n"),
    ("invalid input or errors", "try:\n    nums = [int(x) for x in input().split(',')]\nexcept ValueError:\n    nums = []\nprint(nums)\n"),
    ("invalid input or errors", "nums = [int(x) for x in input().split(',') if x.isdigit()]\nprint(nums)\n"),
]
NORMAL = [  # what stated steps need - never flagged
    FIRST_CODE, TRACK_CODE,
    READ + "print(max(nums))\nprint(nums.count(max(nums)))\n",
    READ + "uniq = []\nfor n in nums:\n    if n not in uniq:\n        uniq.append(n)\nprint(uniq)\n",
    READ + "for i in range(len(nums)):\n    if nums[i] > 0:\n        print(nums[i])\n",
    READ + "print(nums[-1])\nprint(sorted(nums)[::-1])\n",
    READ + "largest = float('-inf')\nfor n in nums:\n    largest = max(largest, n)\nprint(largest)\n",
]


@pytest.mark.parametrize("topic,code", UNRAISED)
def test_unraised_case_behaviour_in_code_is_recognised(topic, code):
    assert llm_service.r3_unraised_case_in_code(None, code, [], TRACK) == topic


@pytest.mark.parametrize("code", NORMAL)
def test_normal_implementation_is_not(code):
    assert llm_service.r3_unraised_case_in_code(None, code, [], FIRST) is None
    assert llm_service.r3_unraised_case_in_code(None, code, [], TRACK) is None


def test_a_raised_fallback_does_not_open_other_cases():
    """They named -1 for one case: a tie check they never raised is still caught."""
    code = READ + "big = max(nums)\nfor n in nums:\n    if n != big:\n        print(n)\nprint(-1)\n"
    assert llm_service.r3_unraised_case_in_code(None, code, [], "Print the numbers, then print -1.") == "ties"


def test_a_ties_check_is_theirs_once_they_state_duplicates():
    assert llm_service.r3_unraised_case_in_code(None, UNRAISED[5][1], [], FIRST) is None


@pytest.mark.parametrize("raised,code", [
    ("Read the numbers; if the line is empty use an empty list, then print the largest.", GATE_CODE),
    ("If there are fewer than 2 numbers print the first one, otherwise print the second smallest.", UNRAISED[4][1]),
    ("Print every number that is not equal to the largest.", UNRAISED[5][1]),
    ("Print the second unique value, or -1 if there isn't one.", UNRAISED[6][1]),
    ("Print the second unique value, or -1 if there isn't one.",
     READ + "unique = sorted(set(nums), reverse=True)\nif len(unique) < 2:\n    print(-1)\nelse:\n    print(unique[1])\n"),
    ("Read the numbers and skip invalid input with try/except.", UNRAISED[9][1]),
    ("Remove the duplicates with a loop: keep a value only if it differs from the previous one, then print them.",
     READ + "uniq = []\nfor n in sorted(nums):\n    if not uniq or n != uniq[-1]:\n        uniq.append(n)\nprint(uniq)\n"),
])
def test_a_case_the_candidate_raised_may_be_handled(raised, code):
    assert llm_service.r3_unraised_case_in_code(None, code, [], raised) is None
    earlier = [{"candidate_prompt": raised, "response_kind": "code_edit"}]
    assert llm_service.r3_unraised_case_in_code(None, code, earlier, "now carry on") is None


def test_a_flag_the_candidate_named_is_not_an_empty_check():
    code = READ + "found = 5 in nums\nif found:\n    print('yes')\n"
    assert llm_service.r3_unraised_case_in_code(None, code, [], "Set found to whether 5 is in nums, and if found print yes.") is None


def test_only_lines_this_edit_adds_are_checked():
    """Code already there (an earlier, accepted turn) is not this edit's doing."""
    assert llm_service.r3_unraised_case_in_code(GATE_CODE, GATE_CODE + "print(len(values))\n", [], "also print the length") is None


def _reply(kind, msg, code=None):
    return json.dumps({"response_kind": kind, "response_message": msg, "code_after": code, "category_status": {}})


def _turn(monkeypatch, replies, prompt, conversation=None, code=None, io_format=None):
    calls, queue = [], list(replies)

    def fake(p, max_tokens=4096):
        calls.append(p)
        return queue.pop(0) if queue else _reply("clarify", "Anything else?")
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = llm_service.round3_coding_turn(scenario_description=TASK, language="python", conversation_so_far=conversation or [],
                                            current_code=code, candidate_prompt=prompt, turn_number=len(conversation or []) + 1,
                                            io_format=io_format)
    return result, calls


# Empty-input behaviour the published format doesn't state is never delivered.
def test_unraised_empty_input_code_goes_through_the_safe_retry(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", CASE3_CODE), _reply("code_edit", "Done.", FIRST_CODE)], FIRST)
    assert result["code_after"] == FIRST_CODE and len(calls) == 2
    assert "it added handling for empty input" in calls[1] and "if input_line:" in calls[1]  # the specific retry (2026-10-08)


def test_unraised_case_code_twice_is_never_delivered(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", CASE3_CODE)] * 2, FIRST)
    assert result["code_after"] is None and result["response_message"] in llm_service.R3_PROGRESS_MESSAGES
    assert len(calls) == 2 and "empty" not in result["response_message"].lower()


def test_the_retry_names_no_requirement_of_this_task(monkeypatch):
    _, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", CASE3_CODE), _reply("code_edit", "Done.", FIRST_CODE)], FIRST)
    note = calls[1].split("IMPORTANT - your previous code for this turn was rejected")[1][:900]
    assert "second" not in note and "distinct" not in note and "-1" not in note


# ---- The published format: "An empty line means there are no values." ----
GATE_INLINE = "line = input()\nnums = [int(x) for x in line.split(',')] if line else []\n" + "unique = sorted(set(nums), reverse=True)\nprint(unique[0])\n"
GATE_NOT = ("line = input()\nif not line.strip():\n    nums = []\nelse:\n    nums = [int(x) for x in line.split(',')]\n"
            "unique = sorted(set(nums), reverse=True)\nprint(unique[0])\n")


# 1. The published rule may produce if/else input handling - delivered first time.
@pytest.mark.parametrize("code", [GATE_CODE, GATE_INLINE, GATE_NOT])
def test_the_published_empty_line_rule_may_be_read_with_if_else(monkeypatch, code):
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", code)], FIRST)
    assert result["response_kind"] == "code_edit" and result["code_after"] == code and len(calls) == 1


# 2. ...and is not flagged as invented.
@pytest.mark.parametrize("code", [GATE_CODE, GATE_INLINE, GATE_NOT])
def test_the_published_rule_is_not_flagged(code):
    assert llm_service.r3_unraised_case_in_code(None, code, [], FIRST, DEFAULT_INPUT) is None


# 3. Empty-input behaviour the format doesn't require is still caught, format or not.
NOT_REQUIRED = [
    CASE3_CODE,                                                                   # logic skipped for an empty line
    GATE_CODE.replace("    values = []\n", "    values = []\n    print('no values')\n"),  # a message added to the read
    "line = input()\nif line:\n    nums = [int(x) for x in line.split(',')]\nelse:\n    print(0)\n",  # an output instead
    READ + "if not nums:\n    print('')\nelse:\n    print(max(nums))\n",          # a check on the values
    READ + "if len(nums) == 0:\n    exit()\nprint(max(nums))\n",
    "line = input()\nif line:\n    nums = [int(x) for x in line.split(',')]\nelse:\n    other = []\n",  # a different name
    "line = input()\nif line:\n    nums = [int(x) for x in line.split(',')]\n    print(nums)\nelse:\n    nums = []\n",  # output only when non-empty
    "line = input()\nif line:\n    nums = [int(x) for x in line.split(',')]\nprint(nums)\n",  # a check with no "no values" branch
]


@pytest.mark.parametrize("code", NOT_REQUIRED)
def test_empty_behaviour_the_format_does_not_require_is_still_caught(code):
    assert llm_service.r3_unraised_case_in_code(None, code, [], FIRST, DEFAULT_INPUT) == "empty input"


@pytest.mark.parametrize("code", NOT_REQUIRED)
def test_and_never_delivered(monkeypatch, code):
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", code), _reply("code_edit", "Done.", FIRST_CODE)], FIRST)
    assert result["code_after"] == FIRST_CODE and len(calls) == 2


def test_without_the_published_rule_the_same_read_is_caught(monkeypatch):
    assert llm_service.r3_unraised_case_in_code(None, GATE_CODE, [], FIRST, NO_EMPTY_RULE["input"]) == "empty input"
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", GATE_CODE), _reply("code_edit", "Done.", FIRST_CODE)],
                          FIRST, io_format=NO_EMPTY_RULE)
    assert result["code_after"] == FIRST_CODE and len(calls) == 2


def test_the_published_rule_opens_no_other_case():
    """Reading the empty line is allowed; a fallback, a too-few check or error handling next to it is not."""
    for topic, extra in [("a fallback value", "print(unique[1] if unique[1:] else -1)\n"),
                         ("too few values", "if len(unique) < 2:\n    exit()\n"),
                         ("invalid input or errors", "try:\n    print(unique[0])\nexcept IndexError:\n    pass\n")]:
        code = GATE_CODE.replace("unique_values", "unique") + extra
        assert llm_service.r3_unraised_case_in_code(None, code, [], TRACK, DEFAULT_INPUT) == topic


# 4. A candidate who raised empty input gets it handled.
def test_raised_empty_input_code_is_delivered_first_time(monkeypatch):
    prompt = "Read the numbers - if the line is empty use an empty list - then remove duplicates, sort descending and print the first."
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", GATE_CODE)], prompt)
    assert result["code_after"] == GATE_CODE and len(calls) == 1


# Ties, too few values, fallbacks and errors - same treatment.
@pytest.mark.parametrize("topic,code", UNRAISED[4:])
def test_other_unraised_cases_in_code_are_retried(monkeypatch, topic, code):
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", code), _reply("code_edit", "Done.", TRACK_CODE)], TRACK)
    assert result["code_after"] == TRACK_CODE and len(calls) == 2


# 4. A normal implementation of their steps: one call, delivered.
@pytest.mark.parametrize("prompt,code", [(FIRST, FIRST_CODE), (TRACK, TRACK_CODE),
                                         ("Find the maximum value with max(), then print how many times it appears.",
                                          READ + "biggest = max(nums)\nprint(nums.count(biggest))\n")])
def test_normal_code_is_delivered_first_time(monkeypatch, prompt, code):
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", code)], prompt)
    assert result["code_after"] == code and len(calls) == 1


# 5. "loop, keep the largest seen, print it" - code path open, starting value never asked.
def test_track_largest_is_a_complete_approach_that_gets_code(monkeypatch):
    assert not round3_policy.is_continuation_of_whole_task([], TRACK, TASK)
    held = _reply("clarify", "Should the largest value start as None or as the first number?")  # the likely gate reply
    result, calls = _turn(monkeypatch, [held, _reply("code_edit", "Done.", TRACK_CODE)], TRACK)
    assert result["code_after"] == TRACK_CODE and len(calls) == 2


def test_the_prompt_says_how_a_tracked_value_starts_and_reads_plainly():
    prompt = llm_service._load_prompt("round3_coding_turn.txt")
    assert '"Keep the largest value seen", "track the smallest so far" (start it at the first value' in prompt
    assert "never ask what it starts as, and never start it at None, -1 or infinity" in prompt
    assert "where the format above says what an empty line means, read such a line that way (as no values)" in prompt
    assert "but add nothing else for empty, missing or invalid input" in prompt
    assert "Write that read plainly" not in prompt  # the reverted wording that conflicted with the format


# 6. Whole-task requests: still refused before the AI.
@pytest.mark.parametrize("prompt", ["Write the complete solution for this problem.",
                                    "Work out the second largest distinct number in the list and print it."])
def test_whole_task_still_refused(monkeypatch, prompt):
    result, calls = _turn(monkeypatch, [], prompt)
    assert result["response_kind"] == "refuse" and calls == []


def test_the_case7_goal_is_refused_before_the_ai(monkeypatch):
    """Policy A (2026-10-08): gate case 7's wording no longer reaches the model - refused with no call."""
    prompt = "Work out the second largest distinct number and print it."
    result, calls = _turn(monkeypatch, [], prompt)
    assert result["response_kind"] == "refuse" and result["code_after"] is None and calls == []


def test_a_model_refusal_the_rules_let_through_stands(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("refuse", round3_policy.WHOLE_TASK_REFUSAL_MESSAGE)], "Print the answer.")
    assert result["response_kind"] == "refuse" and result["code_after"] is None and len(calls) == 1


# 7. The question guard is unchanged.
def test_edge_case_question_guard_still_works(monkeypatch):
    leak = _reply("clarify", "What should the program do if there aren't enough distinct values?")
    result, calls = _turn(monkeypatch, [leak, _reply("code_edit", "Done.", FIRST_CODE)], FIRST)
    assert result["code_after"] == FIRST_CODE and "enough" not in json.dumps(result).lower() and len(calls) == 2


# 8. Dedupe vocabulary unchanged: "keep each value once" is still a requirement the code can't drop.
def test_keep_each_value_once_still_preserved(monkeypatch):
    prompt = "Read the comma-separated numbers, keep each value once, sort them from largest to smallest, and print the first one."
    dropped = READ + "nums.sort(reverse=True)\nprint(nums[0])\n"
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", dropped), _reply("code_edit", "Done.", FIRST_CODE)], prompt)
    assert result["code_after"] == FIRST_CODE and len(calls) == 2


# 9. Steering protection unchanged.
def test_steering_protection_still_works(monkeypatch):
    steer = _reply("clarify", "What should the code do to get the second-largest distinct value?")
    result, calls = _turn(monkeypatch, [steer, _reply("code_edit", "Done.", FIRST_CODE)], FIRST)
    assert result["code_after"] == FIRST_CODE and llm_service._R3_APPROACH_NOTE in calls[1]


def test_an_explanation_of_their_code_is_untouched(monkeypatch):
    msg = "unique_values[0] raises an IndexError when values is empty, because there is no element 0."
    result, calls = _turn(monkeypatch, [_reply("explain", msg)], "Why does my code crash on an empty line?", code=GATE_CODE)
    assert result["response_message"] == msg and len(calls) == 1
