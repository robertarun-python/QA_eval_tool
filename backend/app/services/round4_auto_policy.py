"""
AI-Assisted Test Automation round - deterministic controls. NO LLM CALL
ANYWHERE IN THIS MODULE, by the same discipline as round3_policy.py,
round4_pilot_policy.py and progressive_policy.py.

Two independent controls, both deterministic, because "the prompt tells
the model not to" is not by itself an enforceable boundary:

1. PRE-GENERATION (is_prohibited): refuses a request whose SHAPE is
   asking the assistant to supply the candidate's own design work -
   inventing test cases, test data, assertions, expected results, or
   extra coverage. Runs before llm_service.round4_auto_turn ever calls
   the API, so a matched request costs zero tokens and can't be talked
   past.

2. POST-GENERATION (untraceable_literals): the assistant is only ever
   allowed to encode values the candidate ALREADY specified in their own
   Round 1 design. This extracts the literal values out of generated code
   and reports any that don't appear anywhere in the candidate's
   immutable Round 1 snapshot. Recorded as scoring evidence rather than
   hard-blocking the turn - a literal can legitimately come from the
   provided helper environment (an endpoint path, a helper's own message
   string), so this is a signal for the scorer and for HR, not a verdict.
   Same "evidence, not verdict" split poc_ai_output_detector.py uses.

Deliberately narrow, and honest about it: substring/regex matching over
known request shapes, not natural-language understanding.
"""
import re

REFUSAL_MESSAGE = (
    "That part is yours to decide, not mine - I can only automate the test design you already "
    "wrote in Round 1. Tell me which of your own steps, data or expected results to encode, or "
    "ask me to explain or fix something specific in the code."
)

_PROHIBITED_PATTERNS = (
    # Inventing cases / coverage
    "what else should i test", "what other test", "add more test", "more test cases",
    "suggest test cases", "come up with test", "think of test", "additional test cases",
    "what am i missing", "improve my coverage", "add coverage", "more coverage",
    "what edge cases", "suggest edge cases", "add edge cases",
    # Inventing data
    "make up test data", "invent test data", "invent sample data", "make up sample data",
    "choose the test data", "decide the test data", "pick the test data",
    "what data should i use", "what values should i use", "give me test data",
    "some test values", "generate test data",
    # Inventing assertions / expected results
    "what should i assert", "what assertions should", "decide the assertions",
    "add assertions for me", "write the assertions for me", "what should the expected result",
    "decide the expected result",
    # Wholesale handoff
    "write the whole test", "write the complete test", "do this whole thing for me", "do it all for me",
    # Delegating the decision itself (Sep 2026 guardrail review)
    "you decide", "decide for me", "whatever makes sense", "come up with",
    "inputs yourself", "data yourself", "values yourself", "cases yourself", "checks yourself", "assertions yourself",
    "solve this for me", "design the test for me", "figure out what to test",
)

# A quoted string or a bare number in generated code. Deliberately simple:
# it over-collects (helper names, paths, format strings), which is why the
# result is evidence for a scorer rather than an automatic failure.
_LITERAL_RE = re.compile(r"""["']([^"'\n]{2,})["']|(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])""")

# Values that are part of the provided environment or ordinary programming
# noise, never "invented test data" - excluded so the signal stays useful.
_IGNORED_LITERALS = {
    "0", "1", "2", "-1", "true", "false", "none", "null", "self", "main", "__main__",
    "utf-8", "/", "", " ", "\\n",
}


def is_prohibited(candidate_prompt: str) -> bool:
    lowered = (candidate_prompt or "").lower()
    return any(p in lowered for p in _PROHIBITED_PATTERNS)


def _snapshot_text(selected_rows: list[dict]) -> str:
    """Everything the candidate themselves authored in Round 1 for the
    rows they chose, plus any append-only refinement notes - the full set
    of values the assistant is allowed to encode."""
    parts = []
    for row in selected_rows or []:
        for key in ("title", "preconditions", "steps", "test_data", "expected_result"):
            value = row.get(key)
            if value:
                parts.append(str(value))
        for note in row.get("refinements") or []:
            parts.append(str(note))
    return " ".join(parts).lower()


def untraceable_literals(code: str, selected_rows: list[dict], environment_code: str = "") -> list[str]:
    """Literal values present in `code` that appear neither in the
    candidate's own Round 1 snapshot nor in the provided environment.
    Order-preserving, de-duplicated, capped - this is a reviewable signal,
    not an exhaustive audit."""
    if not code:
        return []
    haystack = _snapshot_text(selected_rows) + " " + (environment_code or "").lower()
    found: list[str] = []
    seen: set[str] = set()
    for quoted, number in _LITERAL_RE.findall(code):
        literal = (quoted or number).strip()
        key = literal.lower()
        if not literal or key in _IGNORED_LITERALS or key in seen:
            continue
        seen.add(key)
        if key not in haystack:
            found.append(literal)
        if len(found) >= 25:
            break
    return found
