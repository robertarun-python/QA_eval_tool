"""
Round 3 Priority 5 - a requirement the candidate states must survive into
the code. Live traces (Run B, case 7) showed "count how many distinct
numbers in nums are bigger than it" implemented as counting ALL bigger
numbers, 6/6, and nothing noticed: the scope check only sees additions and
the reply check didn't cover "distinct". Starts with distinct / unique /
no duplicates only (round3_scope_guard.dropped_requirement). The candidate
stating the requirement never authorizes a technique - a set is still the
candidate's call.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest

from app.services import llm_service, round3_scope_guard as guard

CASE7 = ("For each number in nums, use a nested loop to count how many distinct numbers in nums are bigger than it. "
         "The answer is the number that has exactly one distinct bigger value; if none, print -1.")
CURRENT = "nums = list(map(int, input().split(',')))\nanswer = -1\nprint(answer)"
HEAD = "nums = list(map(int, input().split(',')))\nanswer = -1\nfor num in nums:\n    count = 0\n"
TAIL = "    if count == 1:\n        answer = num\nprint(answer)"
DROPPED = HEAD + "    for other in nums:\n        if other > num:\n            count += 1\n" + TAIL
WITH_SET = HEAD + "    for other in set(nums):\n        if other > num:\n            count += 1\n" + TAIL
WITH_SEEN = HEAD + "    seen = []\n    for other in nums:\n        if other > num and other not in seen:\n            seen.append(other)\n            count += 1\n" + TAIL


# ---- detection ----

def test_dropped_distinct_is_detected_in_the_candidates_words():
    assert guard.dropped_requirement(CASE7.lower(), DROPPED) == "distinct numbers"


@pytest.mark.parametrize("code", [
    WITH_SET,
    WITH_SEEN,
    "vals = list(dict.fromkeys(nums))",
    "nums.sort()\nfor i in range(1, len(nums)):\n    if nums[i] != nums[i - 1]:\n        count += 1",
    "Set<Integer> s = new HashSet<>(nums);",
    "if (!seen.contains(x)) { seen.add(x); }",
])
def test_reasonable_evidence_keeps_the_requirement(code):
    assert guard.dropped_requirement(CASE7.lower(), code) is None


@pytest.mark.parametrize("instruction", [
    "count all the numbers bigger than it, including duplicates",
    "count the values, not distinct ones - repeats count too",
    "make this a distinct step before the loop",
    "loop through nums and store the biggest value in biggest",
    "Loop through nums; if num < biggest and num > answer, set answer = num. Then print answer.",  # Run B T11-style
])
def test_no_requirement_is_read_where_none_was_stated(instruction):
    assert guard.dropped_requirement(instruction.lower(), DROPPED) is None


@pytest.mark.parametrize("instruction", ["keep only unique values in nums", "the list must have no duplicates", "numbers that are distinct"])
def test_each_requirement_form_is_recognised(instruction):
    assert guard.dropped_requirement(instruction, DROPPED) is not None


def test_whole_code_is_checked_not_just_added_lines():
    """Distinctness already done earlier in the code - an edit elsewhere
    hasn't dropped it."""
    earlier = "vals = set(nums)\n" + DROPPED
    assert guard.dropped_requirement("now print the count of distinct values", earlier) is None


# ---- ownership: the requirement authorizes no technique ----

def test_distinct_alone_still_does_not_authorize_a_set():
    assert "chose a set to remove duplicates" in guard.unrequested_additions("python", CURRENT, WITH_SET, CASE7.lower())


def test_candidate_naming_a_set_authorizes_it():
    named = CASE7.replace("count how many distinct numbers", "use a set to count how many distinct numbers").lower()
    assert guard.unrequested_additions("python", CURRENT, WITH_SET, named) == []
    assert guard.dropped_requirement(named, WITH_SET) is None


# ---- the turn flow (mocked model - no API) ----

def _edit(code, message="Done."):
    return {"response_kind": "code_edit", "response_message": message, "code_after": code, "category_status": {}}


