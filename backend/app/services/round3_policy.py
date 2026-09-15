"""
Round 3 - deterministic pre-generation policy check. Same discipline as
Round 5's progressive_policy.py (phrase/pattern matching, no LLM call
anywhere in this module) but standalone - no import of or from any Round 5
module, matching this codebase's per-round isolation convention.

Closes the QA validation finding that Round 3 previously relied entirely on
the model to self-classify a prohibited request as "refuse" from inside its
own prompt - llm_service.round3_coding_turn now checks this BEFORE ever
calling _raw_turn (the only path that reaches _call_claude), so a matched
request never reaches the Generator at all.

Deliberately narrow (substring match on known request SHAPES, not full NLU)
- same honestly-scoped limitation as progressive_policy.py. Requests that
don't match a pattern here still go through the model's own self-
classification exactly as before; this only strengthens the specific
phrasings a candidate is most likely to actually type.
"""

REFUSAL_MESSAGE = (
    "I can't do that for you - tell me the specific, narrow thing you want help with, "
    "and I'll do exactly that."
)

_PROHIBITED_PATTERNS = (
    "solve this for me",
    "write the complete solution",
    "give me the algorithm",
    "generate test cases",
    "give me edge cases",
    "invent sample data",
    "do the reasoning for me",
    "decide how i should solve",
)


def is_prohibited(candidate_prompt: str) -> bool:
    lowered = (candidate_prompt or "").lower()
    return any(p in lowered for p in _PROHIBITED_PATTERNS)
