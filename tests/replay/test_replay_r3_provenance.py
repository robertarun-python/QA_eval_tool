"""
Replay: Round 3 idea provenance (first mention) against the real scorer.
Same opt-in as test_replay_smoke.py - it calls the real model:

    RUN_LLM_REPLAY=1 .venv/bin/python -m pytest tests/replay -v -p no:warnings

Each case is a whole transcript scored by llm_service.score_round3_coding.
A/B/C share the same final code and test results and differ only in who
first introduced the single-pass idea; RunB_submission_193 is the real Run B
transcript where the assistant explained the approach first. Assertions are
structural (is the idea flagged as the assistant's, does the feedback claim
the candidate found it themselves), never exact scores.

The task's hidden tests and reference approach are its answer key, so they
are not in the fixture: test_results carries only each test's pass/fail, and
the scorer gets WITHHELD_APPROACH instead of the real expected_approach.
Provenance is judged from the transcript, which is unchanged.
"""
import json
import os
import re
from pathlib import Path

import pytest

from app.services import llm_service

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LLM_REPLAY") != "1", reason="calls the real model - set RUN_LLM_REPLAY=1")

DATA = json.loads((Path(__file__).parent / "r3_provenance_cases.json").read_text())
WITHHELD_APPROACH = "(withheld from this regression fixture - judge efficiency from the transcript alone)"

_ASSISTANT_INTRODUCED = re.compile(r"assistant introduced", re.I)
_SELF_DISCOVERY = re.compile(r"\bindependent(ly)?\b|\bproactive(ly)?\b|on (your|their) own", re.I)
# Only a sentence about the adopted idea itself counts - "independent
# judgment in diagnosing the negative number bug" is praise for the
# candidate's own work, not a provenance claim.
_IDEA = re.compile(r"optimi|O\(n\)|faster|efficien|single[- ]pass|two[- ]pass|one pass|approach|algorithm", re.I)
# A sentence that denies it ("wasn't your independent discovery"), sets it
# as an expectation ("we'd expect you to ... proactively"), or attributes
# it to the assistant is not a claim that the candidate found it.
_NOT_A_CLAIM = re.compile(
    r"\bnot\b|n't\b|\brather than\b|\binstead\b|\bexpect|\bshould\b|\bwould\b|'d\b|\bassistant\b|suggest|introduc", re.I
)


def _claims_self_discovery(feedback: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", feedback or "")
    return [s for s in sentences if _SELF_DISCOVERY.search(s) and _IDEA.search(s) and not _NOT_A_CLAIM.search(s)]


@pytest.mark.parametrize("case", DATA["cases"], ids=[c["id"] for c in DATA["cases"]])
def test_r3_idea_provenance(case):
    result = llm_service.score_round3_coding(
        scenario_description=DATA["scenario_description"], expected_approach=WITHHELD_APPROACH,
        conversation_so_far=case["conversation_so_far"], test_results=case["test_results"],
    )
    flagged = [v for v in result.get("guardrail_violations", []) if _ASSISTANT_INTRODUCED.search(v)]
    if case["expect"] == "flagged":
        assert flagged, f"assistant-introduced idea not flagged: {result.get('guardrail_violations')}"
        assert not _claims_self_discovery(result.get("feedback_text")), result.get("feedback_text")
    else:
        assert not flagged, f"candidate's own idea attributed to the assistant: {flagged}"
