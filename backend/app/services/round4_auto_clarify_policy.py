"""
AI-Assisted Test Automation round - deterministic half of the
specification-sufficiency gate (see llm_service.round4_auto_clarify for
the LLM half). NO LLM CALL ANYWHERE IN THIS MODULE, same discipline as
round4_auto_policy.py. Three jobs: a pre-filter that short-circuits an
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
prompts/round4_auto_helpers_{python,java,javascript}.txt, all three of
which expose UI/API/Database as the actual class names candidates code
against) - language-INDEPENDENT, unlike R3's vocabulary: the risk here is
the ENGLISH clarifying question naming a layer, not code syntax varying
by language.
"""
import re

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
# all ("test it", "go", "do it now", "please automate this"). Deliberately
# tiny and conservative: this only exists to catch the degenerate case at
# zero LLM cost, never to judge a real instruction's completeness - a
# short but concrete instruction ("confirm login fails") has content
# words left after stripping this list and is correctly NOT caught here;
# only the LLM classification step is trusted to judge those.
_PLACEHOLDER_WORDS = {
    "test", "it", "this", "that", "please", "go", "do", "automate",
    "run", "check", "now", "the", "a", "an", "and", "to", "for", "my", "it's", "its",
}


def is_placeholder_instruction(text: str) -> bool:
    """True only when NO content word survives stripping generic filler -
    an instruction this empty cannot possibly be complete regardless of
    what it's about, so it's safe to short-circuit before ever calling
    the LLM (see llm_service.round4_auto_clarify)."""
    words = re.findall(r"[a-zA-Z']+", (text or "").lower())
    return all(w in _PLACEHOLDER_WORDS for w in words) if words else True


def build_clarify_response(
    status: str, question: str | None = None,
    prior_value: str | None = None, current_value: str | None = None,
) -> dict:
    """The deterministic decision rule: turns the LLM's bounded
    classification (see schemas.Round4AutoClarifyLLMResponse) into what
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
        question = FALLBACK_QUESTION
    return {"response_kind": "clarify", "response_message": question}
