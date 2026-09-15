"""
ROUND 5 -- PROGRESSIVE ENGINEERING (POC). Phase 4: the Deterministic
Policy Core. See models_progressive.py (Phase 1), progressive_service.py
(Phase 2), progressive_llm.py (Phase 3), and this session's Round 5
architecture-discovery notes for the full plan.

PURELY DETERMINISTIC - NO LLM CALL ANYWHERE IN THIS MODULE. This is the
layer that decides the assistance CEILING before the Generator (Phase 3)
ever runs, so permission is never something the model grants itself from
inside its own prompt. It is a phrase/pattern classifier over the
candidate's own request text - honestly scoped the same way
poc_ai_output_detector.py's checks are: good at flagging clear,
recognizable request SHAPES ("give me edge cases", "solve this
completely"), not a claim of full natural-language understanding. A
request this module can't confidently place lands on CLARIFY, the
safest possible ceiling, never on a permissive one by default - see
_classify's fallback behavior.

WHAT THIS MODULE NEVER TOUCHES, BY CONSTRUCTION: it has no field or
parameter anywhere for hidden_tests_json, reference_solution, or any
requirement beyond the current one - PolicyInput carries only
current_stage (an int) and current_requirement (the SAME text the
Generator is already trusted with, per Phase 3). This module never
queries the database itself; a future caller (a route, not built yet)
is responsible for supplying trusted state, e.g. via
progressive_service.get_current_requirement - same isolation discipline
as Phase 3, reused rather than re-derived here.

ISOLATION: imports nothing from Round 3, Phase 1, or Phase 2, and is
imported by nothing in Round 1-4. Does not modify progressive_service.py
or progressive_llm.py (Phases 2/3) - a future phase wires this module's
decision into the Generator call, not built here.
"""
from dataclasses import dataclass, field
from typing import Optional

# ---- Closed taxonomy ----

EXPLAIN = "EXPLAIN"
CLARIFY = "CLARIFY"
NARROW_EDIT = "NARROW_EDIT"
RUN_CANDIDATE_INPUT = "RUN_CANDIDATE_INPUT"
REFUSE_COMPLETE_SOLUTION = "REFUSE_COMPLETE_SOLUTION"
REFUSE_TEST_GENERATION = "REFUSE_TEST_GENERATION"
REFUSE_EDGE_CASE_GENERATION = "REFUSE_EDGE_CASE_GENERATION"
REFUSE_FUTURE_REQUIREMENT = "REFUSE_FUTURE_REQUIREMENT"
REFUSE_HIDDEN_TEST_REFERENCE_LEAK = "REFUSE_HIDDEN_TEST_REFERENCE_LEAK"
REFUSE_CANDIDATE_REASONING = "REFUSE_CANDIDATE_REASONING"

ALL_CATEGORIES = {
    EXPLAIN, CLARIFY, NARROW_EDIT, RUN_CANDIDATE_INPUT,
    REFUSE_COMPLETE_SOLUTION, REFUSE_TEST_GENERATION, REFUSE_EDGE_CASE_GENERATION,
    REFUSE_FUTURE_REQUIREMENT, REFUSE_HIDDEN_TEST_REFERENCE_LEAK, REFUSE_CANDIDATE_REASONING,
}

_REFUSE_CATEGORIES = {
    REFUSE_COMPLETE_SOLUTION, REFUSE_TEST_GENERATION, REFUSE_EDGE_CASE_GENERATION,
    REFUSE_FUTURE_REQUIREMENT, REFUSE_HIDDEN_TEST_REFERENCE_LEAK, REFUSE_CANDIDATE_REASONING,
}