def _turn(monkeypatch, replies, candidate_prompt=CASE7):
    calls = []

    def fake_call(prompt, max_tokens=4096):
        calls.append(prompt)
        return json.dumps(replies[min(len(calls) - 1, len(replies) - 1)])

    monkeypatch.setattr(llm_service, "_call_claude", fake_call)
    result = llm_service.round3_coding_turn(
        scenario_description="Find the second-largest distinct value.", language="python",
        conversation_so_far=[], current_code=CURRENT, candidate_prompt=candidate_prompt, turn_number=3,
    )
    return result, calls


def test_dropped_requirement_gets_one_retry_that_keeps_it_without_choosing_how(monkeypatch):
    ask = {"response_kind": "clarify", "response_message": "How do you want repeated values to be left out?",
           "code_after": None, "category_status": {}}
    result, calls = _turn(monkeypatch, [_edit(DROPPED), ask])
    assert len(calls) == 2
    note = calls[1]
    assert 'required: "distinct numbers"' in note
    assert "use only a technique or data structure they named themselves" in note
    assert "never one you pick for them" in note and "without offering or naming any options" in note
    assert "Never mention this check or the rejected attempt" in note
    assert result["response_kind"] == "clarify" and result["code_after"] is None


def test_retry_that_keeps_it_with_the_candidates_named_set_is_accepted(monkeypatch):
    named = CASE7.replace("count how many distinct numbers", "use a set to count how many distinct numbers")
    result, calls = _turn(monkeypatch, [_edit(DROPPED), _edit(WITH_SET)], candidate_prompt=named)
    assert len(calls) == 2
    assert result["response_kind"] == "code_edit" and "set(nums)" in result["code_after"]


def test_retry_that_picks_a_set_itself_is_still_rejected(monkeypatch):
    result, calls = _turn(monkeypatch, [_edit(DROPPED), _edit(WITH_SET)])
    assert len(calls) == 2
    assert result["response_kind"] == "clarify" and result["response_message"] == guard.SCOPE_FALLBACK_MESSAGE


def test_retry_that_drops_it_again_falls_back_without_naming_anything(monkeypatch):
    result, calls = _turn(monkeypatch, [_edit(DROPPED), _edit(DROPPED)])
    assert len(calls) == 2  # at most one regeneration
    assert result["response_message"] == guard.SCOPE_FALLBACK_MESSAGE
    assert "distinct" not in result["response_message"].lower()


def test_additions_and_a_dropped_requirement_share_one_retry(monkeypatch):
    over = DROPPED + "\nprint(sorted(nums))"
    result, calls = _turn(monkeypatch, [_edit(over), _edit(over)])
    assert len(calls) == 2
    assert "went beyond the candidate's instruction" in calls[1] and "sorting" in calls[1]
    assert 'required: "distinct numbers"' in calls[1]


def test_no_requirement_means_no_extra_call(monkeypatch):
    result, calls = _turn(monkeypatch, [_edit(DROPPED)], candidate_prompt=CASE7.replace("distinct ", ""))
    assert len(calls) == 1 and result["response_kind"] == "code_edit"


# ---- reply honesty ----

def test_reply_claiming_distinct_over_code_without_it_is_misleading():
    reply = "Added a nested loop that counts how many distinct numbers in nums are bigger than each number."
    assert guard.misleading_claim(reply, CURRENT, DROPPED)
    assert not guard.misleading_claim(reply, CURRENT, WITH_SEEN)
    assert not guard.misleading_claim("Counted every bigger number, not distinct ones.", CURRENT, DROPPED)


def test_misleading_distinct_reply_is_replaced_by_the_factual_summary(monkeypatch):
    """No requirement in the instruction, so no retry - but the reply may
    not claim distinctness the code doesn't have."""
    reply = "Counted how many distinct values in nums are bigger than each number."
    result, calls = _turn(monkeypatch, [_edit(DROPPED, reply)], candidate_prompt="For each number in nums, use a nested loop to count how many numbers in nums are bigger than it.")
    assert len(calls) == 1 and result["response_kind"] == "code_edit"
    assert result["response_message"] != reply
    assert result["response_message"] == guard.diff_summary(CURRENT, DROPPED)
