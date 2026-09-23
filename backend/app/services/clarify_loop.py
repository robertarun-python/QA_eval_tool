"""
Shared clarifying-question loop breaker for the R3 coding assistant and
the R2 automation clarify gate. No LLM calls.

Transcript review (Sep 2026) found clarification loops were the single
biggest failure: 54 of 129 thrown-away assistant turns, including the
same question asked 6-9 times word for word and questions continuing
after "this is enough, give the code". Three deterministic signals end a
streak: a question already asked, a candidate who has said they're done,
and too many unresolved questions in a row.
"""
import re

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def normalize_question(text: str | None) -> str:
    return _NON_WORD_RE.sub(" ", (text or "").lower()).strip()


def clarify_streak(conversation_so_far: list[dict]) -> int:
    """How many of the most recent turns in a row were clarifying questions."""
    streak = 0
    for turn in reversed(conversation_so_far or []):
        if turn.get("response_kind") != "clarify":
            break
        streak += 1
    return streak


def was_already_asked(conversation_so_far: list[dict], question: str | None) -> bool:
    asked = {
        normalize_question(t.get("response_message"))
        for t in conversation_so_far or [] if t.get("response_kind") == "clarify"
    }
    return bool(question) and normalize_question(question) in asked


# "I'm done" signals. Strong ones only ever mean "stop asking", wherever they
# appear. Weak ones ("enough", "proceed", "only this", "no more", "go ahead",
# a bare "no") also occur inside ordinary test steps - "then it should
# proceed to the next page", "no more than 5 doctors", "shows only this
# text" - so they count only when they're essentially the whole message.
_STRONG_DONE_RE = re.compile(
    r"\b(that'?s (it|all|enough|everything|final)|this is (it|enough|final|all)|is final\b|"
    r"nothing (more|else)|all done|i'?m done|give (me )?the code|write the code|generate the code|"
    r"complete the test ?case|ignore (this|that|the earlier|earlier|what i said)|use only|"
    r"covered every ?thing|no,? (that'?s )?(all|it)\b)"
)
_WEAK_DONE_RE = re.compile(
    r"\b(enough|proceed|go ahead|only (this|that|these|those|check)|just (this|that)|no more|final|no)\b"
)
_WEAK_MAX_WORDS = 4


def is_done_signal(candidate_prompt: str | None) -> bool:
    text = (candidate_prompt or "").lower()
    if _STRONG_DONE_RE.search(text):
        return True
    return len(re.findall(r"[a-z0-9']+", text)) <= _WEAK_MAX_WORDS and bool(_WEAK_DONE_RE.search(text))


def should_stop_clarifying(
    conversation_so_far: list[dict], candidate_prompt: str, next_question: str | None, max_streak: int,
) -> bool:
    """True when asking `next_question` would continue a loop rather than
    help. Only applies once at least one question has been asked, so a
    first, genuinely unclear instruction still gets its question."""
    streak = clarify_streak(conversation_so_far)
    if streak == 0:
        return False
    return (
        was_already_asked(conversation_so_far, next_question)
        or is_done_signal(candidate_prompt)
        or streak >= max_streak
    )