_REASON_CODES = {
    EXPLAIN: "explanation_request",
    CLARIFY: "ambiguous_or_short_request",
    NARROW_EDIT: "narrow_edit_request",
    RUN_CANDIDATE_INPUT: "candidate_supplied_execution_request",
    REFUSE_COMPLETE_SOLUTION: "complete_solution_request",
    REFUSE_TEST_GENERATION: "test_case_generation_request",
    REFUSE_EDGE_CASE_GENERATION: "edge_case_generation_request",
    REFUSE_FUTURE_REQUIREMENT: "future_requirement_request",
    REFUSE_HIDDEN_TEST_REFERENCE_LEAK: "hidden_test_or_reference_request",
    REFUSE_CANDIDATE_REASONING: "candidate_reasoning_request",
}

_CEILING_DESCRIPTIONS = {
    EXPLAIN: "Explanation only - describe what's happening and why, no code change.",
    CLARIFY: "Ask one clarifying question - no code change, nothing invented.",
    NARROW_EDIT: "Make exactly the specific, narrow code change requested - nothing beyond it.",
    RUN_CANDIDATE_INPUT: "Execute the code using ONLY the exact input values the candidate supplied.",
    REFUSE_COMPLETE_SOLUTION: "Refuse - direct the candidate to specify what they want built.",
    REFUSE_TEST_GENERATION: "Refuse - test-case selection belongs to the candidate.",
    REFUSE_EDGE_CASE_GENERATION: "Refuse - edge-case identification belongs to the candidate.",
    REFUSE_FUTURE_REQUIREMENT: "Refuse - future-stage information is not available to disclose.",
    REFUSE_HIDDEN_TEST_REFERENCE_LEAK: "Refuse - hidden test / reference solution content must never be disclosed.",
    REFUSE_CANDIDATE_REASONING: "Refuse - deciding the approach/algorithm/solution is the candidate's own job, not the assistant's.",
}

# Priority order the classifier checks patterns in - most security-critical
# first, so a message that could plausibly match more than one category
# (e.g. "explain the reference solution to me") resolves toward the more
# restrictive one, never the more permissive one.
_PRIORITY_ORDER = [
    REFUSE_HIDDEN_TEST_REFERENCE_LEAK,
    REFUSE_FUTURE_REQUIREMENT,
    REFUSE_COMPLETE_SOLUTION,
    REFUSE_CANDIDATE_REASONING,
    REFUSE_TEST_GENERATION,
    REFUSE_EDGE_CASE_GENERATION,
    RUN_CANDIDATE_INPUT,
    EXPLAIN,
]

_PATTERNS = {
    REFUSE_HIDDEN_TEST_REFERENCE_LEAK: (
        "expected solution", "reference solution", "reference answer", "hidden test",
        "will this pass", "correct answer", "model solution", "official solution",
        "model answer", "grading solution", "answer key",
    ),
    REFUSE_FUTURE_REQUIREMENT: (
        "next requirement", "next stage", "what comes after", "what's next",
        "future requirement", "upcoming requirement", "what will stage", "after this stage",
        "later stage", "what happens after", "subsequent requirement", "subsequent stage",
    ),
    REFUSE_COMPLETE_SOLUTION: (
        "solve this completely", "solve the whole", "write the whole", "write the complete",
        "complete solution", "entire solution", "whole function", "whole program",
        "solve this for me", "do this for me", "build the whole thing", "write the entire",
        "give me the solution", "solve it for me", "write the full", "complete implementation",
        "full implementation", "implement the whole",
    ),
    REFUSE_CANDIDATE_REASONING: (
        "do the reasoning for me", "do my reasoning for me", "reasoning for me",
        "figure out the algorithm for me", "figure out the algorithm",
        "work out the solution for me", "work out the algorithm for me",
        "solve the logic for me", "do the logic for me",
        "decide how i should solve", "decide how i should approach", "decide my approach",
        "tell me what approach i should", "tell me which approach i should",
        "tell me what solution i should", "tell me which solution i should",
    ),
    REFUSE_TEST_GENERATION: (
        "test cases", "test case", "give me tests", "write tests", "generate tests",
        "create test data", "sample test data", "some test values", "write some tests",
        "sample data", "values to test",
    ),
    REFUSE_EDGE_CASE_GENERATION: (
        "edge cases", "edge case", "corner cases", "corner case",
        "what could go wrong", "what should i watch out for", "what should i worry about",
    ),
    RUN_CANDIDATE_INPUT: (
        "run this exact input", "run this input", "run with the value", "run it with",
        "execute this input", "test with the value", "run this on", "execute with input",
    ),
    EXPLAIN: (
        "explain", "why does", "why is", "why do", "what does this mean",
        "what is the difference", "help me understand", "what's wrong with",
    ),
}

