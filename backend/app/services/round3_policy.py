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

from . import assistant_operations

REFUSAL_MESSAGE = (
    "I can't do that for you - tell me the specific, narrow thing you want help with, "
    "and I'll do exactly that."
)

# Same wording as round3_coding_turn.txt's refuse (c), so a deterministic
# refusal and a model refusal read identically to the candidate.
WHOLE_TASK_REFUSAL_MESSAGE = "I can't write this for you - tell me what you want built, and I'll write exactly that."
# Used instead when the refusal above was the assistant's previous message - a refusal never
# repeats word for word, and it always says how to go on (production, 2026-10-07).
WHOLE_TASK_REFUSAL_AGAIN = ("I still can't write the whole solution - give me your own steps (what the code should do, "
                            "in order) and I'll write those, or type the code yourself with Edit code.")

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
    # "Use my nested-loop approach and write the code" - the candidate names
    # the approach as theirs, with its technique. Only in that possessive
    # form: "write the code using a nested loop" names a technique word, not
    # an approach, and stays a whole-task request.
    r"|\bmy\s+(own\s+)?nested[- ]loops?\s+(approach|method|solution|logic|plan|idea)\b"
)

# "Write the code for my approach" - refers to an approach instead of
# naming it, so the text alone can't show one exists. Allowed only once the
# candidate has already directed something earlier in the conversation
# (see is_continuation_of_whole_task) - never with "complete/full/whole",
# and never on an opening message.
_STATED_APPROACH_REF_RE = re.compile(
    r"\b(code|program)\s+(for|of|using|from|following|with)\s+my\s+(own\s+)?([\w-]+\s+){0,2}?"
    r"(approach|method|plan|idea|logic|algorithm|steps|instructions)\b"
    r"|\bfollow(s|ing)?\s+my\s+(own\s+)?(steps|approach|plan|instructions|algorithm|logic)\b"
)
_WHOLE_WORDS_RE = re.compile(r"\b(complete|full|entire|whole|working)\b")

# AI-owned whatever else the message says (Batch 1a, 2026-10-07): asking for a complete/final
# solution, or for "this task/question/problem" to be done. Checked BEFORE anything that can let
# a message through, so no technique word ("split by comma", "for loop", "x = 1") unlocks them.
_AI_OWNED_RE = re.compile(
    r"\b(complete|full|entire|whole|final|working)\s+(\w+\s+){0,2}?(program|solution|code|answer|implementation)\b"
    r"|\bfinal\s+answer\b"
    r"|\b(do|solve|code|answer|complete|attempt|tackle|finish)\s+(this|the|that)\s+(task|question|problem|exercise|assignment|challenge)\b"
    r"|\b(program|code|solution)\s+for\s+(this|that|it)\s*($|[.,;!?]|(task|question|problem|exercise|assignment|challenge)\b)"
    r"|\b(program|code|solution)\s+for\s+the\s+(task|question|problem|exercise|assignment|challenge)\b"
)

# "write the code to remove duplicates from nums" - one operation of the candidate's own,
# not the task. Only "code to <transforming verb>": "write the code using a nested loop" names
# a technique, not an operation, and stays a whole-task request.
_ONE_OPERATION_RE = re.compile(
    r"\b(write|give|create|generate|provide)\b[^.?!]{0,20}?\bcode\s+to\s+"
    r"(remove|drop|delete|eliminate|filter|dedup\w*|keep|pick|take|extract|retain|collect|store|sort|order|arrange|"
    r"reverse|split|parse|convert|count|compare|track|swap|append|loop|iterate|go\s+through|traverse)\b"
)

