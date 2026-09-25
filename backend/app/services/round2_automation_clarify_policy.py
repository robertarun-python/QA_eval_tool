"""
AI-Assisted Test Automation round - deterministic half of the
specification-sufficiency gate (see llm_service.round2_automation_clarify for
the LLM half). NO LLM CALL ANYWHERE IN THIS MODULE, same discipline as
round2_automation_policy.py. Three jobs: a pre-filter that short-circuits an
obviously-empty instruction before any LLM call is made
(is_placeholder_instruction); the decision rule turning the LLM's bounded
classification into an actual response (build_clarify_response); and the
anti-leakage guard on whatever question ends up used (contains_forbidden_vocab).

Deliberately its own module, not a reuse of round3_constructs.py: R3's
taxonomy (variable/loop/function/...) tracks HR-configured per-scenario
required constructs across a whole conversation, none of which applies
here. R2 has exactly one decision axis worth protecting - which layer of
the provided environment (UI vs API vs Database) an instruction should
go through - fixed and universal across every automation scenario, not
scenario-configured. Same PATTERN as R3 (draft a question, then
deterministically verify it doesn't name the thing being protected,
falling back to a hardcoded neutral question if it does), intentionally
without R3's per-category state-tracking machinery, which has nothing to
track here.

Grounded directly in the provided environment's own class names (see
prompts/round2_automation_helpers_{python,java,javascript}.txt, all three of
which expose UI/API/Database as the actual class names candidates code
against) - language-INDEPENDENT, unlike R3's vocabulary: the risk here is
the ENGLISH clarifying question naming a layer, not code syntax varying
by language.
"""
import re

from . import clarify_loop

# Word-boundary-matched phrases (multi-word entries are matched as a
# whole phrase) plus a couple of dotted-name substrings (matched as plain
# containment, same reasoning as round3_constructs.contains_forbidden_vocab
# for symbol-bearing tokens like "int(").
_FORBIDDEN_PHRASES = {
    "ui", "api", "db", "database",
    "user interface", "front end", "frontend", "back end", "backend",
    "ui layer", "api layer", "db layer", "database layer",
    "through the ui", "through the api", "through the database",
    "application programming interface",
}
_FORBIDDEN_SUBSTRINGS = {"ui.", "api.", "database."}

# One hardcoded, category-neutral fallback - used whenever the LLM's own
# drafted question leaks (see contains_forbidden_vocab). Asks about the
# outcome/requirement, never the mechanism that would satisfy it - same
# register as round3_constructs.FALLBACK_QUESTIONS.
FALLBACK_QUESTION = "What exactly should happen when this step runs, and what should prove it worked?"


def contains_forbidden_vocab(text: str) -> bool:
    lowered = (text or "").lower()
    for phrase in _FORBIDDEN_PHRASES:
        if re.search(r"\b" + re.escape(phrase) + r"\b", lowered):
            return True
    return any(s in lowered for s in _FORBIDDEN_SUBSTRINGS)


# Generic filler an instruction can be made of and still say nothing at
# all ("test it", "go", "do it now", "please automate this", "I want to
# automate the given test case, please do" - a real one observed live,
# padded with enough filler words around "just do it" that it originally
# slipped past a narrower version of this list straight to the LLM,
# which filled the resulting gap with the candidate's OWN design
# specifics instead of asking a genuinely open question). Deliberately
# tiny and conservative: this only exists to catch the degenerate case at
# zero LLM cost, never to judge a real instruction's completeness - a
# short but concrete instruction ("confirm login fails") has content
# words left after stripping this list and is correctly NOT caught here;
# only the LLM classification step is trusted to judge those.
_PLACEHOLDER_WORDS = {
    "test", "it", "this", "that", "please", "go", "do", "automate",
    "run", "check", "now", "the", "a", "an", "and", "to", "for", "my", "it's", "its",
    "i", "you", "we", "want", "need", "would", "like", "help", "me", "us",
    "given", "case", "cases", "one", "can", "could", "should", "write",
    "make", "kindly", "possible", "just",
}


