"""
Round 4 pilot ("Focused Automation Pilot") - deterministic pre-generation
policy check, same discipline as round3_policy.py / progressive_policy.py:
phrase/pattern matching, no LLM call anywhere in this module, runs BEFORE
llm_service.round4_pilot_turn ever calls the Generator. Standalone - no
import of or from round3_policy.py/progressive_policy.py or any R3/R5
module, matching this codebase's per-round isolation convention.

Scope is the ALLOWED/NOT ALLOWED split for this specific round: the AI is
a narrow coding assistant for an EXISTING automation codebase (explain
code/helpers/errors, narrow code changes) - never the one deciding the
candidate's test strategy, test data, or writing the whole solution.
"""

REFUSAL_MESSAGE = (
    "I can't do that for you - understanding the existing code, deciding what to test, "
    "and validating the result are your job here. Ask me something narrower (explain a "
    "helper, interpret an error, or a specific small change) and I'll help with that."
)

_PROHIBITED_PATTERNS = (
    "give me the complete solution",
    "write the complete test",
    "write the full solution",
    "write the entire test",
    "implement the whole",
    "solve this for me",
    "do this for me",
    "design the test strategy",
    "design my test strategy",
    "generate the complete test suite",
    "generate all the test cases",
    "decide the test data for me",
    "decide what test data",
    "decide my test data",
    "what should my final answer be",
    "make the decision for me",
)


def is_prohibited(candidate_prompt: str) -> bool:
    lowered = (candidate_prompt or "").lower()
    return any(p in lowered for p in _PROHIBITED_PATTERNS)
