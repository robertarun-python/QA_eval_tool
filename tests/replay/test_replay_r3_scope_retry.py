"""
Replay: Round 3 Priority 4 against the real model, continuing Run B's
conversation (turns 1-5). A candidate who states their own approach gets
it implemented - an unrequested extra (the -1 output) is dropped on the
retry instead of the approach, and no question asks again for what they
already said. Same opt-in as the other replays:

    RUN_LLM_REPLAY=1 .venv/bin/python -m pytest tests/replay -v -p no:warnings
"""
import os
import re

import pytest

from app.services import llm_service, round3_scope_guard
from tests.replay.test_replay_r3_assistance import CONTEXT, CURRENT_CODE, _DATA, _RUN_B, _nested_for
from app.migrate_round3_io_format import IO_FORMAT

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LLM_REPLAY") != "1", reason="calls the real model - set RUN_LLM_REPLAY=1")

_REASKS_TRACKING = re.compile(r"what (specific )?(information|values?|data) (needs|need|should|do you want) to (be )?track", re.I)
_ADDED_FALLBACK = re.compile(r"print\s*\(\s*-1\s*\)")

NESTED_STATED = CONTEXT + [{
    "turn_number": 6, "candidate_prompt": "My new approach: for each number in nums, use a nested loop to count how many numbers in nums are bigger than it.",
    "response_kind": "clarify", "response_message": "What should the program do with that count?", "code_after": None,
}]


def _turn(message, conversation=CONTEXT):
    return llm_service.round3_coding_turn(
        scenario_description=_DATA["scenario_description"], language="python", conversation_so_far=conversation,
        current_code=CURRENT_CODE, candidate_prompt=message, turn_number=len(conversation) + 1,
        required_constructs=[], declared_constructs={}, io_format=IO_FORMAT,
    )


@pytest.mark.parametrize("message", [
    "I'll scan once and keep the largest and second-largest values. Implement that.",
    "Go through nums one time, keeping the biggest and second-biggest values seen so far. Implement that.",
])
def test_own_single_pass_approach_is_implemented_without_extras(message):
    r = _turn(message)
    assert r["response_kind"] in ("code_edit", "clarify"), r
    assert not _REASKS_TRACKING.search(r["response_message"]), r["response_message"]
    if r["response_kind"] == "code_edit":
        added = "\n".join(round3_scope_guard.added_lines(CURRENT_CODE, r["code_after"]))
        assert not _ADDED_FALLBACK.search(added), r["code_after"]
        assert "sorted(" not in r["code_after"] or "sorted(" in CURRENT_CODE, r["code_after"]


# Priority 3 regression (found in the Priority 5 investigation): a fully
# specified step after Run B's history was refused (a) 3/3 while refuse (e)
# said "takes precedence over (a)". Must get code; the similar "keep track of
# the biggest value" step must too.
@pytest.mark.parametrize("message", [
    "Loop through nums, store the biggest value in a variable called biggest starting at nums[0], and print biggest.",
    "Now loop through nums and keep track of the biggest value in a variable called biggest.",
])
def test_explicit_step_after_run_b_history_gets_code(message):
    r = _turn(message)
    assert r["response_kind"] == "code_edit" and "biggest" in (r.get("code_after") or ""), r


# The candidate stated the nested-loop approach one turn earlier; these point
# back to it. Before the final Priority 4 prompt change the model refused
# "write the code" 5/5 and "implement it" 1/5 under refuse (c).
@pytest.mark.parametrize("message", [
    "Use my nested-loop approach and write the code.",
    "Use my nested-loop method and implement it.",
    "Use the approach I just described and write that code.",
])
def test_own_nested_loop_approach_is_not_refused(message):
    r = _turn(message, conversation=NESTED_STATED)
    assert r["response_kind"] != "refuse", r
    if r["response_kind"] == "code_edit":
        assert _nested_for(r["code_after"]), r["code_after"]
        assert not re.search(r"sorted\(|\.sort\(|\bset\(|\bmax\(", r["code_after"]), r["code_after"]


@pytest.mark.parametrize("message", [
    "Scan once and keep the largest and second-largest values; implement that.",
    "I'll scan once and maintain the largest and second-largest values. Implement this.",
])
def test_own_approach_in_the_same_message_is_not_refused(message):
    r = _turn(message)
    assert r["response_kind"] in ("code_edit", "clarify"), r
    assert not _REASKS_TRACKING.search(r["response_message"]), r["response_message"]


# No implementation direction anywhere - the assistant must not write the
# rest of the solution (refused, or at most asked what it should do).
@pytest.mark.parametrize("message,conversation", [
    ("Use a nested loop and write the code.", []),
    ("Use my approach and write the code.", []),
    ("Write the code for the problem.", []),
    ("Write code for the problem.", CONTEXT),  # not caught before the model - refuse (c) decides
    ("Implement the solution.", []),
    ("Do the rest for me.", CONTEXT),
    ("Write the complete solution.", CONTEXT),
])
def test_no_stated_approach_gets_no_solution(message, conversation):
    r = _turn(message, conversation=conversation)
    assert r["response_kind"] in ("refuse", "clarify") and not r.get("code_after"), r


def test_asking_for_a_faster_approach_is_still_refuse_e():
    from tests.replay.test_replay_r3_assistance import REFUSE_E
    r = _turn("I don't know a faster approach. Can you give me one?")
    assert r["response_message"] == REFUSE_E, r


def test_run_b_step_by_step_instruction_still_accepted():
    r = _turn(_RUN_B[6]["candidate_prompt"])
    assert r["response_kind"] == "code_edit" and "biggest = max(nums)" in r["code_after"], r
