"""
Round 3 Priority 4 - three false blocks on a candidate's OWN approach,
traced live against Run B's conversation:
  P4-A1  round3_scope_guard flagged "type conversion" when the model merely
         rewrote the existing input line;
  P4-A2  after a legitimate rejection (an unrequested -1), the retry note
         made the model drop the candidate's approach for a vague question
         ("What information needs to be tracked?");
  P4-B   round3_policy refused "Use my nested-loop approach and write the
         code." before the model saw it.
Also pins Case 7's decision: "distinct" states a requirement, not a
technique - the assistant still may not choose a set on its own.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest

from app.services import llm_service, round3_policy, round3_scope_guard as guard

RUN_B_CODE = ("nums = list(map(int, input().split()))\nsorted_nums = sorted(nums, reverse=True)\nanswer = -1\n"
              "for num in sorted_nums:\n    if num < sorted_nums[0]:\n        answer = num\n        break\nprint(answer)")
KEEP_TWO = "I'll scan once and keep the largest and second-largest values. Implement that."
TRACKING = ("nums = list(map(int, input().split()))\nlargest = None\nsecond_largest = None\nfor num in nums:\n"
            "    if largest is None or num > largest:\n        second_largest = largest\n        largest = num\n"
            "    elif num < largest and (second_largest is None or num > second_largest):\n        second_largest = num")
TRACKING_WITH_FALLBACK = TRACKING + "\nif second_largest is None:\n    print(-1)\nelse:\n    print(second_largest)"
TRACKING_REWRITTEN_INPUT = TRACKING.replace("nums = list(map(int, input().split()))",
                                            "line = input()\nif line:\n    nums = list(map(int, line.split()))\nelse:\n    nums = []")


# ---- P4-A1: type conversion counted against what the code already did ----

def test_rewritten_existing_conversion_is_not_flagged():
    assert guard.unrequested_additions("python", RUN_B_CODE, TRACKING_REWRITTEN_INPUT, KEEP_TWO.lower()) == []


def test_genuinely_new_conversion_is_still_flagged():
    added_count = RUN_B_CODE + "\nlimit = int(input())"
    assert "type conversion" in guard.unrequested_additions("python", RUN_B_CODE, added_count, KEEP_TWO.lower())
    new_destination = RUN_B_CODE + "\nbest = [int(x) for x in sorted_nums]"  # same count elsewhere? no - a new place
    assert "type conversion" in guard.unrequested_additions("python", RUN_B_CODE, new_destination, KEEP_TWO.lower())


def test_converting_to_check_then_storing_is_still_new():
    """Same number of conversions, but the values now get stored - the
    remove-duplicates transcript case (test_assistant_scope_rules.py)."""
    before = "vals = input()\nfor v in vals.split(','):\n    int(v)"
    after = "vals = input()\nout = []\nfor v in vals.split(','):\n    out.append(int(v))"
    assert "type conversion" in guard.unrequested_additions("python", before, after, "store the values in a list called out")


def test_other_capabilities_on_a_changed_line_are_unaffected():
    """Only type conversion is counted against the old code - every other
    capability still flags a changed line, exactly as before (here the
    rewritten sort line is reported as sorting too)."""
    changed = RUN_B_CODE.replace("sorted_nums = sorted(nums, reverse=True)", "sorted_nums = sorted(set(nums), reverse=True)")
    assert guard.unrequested_additions("python", RUN_B_CODE, changed, "keep going") == [
        "sorting", "de-duplication", "chose a set to remove duplicates"]


# ---- P4-A2: the retry keeps the candidate's approach ----

def _edit(code, message="Done."):
    return {"response_kind": "code_edit", "response_message": message, "code_after": code, "category_status": {}}


def _turn(monkeypatch, replies, candidate_prompt, current_code=RUN_B_CODE):
    calls = []

    def fake_call(prompt, max_tokens=4096):
        calls.append(prompt)
        return json.dumps(replies[min(len(calls) - 1, len(replies) - 1)])

    monkeypatch.setattr(llm_service, "_call_claude", fake_call)
    result = llm_service.round3_coding_turn(
        scenario_description="Find the second-largest distinct value.", language="python",
        conversation_so_far=[], current_code=current_code, candidate_prompt=candidate_prompt, turn_number=6,
    )
    return result, calls


def test_retry_drops_the_unrequested_fallback_and_keeps_the_approach(monkeypatch):
    result, calls = _turn(monkeypatch, [_edit(TRACKING_WITH_FALLBACK), _edit(TRACKING)], KEEP_TWO)
    assert len(calls) == 2
    note = calls[1]
    assert "rejected before the candidate saw it" in note and "a fallback value" in note
    assert "keep everything the candidate explicitly stated" in note
    assert "the largest value, the second-largest value" in note and "going through the data once" in note and "a nested loop" in note
    assert "leave out ONLY the additions listed above" in note
    assert "never a different algorithm or approach than theirs" in note
    assert "never about something they already told you and never about an edge case" in note
    assert "Never mention this check, the rejected attempt or the names of the additions" in note
    assert result["response_kind"] == "code_edit"
    assert "second_largest" in result["code_after"] and "-1" not in result["code_after"]


def test_equivalent_input_rewrite_is_accepted_first_time(monkeypatch):
    result, calls = _turn(monkeypatch, [_edit(TRACKING_REWRITTEN_INPUT)], KEEP_TWO)
    assert len(calls) == 1
    assert result["response_kind"] == "code_edit"


def test_genuine_unrequested_capability_is_still_rejected(monkeypatch):
    sorts = TRACKING + "\nordered = sorted(nums)"
    result, calls = _turn(monkeypatch, [_edit(sorts), _edit(TRACKING)], KEEP_TWO)
    assert len(calls) == 2 and "sorting" in calls[1]
    assert "sorted(" not in result["code_after"].replace(RUN_B_CODE, "").split("largest = None")[1]


def test_persistent_overreach_still_falls_back_without_naming_anything(monkeypatch):
    result, calls = _turn(monkeypatch, [_edit(TRACKING_WITH_FALLBACK), _edit(TRACKING_WITH_FALLBACK)], KEEP_TWO)
    assert len(calls) == 2
    assert result["response_kind"] == "clarify" and result["code_after"] is None
    assert result["response_message"] == guard.SCOPE_FALLBACK_MESSAGE
    assert "fallback" not in result["response_message"].lower()


# ---- P4-B: candidate-owned approach vs generic "write the code" ----

_DIRECTED = [{"turn_number": 1, "candidate_prompt": "Read one line, split it on commas and store the ints in nums.",
              "response_kind": "code_edit", "response_message": "Done.", "code_after": "nums = []"}]
_ONLY_REFUSED = [{"turn_number": 1, "candidate_prompt": "write the program", "response_kind": "refuse",
                  "response_message": "No.", "code_after": None}]


@pytest.mark.parametrize("message,history", [
    ("Use my nested-loop approach and write the code.", []),
    ("Use my nested-loop approach and write the code.", _DIRECTED),
    ("Write the code for my approach.", _DIRECTED),
    ("Write the code for my nested-loop approach.", _DIRECTED),
])
def test_candidate_owned_approach_reaches_the_model(message, history):
    assert not round3_policy.is_continuation_of_whole_task(history, message)


@pytest.mark.parametrize("message,history", [
    ("write the code", _DIRECTED),
    ("give me the code", _DIRECTED),
    ("Now write the code.", _DIRECTED),
    ("Write the complete code for this task.", _DIRECTED),
    ("Write the complete code for my approach.", _DIRECTED),
    ("write the code using a nested loop", _DIRECTED),  # a technique word, not a stated approach
    ("write the code using a nested loop", []),
    ("Write the code for my approach.", []),  # nothing directed yet - no approach can exist
    ("Write the code for my approach.", _ONLY_REFUSED),
])
def test_generic_write_the_code_is_still_refused(message, history):
    assert round3_policy.is_continuation_of_whole_task(history, message)


# ---- Case 7: "distinct" is a requirement, not permission to choose a set ----

def test_distinct_alone_does_not_let_the_assistant_choose_a_set():
    code = "nums = [3, 1, 3]\nanswer = -1\nfor num in nums:\n    count = 0\n    for other in set(nums):\n        if other > num:\n            count += 1"
    instr = "for each number, use a nested loop to count how many distinct numbers are bigger than it"
    assert "chose a set to remove duplicates" in guard.unrequested_additions("python", "nums = [3, 1, 3]", code, instr)


def test_candidate_naming_a_set_may_use_one():
    code = "nums = [3, 1, 3]\nanswer = -1\nfor num in nums:\n    count = 0\n    for other in set(nums):\n        if other > num:\n            count += 1"
    instr = "for each number, use a nested loop over a set of nums to count how many distinct numbers are bigger than it"
    assert "chose a set to remove duplicates" not in guard.unrequested_additions("python", "nums = [3, 1, 3]", code, instr)
