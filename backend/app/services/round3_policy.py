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
import re

REFUSAL_MESSAGE = (
    "I can't do that for you - tell me the specific, narrow thing you want help with, "
    "and I'll do exactly that."
)

# Same wording as round3_coding_turn.txt's refuse (c), so a deterministic
# refusal and a model refusal read identically to the candidate.
WHOLE_TASK_REFUSAL_MESSAGE = "I can't write this for you - tell me what you want built, and I'll write exactly that."

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


# "Build the whole thing" requests - the shapes round3_coding_turn.txt's
# refuse (c) describes, checked here so it doesn't rest on the model alone.
# Phrased as requests for a whole program/solution, so "write the code for
# the loop" or "a program that reads..." inside a concrete instruction
# don't match.
_WHOLE_TASK_RE = re.compile(
    r"\b(write|give|create|build|generate|make|provide)\b[^.?!]{0,20}?\b(a|the|me a|me the|full|complete|entire|whole)\s+"
    r"(full |complete |entire |whole |working )?(program|solution)\b"
    # "code" only when it's the whole thing - "write the code for the loop" is a normal instruction
    r"|\b(write|give|create|generate|provide)\b[^.?!]{0,20}?\b(the |me the |full |complete |entire |whole )+code\b"
    r"(?!\s+(for|to|that|which|in)\s+(the\s+|this\s+|that\s+)?(loop|step|line|part|function|method|variable|check|condition|if|print|input))"
    r"|\bsolve (this|it|the problem|the question)\b"
)


def is_whole_task_request(text: str) -> bool:
    return bool(_WHOLE_TASK_RE.search((text or "").lower()))


def is_continuation_of_whole_task(conversation_so_far: list[dict], candidate_prompt: str) -> bool:
    """Closes a two-step bypass found in transcript review: a whole-task
    request ("write a program to check odd or even and print it") gets a
    clarifying question, the candidate answers in one word ("cli"), and
    the combined instruction was then treated as a normal code_edit - the
    full solution, from what was really still a solve-it-for-me request.
    If the new message answers a clarifying question (or a chain of them)
    that was about a whole-task request, the answer is refused the same
    way the original request would have been."""
    if is_whole_task_request(candidate_prompt):
        return True
    for turn in reversed(conversation_so_far or []):
        if turn.get("response_kind") != "clarify":
            return False
        if is_whole_task_request(turn.get("candidate_prompt")):
            return True
    return False