_TASK_WORD_RE = re.compile(r"[a-z]+")
_GENERIC_WORDS = set("""a an the and or of to in on for from with by is are be it its this that these those i you me my we our
your please can could would will should just now then also so if else write code program solution script implement create
build give make generate provide want need help task question problem following steps step using use do does done
work out find figure compute calculate determine get number numbers value values integer integers element elements item items
there isn aren one any none
""".split())
# The last two lines (2026-10-08, real-AI gate case 7): goal verbs, generic data nouns and filler - task-independent.
# Counted as content they diluted a restated goal under the 60% share ("Work out the second largest distinct number
# and print it" scored 4/7), so a goal-only request reached the model, which asked for steps instead of refusing.
# Content words shared with the scenario's own description, compared on their first five letters.
_PARAPHRASE_MIN_WORDS = 4
_PARAPHRASE_SHARE = 0.6
_COPIED_RUN = 8  # this many consecutive words of the scenario description = the task pasted in


def _stems(text: str) -> list[str]:
    return [w[:5] for w in _TASK_WORD_RE.findall((text or "").lower()) if len(w) > 2 and w not in _GENERIC_WORDS]


def _copied_span(text: str, scenario_description: str) -> tuple[bool, str]:
    """(whether the message pastes part of the task, the message with every pasted word removed)."""
    words = _TASK_WORD_RE.findall((text or "").lower())
    task = " " + " ".join(_TASK_WORD_RE.findall((scenario_description or "").lower())) + " "
    pasted = set()
    for i in range(len(words) - _COPIED_RUN + 1):
        if " " + " ".join(words[i:i + _COPIED_RUN]) + " " in task:
            pasted.update(range(i, i + _COPIED_RUN))
    return bool(pasted), " ".join(w for i, w in enumerate(words) if i not in pasted)


# "if fewer than two values, print -1" - the candidate stating one rule of their own, with its value.
_OWN_RULE_RE = re.compile(r"\bif\b[^.;\n]{0,80}\b(print|return|output|show)\s+-?\d+\b")


def _paraphrases_task(text: str, scenario_description: str) -> bool:
    """The goal restated (no operation of the candidate's own): most of what the message says
    comes straight from the scenario's description."""
    stems = _stems(text)
    if len(stems) < _PARAPHRASE_MIN_WORDS:
        return False
    task = set(_stems(scenario_description))
    return sum(1 for s in stems if s in task) / len(stems) >= _PARAPHRASE_SHARE


def is_whole_task_request(text: str, scenario_description: str | None = None) -> bool:
    """Whether the message hands the task to the assistant (AI-owned) rather than stating what to
    build (candidate-owned). AI-owned signals are checked first and can't be unlocked by anything
    else in the message. With the scenario's description, a pasted task or a restated goal with no
    operation of the candidate's own is AI-owned too - but never a message that states an approach
    of their own (two or more operations), however much it overlaps the task's wording."""
    lowered = (text or "").lower()
    if _FINISH_IT_RE.search(lowered) or _AI_OWNED_RE.search(lowered):
        return True
    owns_approach = assistant_operations.is_candidate_approach(lowered)
    if scenario_description:
        pasted, own_words = _copied_span(lowered, scenario_description)
        if pasted:
            # Only what they wrote themselves can show an approach - the task's own wording can't.
            owns_approach = assistant_operations.is_candidate_approach(own_words)
            if not owns_approach:
                return True
        elif not owns_approach and not _OWN_RULE_RE.search(lowered) and _paraphrases_task(lowered, scenario_description):
            return True
    if not _WHOLE_TASK_RE.search(lowered):
        return False
    if owns_approach:
        return False  # "write the program: 1. pick the unique elements 2. sort descending 3. print [1]"
    if _ONE_OPERATION_RE.search(lowered):
        return False  # "write the code to remove duplicates from nums" - restating the goal was caught above
    return not _CONCRETE_STEP_RE.search(lowered)


# Output target != implementation method (Batch 1c, 2026-10-08). Naming WHICH value to produce - a selection by
# rank ("the largest", "the second smallest", "the maximum") - says nothing about HOW to get it. Real-AI gate 14:
# "Remove the duplicates, then print the second largest distinct value" got three home-made algorithms (sort and
# index, remove-the-max-then-max) from the model, each with an invented -1 fallback. When the candidate names
# such a target and nothing they have said gives a way to get it (sorting, a loop, a comparison, max()/min(),
# an index), the method is theirs to give: a fixed question, before any AI call. A stated way to get it - even a
# wrong one ("sort descending and print the first element") - is their approach and goes to the model as before.
MISSING_METHOD_MESSAGE = ("Which steps should the code use to get the value you want printed? "
                          "Tell me how you want it worked out, and I'll write exactly that.")
