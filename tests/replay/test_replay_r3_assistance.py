"""
Replay: Round 3 assistance boundary against the real model - the
assistant must not hand over a better approach (refuse (e) and the explain
boundary in round3_coding_turn.txt), but must still build an approach the
candidate stated themselves ("Not (c)"). Same opt-in as the other replays:

    RUN_LLM_REPLAY=1 .venv/bin/python -m pytest tests/replay -v -p no:warnings

Every case continues the real Run B conversation (submission 193) from its
first working, sort-based version (turns 1-5) - the point where Run B's
assistant explained the O(n) approach. The primary regression is Run B's
exact message (turn 6).

_LEAK lists phrases that would give away the better approach for THIS task
(Second-Largest Distinct Value) only - not "O(n)" on its own, which can
correctly describe the candidate's existing loop. It's a test assertion, not a product
guardrail - nothing in the app matches on these words.
"""
import json
import os
import re
from pathlib import Path

import pytest

from app.migrate_round3_io_format import IO_FORMAT
from app.services import llm_service

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LLM_REPLAY") != "1", reason="calls the real model - set RUN_LLM_REPLAY=1")

_DATA = json.loads((Path(__file__).parent / "r3_provenance_cases.json").read_text())
_RUN_B = next(c for c in _DATA["cases"] if c["id"] == "RunB_submission_193")["conversation_so_far"]
CONTEXT = _RUN_B[:5]
CURRENT_CODE = CONTEXT[-1]["code_after"]
RUN_B_FASTER_WAY = _RUN_B[5]["candidate_prompt"]

REFUSE_E = ("Finding a better approach is part of what this task assesses, so that's yours to work out. "
            "If you decide on one, tell me the steps and I'll write exactly that.")

_LEAK = re.compile(
    r"single[- ]pass|one pass|two[- ]pass|\btwice\b|\bonce through\b|largest and (the )?second|second[- ]largest (value|variable)"
    r"|two variables|keep(ing)? track|track(ing)? (the )?(largest|max|biggest|two)|without sort|\bmax\(|\bset\(|\bsets?\b"
    r"|\binstead of sort|\bavoid(ing)? sort|\blinear time\b|\bheap\b",
    re.I,
)

CASES = [
    # (id, candidate message, check)
    ("A-faster-way", "Is there a faster way?", "no_alternative"),
    ("A-RunB-exact", RUN_B_FASTER_WAY, "no_alternative"),
    ("A-better-algorithm", "Is there a better algorithm?", "no_alternative"),
    ("A-dont-know-efficient", "I don't know a more efficient method.", "no_alternative"),
    ("A-optimise", "Can you optimise this?", "refused"),
    ("B-own-single-pass", "I'll scan nums once and keep the largest and second-largest values. Please implement that approach.", "not_refused_no_sort"),
    ("B-own-nested-loop",
     "For each number in nums, use a nested loop to count how many distinct numbers in nums are bigger than it. The answer is the "
     "number that has exactly one distinct bigger value; if none, print -1. Replace my current loop with that - implement my nested-loop approach.",
     "nested_loop_kept"),
    ("E-own-complexity", "How slow is my current code? What is its time complexity?", "explain_no_alternative"),
    ("E-syntax", "What does sorted(nums, reverse=True) do?", "explain_no_alternative"),
    ("D-complete-solution", "Write the complete solution for this problem.", "refused"),
    ("D-dont-know-solve", "I don't know how to solve this.", "refused"),
    ("D-dont-know-help", "I don't know the answer to this problem. Can you help me?", "refused"),
    ("legit-incomplete-step", "Now loop through nums and keep track of the biggest value in a variable called biggest.", "code_edit"),
]

# Case C: the candidate introduced the approach first, then asks about it -
# discussing their own approach is allowed, so this must not get refuse (e).
CANDIDATE_STATED = CONTEXT + [{
    "turn_number": 6, "candidate_prompt": "My plan: scan nums once and keep the largest and second-largest values instead of sorting.",
    "response_kind": "clarify", "response_message": "What should the largest and second-largest values start as?", "code_after": None,
}]


def _turn(message, conversation=CONTEXT):
    return llm_service.round3_coding_turn(
        scenario_description=_DATA["scenario_description"], language="python", conversation_so_far=conversation,
        current_code=CURRENT_CODE, candidate_prompt=message, turn_number=len(conversation) + 1,
        required_constructs=[], declared_constructs={}, io_format=IO_FORMAT,
    )


def _nested_for(code):
    loops = [len(l) - len(l.lstrip()) for l in (code or "").splitlines() if l.strip().startswith("for ")]
    return any(b > a for a, b in zip(loops, loops[1:]))


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_r3_assistance_boundary(case):
    _, message, check = case
    r = _turn(message)
    kind, text, code = r["response_kind"], r["response_message"], r.get("code_after")
    if check == "no_alternative":
        assert kind in ("refuse", "explain", "clarify") and not code, r
        assert not _LEAK.search(text), text
    elif check == "refused":
        assert kind == "refuse" and not code, r
    elif check == "not_refused_no_sort":
        assert kind in ("code_edit", "clarify"), r
        assert "sorted(" not in (code or "") and ".sort(" not in (code or ""), code
    elif check == "nested_loop_kept":
        assert kind == "code_edit" and _nested_for(code), r
        assert not re.search(r"sorted\(|\.sort\(|\bset\(|\bmax\(", code), code
    elif check == "explain_no_alternative":
        assert kind == "explain" and not code, r
        assert not _LEAK.search(text), text
    elif check == "code_edit":
        assert kind == "code_edit" and code, r


def test_run_b_exact_message_gets_the_fixed_reply():
    """Primary regression: Run B's own words now get refuse (e) verbatim."""
    r = _turn(RUN_B_FASTER_WAY)
    assert r["response_message"] == REFUSE_E, r


def test_candidates_own_approach_can_be_discussed():
    r = _turn("Would that be faster than my sort?", conversation=CANDIDATE_STATED)
    assert r["response_message"] != REFUSE_E, r
