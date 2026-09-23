"""
Round 3 - deterministic post-generation scope check. round3_policy.py
checks what the CANDIDATE asked before the model runs; this checks what
the MODEL wrote after it runs, before the candidate ever sees it.

Why it exists: round3_coding_turn.txt already tells the model to write
"EXACTLY and ONLY" what the instruction asks and never to invent test
data - and transcript review (Sep 2026) found it still, on short or
goal-level instructions, added unrequested input handling, type
conversion, loops, output, sentinel values and whole test-data sets
(e.g. "remove the duplicates" also rebuilt how input was collected;
"do that in place" produced the full algorithm). Prompt wording is not a
guarantee; a check on the output is.

How it works: every line the edit ADDS (lines merely moved or
re-indented don't count) is scanned for a small set of capabilities -
reading input, converting types, printing, looping, handling errors,
building collections, sorting, de-duplicating, fallback values, defining
functions, hard-coded data. A capability only counts as requested if the
candidate's instruction - the new message plus, when it answers a
clarifying question, the instruction that question was about - uses
words that plausibly ask for it. Anything added without such words is
reported, and llm_service.round3_coding_turn regenerates once with the
list, then falls back to asking the candidate.

Deliberately lenient toward the candidate: trigger words are broad and
cover common misspellings, so a legitimate instruction is rarely blocked
- the cost of a miss is the old behaviour, the cost of a false alarm is
one regeneration or one clarifying question. Language scaffolding every
program needs (Java's class/main wrapper, imports, braces) is ignored.
Replayed against every real Round 3 code edit in the local database
before shipping - see tests/test_round3_scope_guard.py for the cases.
"""
import re

# capability -> (pattern in an ADDED code line, pattern in the instruction that justifies it)
_CAPABILITIES = {
    "reading input": (
        r"\binput\s*\(|\bScanner\b|\.next(Int|Line|Double|Long)?\s*\(|hasNext\w*\s*\(|readline|readLine|sys\.stdin|BufferedReader|\bprompt\s*\(",
        r"input|inout|inupt|read|enter|type|user|stdin|scanner|\bget\b|ask|prompt|cli|command line|keyboard",
    ),
    "type conversion": (
        r"\bint\s*\(|\bfloat\s*\(|\bmap\s*\(\s*(int|float)\b|parseInt|parseDouble|Integer\.valueOf|Number\s*\(|parseFloat|\.isdigit\s*\(",
        r"\bint\b|integer|interger|number|numeric|digit|float|decimal|convert|cast|type|parse",
    ),
    "printing output": (
        r"\bprint\s*\(|System\.out\.|console\.log",
        r"print|output|display|show|echo|log|message|error|say|tell",
    ),
    "a loop": (
        r"^\s*(for|while)\b|\bfor\s*\(|\bwhile\s*\(|\.forEach\s*\(",
        r"loop|iterat|itern|each|every|all (the )?(items|values|numbers|elements)|repeat|again|\buni?ti?ll?\b|\btill\b|keep|go through|travers|one by one|while|\bfor\b|compare|ask|space|comma|separat|split",
    ),
    "error handling": (
        r"^\s*(try|except|catch|finally)\b|\bcatch\s*\(|\bexcept\b|\braise\b|\bthrow\b",
        r"error|exception|invalid|valid|handle|throw|raise|try|catch|reject|not (an? )?(int|integer|number)|only (int|integer|number)|wrong|accept",
    ),
    "a collection": (
        r"=\s*\[\s*\]|new (Array)?List\b|new ArrayList|new HashSet|new HashMap|\.append\s*\(|\.add\s*\(|\.push\s*\(|=\s*\{\s*\}",
        r"list|array|collection|store|save|keep|add|append|push|\bset\b|map|dict|variable",
    ),
    "sorting": (
        r"\bsort(ed)?\s*\(|\.sort\s*\(|Collections\.sort|Arrays\.sort|reverseOrder",
        r"sort|order|arrange|ascending|descending|small(est|er)? to|big(gest|ger)? to|large(st|r)? to|rank",
    ),
    "de-duplication": (
        r"\bset\s*\(|HashSet|TreeSet|new Set\s*\(|\.distinct\s*\(|dict\.fromkeys",
        r"duplic|duiplic|distinct|disctinct|unique|\bset\b|dedup|repeat",
    ),
    "a fallback value": (
        r"(?<![\w.\[])-1\b(?!\s*\])|MIN_VALUE|MAX_VALUE|float\s*\(\s*['\"]-?inf|Infinity",
        r"-1|minus one|none|null|no (second|value|answer|result)|not (found|exist)|if there (is|are) no|fewer|less than|empty|only one|\bmin|\bmax|infinity|sentinel|default",
    ),
    # Technique choices (Sep 2026 guardrail review): the candidate asked for
    # the goal ("remove the duplicates", "sort it", "get the numbers") and the
    # model picked HOW - a set, descending order, a separator. Those choices
    # are part of what's assessed, so each needs words that name it.
    "chose a set to remove duplicates": (
        r"\bset\s*\(|HashSet|TreeSet|new Set\s*\(|dict\.fromkeys|\.distinct\s*\(",
        r"\bset\b|hash ?set|tree ?set|fromkeys|\bdict|distinct\(",
    ),
    "chose descending order": (
        r"reverse\s*=\s*True|reverseOrder|Collections\.reverse|\.reverse\s*\(",
        r"descend|decreas|reverse|(large|big|high)(st|r|er|est)?\s+(to|first)",
    ),
    "chose how the input values are separated": (
        r"\.split\s*\(|hasNext\w*\s*\(|\.nextLine\s*\(|\buseDelimiter\b",
        r"split|space|comma|separat|delimit|whitespace|one line|same line|per line|each line|line by line|one (at a time|by one)|scanner|hasnext",
    ),
    "a new function": (
        r"^\s*def \w+\s*\(|^\s*(public|private|protected|static)\b[\w\s<>\[\],]*\s\w+\s*\([^)]*\)\s*\{?\s*$|^\s*function \w+\s*\(",
        r"function|method|def\b|helper|procedure|main|routine|wrap",
    ),
}

