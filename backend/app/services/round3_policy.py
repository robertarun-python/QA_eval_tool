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


# "Finish it for me" shapes - asking the assistant to complete the rest of
# the solution rather than naming the next step. None of these were caught
# before the Sep 2026 guardrail review; all relied on the model refusing.
_FINISH_IT_RE = re.compile(
    r"\b(do|finish|complete|write|fill in)\s+(the\s+)?(rest|remaining(\s+\w+)?|missing\s+(part|parts|logic|code|bits?|pieces?))\b"
    r"|\bfinish\s+(the|this|my)\s+(program|code|task|solution|logic)\b"
    r"|\bmake\s+it\s+work\s+for\s+(all|every|any)\b"
    r"|\bhandle\s+(all\s+)?(the\s+)?(edge|corner)\s+cases\b(?!\s*(where|when|by|like|such|:))"
    r"|\boptimi[sz]e\s+(it|this|the\s+(code|program|solution))\b(?!\s+(by|using|with))"
    r"|\b(write|give|implement)\s+(me\s+)?the\s+logic\s+(to|for)\b"
    r"|\bimplement\s+(the|a)\s+(solution|program)\b"
    r"|\b(do|write|build)\s+the\s+whole\s+thing\b"
)

# Signs the candidate is directing a concrete technique rather than handing
# over the task - "write a program that reads two integers from stdin" or
# "solve it using a for loop over nums" name what to do and are not refused
# here (the model and the scope guard still judge them).
_CONCRETE_STEP_RE = re.compile(
    r"\bstdin\b|\binput\s*\(|\bsplit\b|\bcomma|\bspace[- ]separated\b|\bone per line\b"
    r"|\b(variable|list|array|set|dict|map|function)\s+(called|named)\b|\b[a-z_]\w*\s*=\s*\S"
    r"|\b(for|while)\s+loop\b|\bappend\b|\bscanner\b|\bparseint\b|%|\bmodulo\b"
)


def is_whole_task_request(text: str) -> bool:
    lowered = (text or "").lower()
    if _FINISH_IT_RE.search(lowered):
        return True
    return bool(_WHOLE_TASK_RE.search(lowered)) and not _CONCRETE_STEP_RE.search(lowered)


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


_PUBLIC_CLASS_RE = re.compile(r"\bpublic\s+(?:final\s+|abstract\s+)*class\s+([A-Za-z_]\w*)")


def ensure_java_main_class(code: str) -> str:
    """The Java runner compiles Main.java, so the public class must be named
    Main - the prompt says so, but transcript review found the model still
    naming it Solution (R3 submissions 19 and 22, which then failed to
    compile). When there's exactly one public class under another name,
    rename it (and its other references); otherwise leave the code alone."""
    names = _PUBLIC_CLASS_RE.findall(code or "")
    if len(names) != 1 or names[0] == "Main":
        return code
    return re.sub(r"\b" + re.escape(names[0]) + r"\b", "Main", code)