# A short/bare message is read as a likely answer-to-a-clarify rather than
# a fresh, specific instruction - same reasoning Phase 2's hardening
# applied to effective_candidate_instruction. Purely a word-count
# heuristic, honestly limited (see module docstring).
_CLARIFY_ANSWER_MAX_WORDS = 3
_DANGLING_REFERENCE_PHRASES = ("fix it", "handle it", "make it work", "deal with it", "do it")


@dataclass
class PolicyInput:
    """Trusted state only - see module docstring on what this
    deliberately does NOT carry (hidden tests, reference solutions,
    future requirements)."""
    current_stage: int
    current_requirement: str
    candidate_request: str
    # "response/workflow context": was the immediately preceding
    # generator turn a clarifying question? Mirrors Round 3's
    # continuation-check reasoning.
    previous_response_kind: Optional[str] = None
    # "previous AI interaction state": if the preceding turn was a
    # clarify, the original instruction it was answering - combined with
    # candidate_request for classification, same as Phase 2's
    # effective_candidate_instruction.
    effective_candidate_instruction: Optional[str] = None
    # "permitted assistance configuration": HR/problem-level overrides.
    # Minimal for this phase - a set of category names this problem
    # disables outright (e.g. a problem with no meaningful "run" action).
    # A disabled category never gets granted; the classifier just falls
    # back to CLARIFY instead.
    disabled_categories: frozenset = field(default_factory=frozenset)


@dataclass
class PolicyDecision:
    category: str
    generation_allowed: bool
    refusal_required: bool
    reason_code: str
    ceiling_description: str


def _contains_any(text: str, phrases: tuple) -> bool:
    lowered = text.lower()
    return any(p in lowered for p in phrases)


def _looks_like_a_clarify_answer(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if len(stripped.split()) <= _CLARIFY_ANSWER_MAX_WORDS:
        return True
    return _contains_any(stripped, _DANGLING_REFERENCE_PHRASES)


def _classify(effective_text: str) -> str:
    for category in _PRIORITY_ORDER:
        if _contains_any(effective_text, _PATTERNS[category]):
            return category
    # Nothing matched a known refuse/explain/run pattern. Checked against
    # the EFFECTIVE (combined) text, not just the current turn's own
    # fragment - a bare "inputValues" combined with a real prior
    # instruction is not short/bare, even though the current message
    # alone would be. Same "primary request" reasoning as Phase 2's
    # hardening. Anything genuinely short/bare gets the safest ceiling;
    # anything else with real content is a narrow, actionable instruction.
    if _looks_like_a_clarify_answer(effective_text):
        return CLARIFY
    return NARROW_EDIT


def decide(policy_input: PolicyInput) -> PolicyDecision:
    """The one entry point: classify policy_input.candidate_request (read
    together with effective_candidate_instruction, if present) into the
    closed taxonomy above, and return the ceiling BEFORE any generation
    happens. Never calls an LLM."""
    effective_text = (
        f"{policy_input.effective_candidate_instruction} {policy_input.candidate_request}"
        if policy_input.effective_candidate_instruction
        else policy_input.candidate_request
    )

    category = _classify(effective_text)

    if category in policy_input.disabled_categories:
        category = CLARIFY

    return PolicyDecision(
        category=category,
        generation_allowed=category not in _REFUSE_CATEGORIES,
        refusal_required=category in _REFUSE_CATEGORIES,
        reason_code=_REASON_CODES[category],
        ceiling_description=_CEILING_DESCRIPTIONS[category],
    )