def is_placeholder_instruction(text: str) -> bool:
    """True only when NO content word survives stripping generic filler -
    an instruction this empty cannot possibly be complete regardless of
    what it's about, so it's safe to short-circuit before ever calling
    the LLM (see llm_service.round2_automation_clarify)."""
    words = re.findall(r"[a-zA-Z']+", (text or "").lower())
    return all(w in _PLACEHOLDER_WORDS for w in words) if words else True


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_value(text: str | None) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "").strip().lower())


# The exact fixed phrase build_clarify_response's contradicts_prior
# branch always ends its response_message with - the only place this
# wording is ever generated, so matching it in a PRIOR turn's own
# response_message is a reliable, deterministic way to count how many
# times this conversation has already hit contradicts_prior. See
# _contradicts_prior_count.
_CONTRADICTS_PRIOR_MARKER = "for the same thing - which one should the automation use?"


def contradicts_prior_count(conversation_so_far: list[dict]) -> int:
    """How many prior turns in this conversation were already classified
    contradicts_prior. Used by llm_service.round2_automation_clarify as a
    deterministic circuit breaker: verified live that the LLM classifier
    can get stuck re-flagging a candidate's own already-stated, already-
    settled scope choice as an unresolved contradiction indefinitely,
    with no way for the candidate to ever get past it - real instruction-
    prompt wording alone did not reliably stop this. Once a conversation
    has already had one contradicts_prior, a second is treated as
    "sufficient" instead of asked again - the candidate has restated
    their answer, and it's scoring's job to judge whether it was the
    right call, not this gate's job to keep re-litigating it forever."""
    return sum(
        1 for turn in (conversation_so_far or [])
        if _CONTRADICTS_PRIOR_MARKER in (turn.get("response_message") or "")
    )


def value_traces_to_candidate(value: str | None, selected_design: list[dict], conversation_so_far: list[dict]) -> bool:
    """Whether `value` (an LLM-claimed "prior_value" for contradicts_prior)
    genuinely traces back to something the CANDIDATE said - their own
    design's fields/refinements, or an earlier message they sent - as
    opposed to only appearing in the provided environment code. Verified
    live that the LLM classifier sometimes cites a value baked into the
    environment's own configuration (e.g. a base_url constant) as
    something "the candidate said earlier", which they never did -
    round2_automation_clarify uses this to deterministically refuse to treat
    that as a real contradiction, the same defense-in-depth pattern
    contains_forbidden_vocab already uses for a different failure mode."""
    needle = _normalize_value(value)
    if not needle:
        return False
    haystacks = []
    for row in selected_design or []:
        for v in row.values():
            if isinstance(v, str):
                haystacks.append(v)
            elif isinstance(v, list):
                haystacks.extend(x for x in v if isinstance(x, str))
    for turn in conversation_so_far or []:
        haystacks.append(turn.get("candidate_prompt") or "")
    return any(needle in _normalize_value(h) for h in haystacks)


def build_clarify_response(
    status: str, question: str | None = None,
    prior_value: str | None = None, current_value: str | None = None,
    fallback: str | None = None,
) -> dict:
    """The deterministic decision rule: turns the LLM's bounded
    classification (see schemas.Round2AutomationClarifyLLMResponse) into what
    the candidate actually sees. The LLM classifies; this function - not
    raw LLM say-so - decides the response, same split as
    round3_construct_engine.decide() deciding clarify/proceed from the
    LLM's own per-category classification."""
    if status == "sufficient":
        return {
            "response_kind": "explain",
            "response_message": "That reads as complete enough to encode - go ahead and ask me to do it.",
        }

    if status == "contradicts_prior":
        # A fixed template, not an LLM-drafted question: quoting the
        # candidate's own two prior statements back to them is
        # reflection, not new AI-authored guidance, so there's nothing
        # here for contains_forbidden_vocab to usefully guard - and a
        # template removes any risk of the LLM's own phrasing leaking
        # the category while resolving this.
        return {
            "response_kind": "clarify",
            "response_message": (
                f"You said \"{prior_value}\" earlier and \"{current_value}\" now for the same thing - "
                "which one should the automation use?"
            ),
        }

    # status == "insufficient"
    if contains_forbidden_vocab(question):
        question = fallback or FALLBACK_QUESTION
    return {"response_kind": "clarify", "response_message": question}