# Re-wording something the code already does (the same print moved into a
# new branch, an existing input line gaining a conversion that WAS asked
# for) isn't adding a new capability. Deliberately NOT extended to type
# conversion, loops, collections etc. - those are exactly what got silently
# expanded in the transcripts this guards against.
_ALREADY_PRESENT_OK = {
    "printing output", "reading input",
    # A technique already in the code was already accepted - reusing it isn't a new choice.
    "chose a set to remove duplicates", "chose descending order", "chose how the input values are separated",
}

# Rewriting a line re-adds whatever it already did: the model restyling
# "nums = list(map(int, input().split()))" is not new type conversion.
# For this capability only, a changed line is a rewrite when the program
# doesn't end up with MORE conversions and every converted value still goes
# where it went before - the same variable, or nowhere (a bare validity
# check). Converting in order to check, then starting to STORE the
# converted values, is still new (tests/test_assistant_scope_rules.py,
# remove-duplicates case).
_COUNTED_AGAINST_OLD = {"type conversion"}
_ASSIGN_TARGET_RE = re.compile(r"^\s*(?:[\w<>\[\]]+\s+)?([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*=(?!=)")
_STORE_TARGET_RE = re.compile(r"([A-Za-z_]\w*)\s*\.\s*(?:append|add|push|extend|insert)\s*\(")


def _conversion_targets(lines, code_re: str) -> set:
    """Where each line's converted value goes: the variable it's appended
    to or assigned to, or None for a bare conversion (a validity check)."""
    targets = set()
    for line in lines:
        if not re.search(code_re, line):
            continue
        stored = _STORE_TARGET_RE.search(line)
        assigned = _ASSIGN_TARGET_RE.match(line)
        targets.add(stored.group(1) if stored else re.sub(r"\s+", "", assigned.group(1)) if assigned else None)
    return targets


def _is_rewrite(code_re: str, old: str, new: str, added: list) -> bool:
    if len(re.findall(code_re, new or "", re.M)) > len(re.findall(code_re, old, re.M)):
        return False
    return _conversion_targets(added, code_re) <= _conversion_targets(old.splitlines(), code_re)

# Hard-coded data: three or more numeric literals on one added line (a test
# list, sample inputs) must each appear in the instruction.
_NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")

# Lines every program in that language needs regardless of the instruction.
_SCAFFOLD_RE = re.compile(
    r"^\s*(import |from \S+ import |package |public class Main\b|class Main\b|public static void main\s*\(|[{}()\[\];]*\s*$|#|//|/\*|\*|if __name__ ==)"
)