_RANKED_TARGET_RE = re.compile(
    r"\b(?:(?:second|third|fourth|fifth|next|\d+(?:st|nd|rd|th))[\s-]+)?"
    r"(?:largest|smallest|biggest|highest|lowest|greatest|maximum|minimum|max|min)\b(?!\s*\()", re.I)
_METHOD_FAMILIES = {"sorting", "a loop", "comparing/tracking", "indexing"}
_METHOD_CALL_RE = re.compile(r"\b(?:max|min|sorted|sort|nlargest|nsmallest)\s*\(", re.I)
# a comparison they state is a method ("if greater than the current highest, replace it")
_COMPARISON_RE = re.compile(r"\b(?:greater|less|bigger|smaller|larger|higher|lower)\s+than\b|[<>]=?", re.I)
# the ranked value is a TARGET only in a step that outputs it or sets out to obtain it
_CLAUSE_RE = re.compile(r"[,;:.]|\bthen\b|\band\b", re.I)
_TARGET_STEP_RE = re.compile(r"\b(?:find|get|work\s+out|determine|compute|calculate|identify|pick|select|choose)\b", re.I)


def names_target_without_method(conversation_so_far: list[dict], candidate_prompt: str) -> bool:
    """The message names a ranked value to produce (in a step that outputs or obtains it), and no candidate
    message in this conversation (this one included) states any way of getting it."""
    clauses = [c for c in _CLAUSE_RE.split(candidate_prompt or "") if c.strip()]
    if not any(_RANKED_TARGET_RE.search(c) and (assistant_operations.OUTPUT.search(c) or _TARGET_STEP_RE.search(c))
               for c in clauses):
        return False
    said = [t.get("candidate_prompt") or "" for t in conversation_so_far or [] if t.get("response_kind") != "refuse"]
    for text in [*said, candidate_prompt or ""]:
        if (assistant_operations.families(text) & _METHOD_FAMILIES or _METHOD_CALL_RE.search(text)
                or _COMPARISON_RE.search(text)):
            return False
    return True


def is_continuation_of_whole_task(conversation_so_far: list[dict], candidate_prompt: str,
                                  scenario_description: str | None = None) -> bool:
    """Closes a two-step bypass found in transcript review: a whole-task
    request ("write a program to check odd or even and print it") gets a
    clarifying question, the candidate answers in one word ("cli"), and
    the combined instruction was then treated as a normal code_edit - the
    full solution, from what was really still a solve-it-for-me request.
    If the new message answers a clarifying question (or a chain of them)
    that was about a whole-task request, the answer is refused the same
    way the original request would have been - unless the answer itself
    states an approach of the candidate's own, which no question can make
    AI-owned."""
    if is_whole_task_request(candidate_prompt, scenario_description):
        return not _implements_stated_approach(conversation_so_far, candidate_prompt)
    for turn in reversed(conversation_so_far or []):
        if turn.get("response_kind") != "clarify":
            return False
        if is_whole_task_request(turn.get("candidate_prompt"), scenario_description):
            return not assistant_operations.is_candidate_approach(candidate_prompt)
    return False


def _implements_stated_approach(conversation_so_far: list[dict], candidate_prompt: str) -> bool:
    """"Write the code for my approach", after the candidate has already
    directed at least one step the assistant didn't refuse - the approach
    it points to is theirs. The model's own (c)/"Not (c)" rules still judge
    whether an approach was really stated; this only stops the pattern above
    refusing it before the model sees it."""
    lowered = (candidate_prompt or "").lower()
    if not _STATED_APPROACH_REF_RE.search(lowered) or _WHOLE_WORDS_RE.search(lowered) or _FINISH_IT_RE.search(lowered):
        return False
    return any(
        t.get("response_kind") != "refuse" and not is_whole_task_request(t.get("candidate_prompt"))
        for t in conversation_so_far or []
    )


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