# ---- Clarification loop breaker -------------------------------------------
# Transcript review (Sep 2026) found candidates stuck for up to 16
# consecutive clarifying questions - 9 of them word-for-word identical,
# the fixed FALLBACK_QUESTION among them - including after "this is enough,
# give the code" and "ignore that, use only X". contradicts_prior_count
# already breaks the contradiction loop; nothing broke the "insufficient"
# one. These rules end any clarification streak, whichever branch produced
# it. Ending it means proceeding to generation, which still encodes ONLY
# the candidate's own design and messages (round2_automation_turn.txt) - it
# never fills gaps for them - and scoring judges whether what they gave
# was enough. Asking more never helps a candidate who has said they're done.

MAX_CONSECUTIVE_CLARIFIES = 3


def clarify_streak(conversation_so_far: list[dict]) -> int:
    """How many of the most recent turns in a row were clarifying questions."""
    return clarify_loop.clarify_streak(conversation_so_far)


def should_stop_clarifying(conversation_so_far: list[dict], candidate_prompt: str, next_question: str | None) -> bool:
    """True when asking `next_question` would continue a loop rather than
    help: it repeats a question already asked in this conversation, the
    candidate has said they're done after being asked at least once, or
    MAX_CONSECUTIVE_CLARIFIES questions in a row have already gone
    unresolved. Shared with the R3 assistant - see clarify_loop."""
    return clarify_loop.should_stop_clarifying(
        conversation_so_far, candidate_prompt, next_question, max_streak=MAX_CONSECUTIVE_CLARIFIES,
    )


# ---- The candidate's own design is the specification (Sep 2026) ----------
# Transcripts showed candidates asked "what should prove it worked?" again
# and again while their own Round 1 design already said it ("redirected to
# /dashboard and the header shows 'Welcome, Jordan'"), and a fixed fallback
# question sent word for word whatever they typed. Now:
#   - a design with its steps, test data and expected result filled in
#     needs no questions at all - generation encodes it (no AI call here);
#   - at most ONE clarifying question per test case; after that, proceed;
#   - any question names what is actually missing from the design instead
#     of one generic sentence.
# Scoring still judges whether what the candidate specified was enough.
_DESIGN_QUESTIONS = (
    ("steps", "Which steps should the test perform, in order?"),
    ("test_data", "Which test data should the test use?"),
    ("expected_result", "What result should the test check to prove it passed?"),
)


def missing_design_fields(selected_design: list[dict]) -> list[str]:
    """Design fields (steps, test data, expected result) left empty or near-empty."""
    row = (selected_design or [{}])[0] or {}
    return [field for field, _ in _DESIGN_QUESTIONS if len(str(row.get(field) or "").split()) < 2]


def design_is_complete(selected_design: list[dict]) -> bool:
    return not missing_design_fields(selected_design)


def question_for_design(selected_design: list[dict]) -> str:
    """The question for the first missing design field - the generic one only when nothing is missing."""
    missing = missing_design_fields(selected_design)
    return next((q for field, q in _DESIGN_QUESTIONS if field in missing), FALLBACK_QUESTION)


def already_asked(conversation_so_far: list[dict]) -> bool:
    """Whether a clarifying question has been asked for this test case already."""
    return any((t or {}).get("response_kind") == "clarify" for t in conversation_so_far or [])