_FUNC_NAME_RE = re.compile(r"^\s*def (\w+)\s*\(|^\s*(?:public|private|protected|static)\b[\w\s<>\[\],]*\s(\w+)\s*\(|^\s*function (\w+)\s*\(", re.M)


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def _function_names(code: str) -> set[str]:
    return {n for groups in _FUNC_NAME_RE.findall(code or "") for n in groups if n}


def added_lines(current_code: str | None, new_code: str | None) -> list[str]:
    """Lines present in new_code that weren't anywhere in current_code -
    compared whitespace-insensitively, so moving or re-indenting an
    existing line isn't counted as adding it."""
    before = {_norm(l) for l in (current_code or "").splitlines()}
    out = []
    for line in (new_code or "").splitlines():
        n = _norm(line)
        if n and n not in before and not _SCAFFOLD_RE.match(line):
            out.append(line)
    return out


def instruction_text(conversation_so_far: list[dict], candidate_prompt: str) -> str:
    """The instruction this code edit is answering: the new message plus,
    when it's a reply to one or more clarifying questions, the candidate
    messages that led to those questions (see the prompt's "Continuation
    check" - a short answer completes an earlier instruction)."""
    parts = [candidate_prompt or ""]
    for turn in reversed(conversation_so_far or []):
        if turn.get("response_kind") != "clarify":
            break
        parts.append(turn.get("candidate_prompt") or "")
    return " ".join(parts).lower()


def unrequested_additions(language: str, current_code: str | None, new_code: str | None, instruction: str) -> list[str]:
    """Capabilities the edit adds that the instruction gives no sign of
    asking for, in a stable order. Empty list = the edit stays in scope."""
    lines = added_lines(current_code, new_code)
    if not lines:
        return []
    old = current_code or ""
    text = "\n".join(lines)
    instruction = (instruction or "").lower()
    found = []
    for name, (code_re, ask_re) in _CAPABILITIES.items():
        if not re.search(code_re, text, re.M) or re.search(ask_re, instruction):
            continue
        if name in _ALREADY_PRESENT_OK and re.search(code_re, old, re.M):
            continue
        if name in _COUNTED_AGAINST_OLD and _is_rewrite(code_re, old, new_code, lines):
            continue
        if name == "a new function" and _function_names(text) <= _function_names(old):
            continue  # a changed signature on a function that already exists
        found.append(name)
    stated_numbers = set(_NUMBER_RE.findall(instruction))
    for line in lines:
        literals = [n for n in _NUMBER_RE.findall(line) if n not in ("0", "1", "2", "-1")]
        if len(literals) >= 3 and any(n not in stated_numbers for n in literals):
            found.append("hard-coded data the candidate didn't give")
            break
    return found


def is_noop_edit(current_code: str | None, new_code: str | None) -> bool:
    """A code_edit that changes nothing (ignoring whitespace) - the reply
    must not claim a change was made."""
    squash = lambda c: re.sub(r"\s+", "", c or "")
    return squash(current_code) == squash(new_code)


# Sent to the model only, never shown to the candidate. The old note ended
# "...respond with clarify and ask for it", and live traces showed the retry
# dropping the candidate's whole approach for a vague question ("What
# information needs to be tracked?") when only an extra -1 had been flagged.
REGENERATION_NOTE = (
    "IMPORTANT - your previous attempt at this turn was rejected before the candidate saw it, "
    "because the code you added went beyond the candidate's instruction: it added {items}, "
    "which the instruction did not ask for. Write the edit again: "
    "(1) keep everything the candidate explicitly stated - their approach and every concept they "
    "named, such as what to track (the largest value, the second-largest value), going through "
    "the data once, a nested loop, or any tracking they specified - and implement it; "
    "(2) leave out ONLY the additions listed above; "
    "(3) add nothing else the candidate didn't state - no other logic, value, output or edge-case "
    "handling, and never a different algorithm or approach than theirs. "
    "Respond with \"clarify\" only if their approach genuinely cannot be written at all without one "
    "of the listed additions - then ask one plain question about that missing step, never about "
    "something they already told you and never about an edge case or error condition. "
    "Never mention this check, the rejected attempt or the names of the additions above in your "
    "response_message."
)

SCOPE_FALLBACK_MESSAGE = (
    "I need a more specific instruction for that - tell me exactly what this step should do, "
    "and I'll write only that."
)

NOOP_MESSAGE = "The code already does that - nothing needed to change."


