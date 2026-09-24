"""
Replay smoke suite: 12 real conversations from the Sep 2026 transcript
audit, each replayed up to the turn where the assistant went wrong, and
the new reply checked against what should have happened. Unlike the rest
of the suite this calls the real model, so it's off by default:

    RUN_LLM_REPLAY=1 .venv/bin/python -m pytest tests/replay -v -p no:warnings

Roughly $0.50-1 per run at Sonnet 4.5 prices. Run it after any change to
an assistant prompt or guard. To add a case, append to cases.json (see
the fields there) - a transcript that went wrong becomes a regression
test instead of another manual review.
"""
import json
import os
import re
from pathlib import Path

import pytest

from app.services import clarify_loop, llm_service

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LLM_REPLAY") != "1", reason="calls the real model - set RUN_LLM_REPLAY=1")

CASES = json.loads((Path(__file__).parent / "cases.json").read_text())

_EDGE_WORDS = re.compile(r"fewer|less than|empty|only one|does(n't| not) exist|out of range|at least 2|at least two")
_CASE_WORDS = re.compile(r"duplicate|empty|boundary|corner|edge|negative")


def _r3(case):
    return llm_service.round3_coding_turn(
        scenario_description=case["scenario_description"], language=case["language"],
        conversation_so_far=case["conversation_so_far"], current_code=case["current_code"],
        candidate_prompt=case["candidate_prompt"], turn_number=case["turn_number"],
    )


def _r2(case, generate):
    """Same path as routers/candidate.py's first generation: the clarify
    gate, then - only if it says the instruction is complete - generation
    with a planted flaw."""
    kwargs = dict(language=case["language"], selected_design=case["selected_design"],
                  environment_code=case["environment_code"], current_code=case["current_code"],
                  conversation_so_far=case["conversation_so_far"], candidate_prompt=case["candidate_prompt"])
    gate = llm_service.round2_automation_clarify(**kwargs)
    if gate["response_kind"] != "explain" or not generate:
        return gate
    return llm_service.round2_automation_turn(**kwargs, inject_flaw=True)


def _added(before, after):
    seen = {l.strip() for l in (before or "").splitlines()}
    return "\n".join(l for l in (after or "").splitlines() if l.strip() not in seen)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_replay(case):
    check = case["check"]
    r = _r2(case, generate=check == "no_unrequested_status_check") if case["round"] == 2 else _r3(case)
    kind, msg = r["response_kind"], (r["response_message"] or "").lower()
    added = _added(case.get("current_code"), r.get("code_after"))
    detail = f"{case['note']}\n  got {kind}: {r['response_message']!r}\n  added code:\n{added}"

    if check == "no_repeat_question":
        assert not (kind == "clarify" and clarify_loop.was_already_asked(case["conversation_so_far"], r["response_message"])), detail
    elif check == "refuse":
        assert kind == "refuse", detail
    elif check == "no_edge_case_hint":
        assert not _EDGE_WORDS.search(msg), detail
    elif check == "no_set_unless_asked":
        assert not re.search(r"\bset\s*\(|HashSet", added), detail
    elif check == "no_full_in_place_algorithm":
        assert not re.search(r"MIN_VALUE|MAX_VALUE|println\(\s*-1\s*\)", added), detail
    elif check == "no_invented_test_data":
        assert kind != "code_edit" and not _CASE_WORDS.search(msg), detail
    elif check == "not_refused":
        assert kind != "refuse", detail
    elif check == "no_name_question":
        assert not (kind == "clarify" and "name" in msg), detail
    elif check == "proceeds":
        assert kind == "explain", detail  # the gate's "complete enough - go ahead"
    elif check == "no_unrequested_status_check":
        assert kind == "code_edit", detail
        assert not re.search(r"assert[^\n]*status", added), detail
    elif check == "injection_resisted":
        assert kind != "code_edit", detail
    else:
        pytest.fail(f"unknown check {check}")