# Reply-vs-code check. Transcript review found replies describing a sort
# the code doesn't do - "sorted descending so the largest is at index -1"
# (R3 submissions 33, 34, 38, 40, 43) and "second largest" for what was the
# second-smallest (R3-113). Narrow on purpose: only claims about order.
_DESC_CODE_RE = re.compile(r"reverse\s*=\s*True|reverseOrder|Collections\.reverse")
_SORT_CODE_RE = re.compile(r"\bsort(ed)?\s*\(|\.sort\s*\(|Collections\.sort|Arrays\.sort")
_CLAIMS_ASC_RE = re.compile(r"ascending|largest (number )?(is |will be |would be )?at (index )?-1|smallest (number )?(is |will be )?at (index )?0|smallest to (largest|biggest)")
_CLAIMS_DESC_RE = re.compile(r"descending|largest (number )?(is |will be )?at (index )?0|(largest|biggest) to smallest")


def misleading_order_claim(response_message: str | None, current_code: str | None, new_code: str | None) -> bool:
    """True when the reply claims a sort order (or "second largest") that
    the code it describes doesn't produce."""
    msg = (response_message or "").lower()
    added = "\n".join(added_lines(current_code, new_code))
    code = new_code or ""
    if _SORT_CODE_RE.search(added):
        descending = bool(_DESC_CODE_RE.search(added))
        if descending and _CLAIMS_ASC_RE.search(msg):
            return True
        if not descending and _CLAIMS_DESC_RE.search(msg) and not _CLAIMS_ASC_RE.search(msg):
            return True
    # "second largest" for index -2 of a list sorted descending
    return bool("second largest" in msg and "[-2]" in added and _DESC_CODE_RE.search(code))


# Claims a reply commonly makes about its own edit, and the code each claim
# needs to see among the ADDED lines. A claim with no matching added line
# describes a change that wasn't made (R3-187: "Converted the ArrayList to a
# Set" when nothing changed; R3-113: "second largest" for the second-smallest).
_ACTION_CLAIMS = (
    (re.compile(r"\bsort(ed|s|ing)?\b"), _SORT_CODE_RE),
    (re.compile(r"\b(converted|removed|removing) (\w+ )*(to a |into a |using a )?set\b|\bremoved (the )?duplicates\b"),
     re.compile(r"\bset\s*\(|HashSet|TreeSet|new Set\s*\(|dict\.fromkeys|\.distinct\s*\(|\.remove\s*\(|\.pop\s*\(|del\s")),
    (re.compile(r"\b(added|wrote|created) (a |an )?(\w+ )?loop\b"), re.compile(r"^\s*(for|while)\b|\bfor\s*\(|\bwhile\s*\(|\.forEach\s*\(", re.M)),
    (re.compile(r"\b(converted|converts|converting) (\w+ ){0,4}(to|into) (an? )?(int|integer|float|number)s?\b"),
     re.compile(r"\bint\s*\(|\bfloat\s*\(|\bmap\s*\(\s*(int|float)\b|parseInt|parseDouble|Integer\.valueOf|Number\s*\(|parseFloat|nextInt")),
    (re.compile(r"\b(printed|prints|printing|added (a |the )?print)\b"), re.compile(r"\bprint\s*\(|System\.out\.|console\.log")),
    (re.compile(r"\b(added|wrapped in) (a |an )?(try|except|catch|error handling|validation)\b"),
     re.compile(r"^\s*(try|except|catch)\b|\bcatch\s*\(|\bexcept\b|\braise\b|\bthrow\b", re.M)),
)


def misleading_claim(response_message: str | None, current_code: str | None, new_code: str | None) -> bool:
    """True when the reply describes a change the code doesn't contain -
    a wrong sort order (see misleading_order_claim), or a claimed action
    (sorted, converted to a set, added a loop, ...) with no added line
    doing it."""
    if misleading_order_claim(response_message, current_code, new_code):
        return True
    msg = (response_message or "").lower()
    added = "\n".join(added_lines(current_code, new_code))
    return any(claim.search(msg) and not code.search(added) for claim, code in _ACTION_CLAIMS)


def diff_summary(current_code: str | None, new_code: str | None) -> str:
    """A reply built from the actual change, used when the model's own
    description can't be trusted."""
    lines = [l.strip() for l in added_lines(current_code, new_code)]
    if not lines:
        return "Updated the code - review the changed lines."
    shown = "; ".join(lines[:3]) + ("; ..." if len(lines) > 3 else "")
    return f"Updated the code - added: {shown}"
