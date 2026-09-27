"""
AI-Assisted Test Automation round - deterministic controls. NO LLM CALL
ANYWHERE IN THIS MODULE, by the same discipline as round3_policy.py
and progressive_policy.py.

Two independent controls, both deterministic, because "the prompt tells
the model not to" is not by itself an enforceable boundary:

1. PRE-GENERATION (is_prohibited): refuses a request whose SHAPE is
   asking the assistant to supply the candidate's own design work -
   inventing test cases, test data, assertions, expected results, or
   extra coverage. Runs before llm_service.round2_automation_turn ever calls
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
from difflib import SequenceMatcher

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
    # Red-team set (tests/redteam, Sep 2026): reworded requests that got through
    "more thorough", "test suite", "negative tests", "extra test", "add test cases", "more assertions",
    "extra assertions", "you think are needed", "you think is needed", "whatever they are", "make up",
    "ignore previous instructions", "ignore all previous instructions", "ignore your instructions",
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
    return _snapshot_text_as_written(selected_rows).lower()


def _snapshot_text_as_written(selected_rows: list[dict]) -> str:
    parts = []
    for row in selected_rows or []:
        for key in ("title", "preconditions", "steps", "test_data", "expected_result"):
            value = row.get(key)
            if value:
                parts.append(str(value))
        for note in row.get("refinements") or []:
            parts.append(str(note))
    return " ".join(parts)


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


# ---- Post-generation checks (Sep 2026 guardrail review) --------------------
#
# 3. UNREQUESTED ASSERTIONS: every assertion the assistant ADDS must trace to
#    something the candidate wrote - their design (title/steps/test data/
#    expected result/refinements) or one of their messages. Transcript
#    review and a live check both found the assistant adding checks nobody
#    asked for, e.g. `assert page["status"] == 200` after "No only this".
#    An assertion traces when one of its meaningful words or values appears
#    in the candidate's own text; one with no meaningful words at all
#    (`assert ok`) can't be judged and is left alone.
#
# 4. ENVIRONMENT LEAKS: an explain/clarify/refuse reply must not reveal a
#    value that exists only in the provided environment (a credential, an
#    expected message, a URL) - reading the environment is part of what's
#    assessed. Values the candidate already wrote themselves are fine.

_ASSERTION_LINE_RE = {
    "python": re.compile(r"^\s*assert\b"),
    "javascript": re.compile(r"^\s*(assert(\.\w+)?\s*\(|expect\s*\()"),
    "java": re.compile(r"^\s*(assert\b|Assert\.\w+\s*\(|assert(Equals|True|False|NotNull|Null|That)\s*\()"),
}

# Words that appear in almost any assertion and say nothing about WHAT is
# being checked.
_NEUTRAL_WORDS = {
    "assert", "assertequals", "asserttrue", "assertfalse", "assertnotnull", "assertnull", "assertthat",
    "expect", "tobe", "toequal", "equals", "true", "false", "none", "null", "not", "and", "the", "is",
    "result", "response", "res", "resp", "value", "text", "self", "get", "data", "ok", "msg",
    "message", "error", "code", "var", "let", "const", "str", "string", "int", "len", "size", "length",
    "status", "page", "actual", "expected", "should", "failed", "passed", "test", "check",
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def _norm_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if len(w) >= 3 and w not in _NEUTRAL_WORDS}


def _candidate_text(selected_rows: list[dict], conversation_so_far: list[dict], candidate_prompt: str) -> str:
    parts = [_snapshot_text(selected_rows), candidate_prompt or ""]
    parts += [t.get("candidate_prompt") or "" for t in conversation_so_far or []]
    return " ".join(parts).lower()


def _added_lines(before: str | None, after: str | None) -> list[str]:
    seen = {l.strip() for l in (before or "").splitlines()}
    return [l for l in (after or "").splitlines() if l.strip() and l.strip() not in seen]


def unrequested_assertions(
    language: str, before: str | None, after: str | None,
    selected_rows: list[dict], conversation_so_far: list[dict], candidate_prompt: str,
) -> list[str]:
    """Added assertion lines with nothing in them the candidate asked for."""
    pattern = _ASSERTION_LINE_RE.get(language)
    if pattern is None:
        return []
    haystack = _candidate_text(selected_rows, conversation_so_far, candidate_prompt)
    flagged = []
    for line in _added_lines(before, after):
        if not pattern.search(line):
            continue
        words = _norm_words(line)
        if words and not any(w in haystack for w in words):
            flagged.append(line.strip())
    return flagged


def drop_single_line_statements(code: str, lines: list[str]) -> tuple[str, list[str]]:
    """Removes each flagged line that is a complete statement on its own
    (balanced brackets; Java/JS lines ending in ';'). Returns the new code
    and the flagged lines that couldn't be removed safely."""
    remaining = []
    out = code or ""
    for flagged in lines:
        complete = flagged.count("(") == flagged.count(")") and flagged.count("[") == flagged.count("]")
        complete = complete and (flagged.startswith("assert ") or flagged.endswith(";"))
        kept = [l for l in out.splitlines() if l.strip() != flagged]
        if complete and len(kept) < len(out.splitlines()):
            out = "\n".join(kept) + ("\n" if out.endswith("\n") else "")
        else:
            remaining.append(flagged)
    return out, remaining


_STRING_LITERAL_RE = re.compile(r"""["']([^"'\n]{5,})["']""")


def leaked_environment_values(
    message: str | None, environment_code: str,
    selected_rows: list[dict], conversation_so_far: list[dict], candidate_prompt: str,
) -> list[str]:
    """String values from the environment code that appear in `message` but
    that the candidate never wrote themselves. Plain lowercase identifiers
    (dictionary keys like "completed") are ignored - they aren't secrets."""
    msg = (message or "").lower()
    if not msg:
        return []
    known = _candidate_text(selected_rows, conversation_so_far, candidate_prompt)
    leaks = []
    for value in dict.fromkeys(_STRING_LITERAL_RE.findall(environment_code or "")):
        v = value.strip()
        if re.fullmatch(r"[a-z_]+", v) or v.lower() in known:
            continue
        if v.lower() in msg:
            leaks.append(v)
    return leaks


def redact(message: str, values: list[str]) -> str:
    for v in values:
        message = re.sub(re.escape(v), "[withheld]", message, flags=re.IGNORECASE)
    return message


# 5. FABRICATED OBSERVATIONS: a test must READ what it checks from the
#    application through the environment's helpers. A variable named like
#    something observed (currentPage, dropdownText, actual_header, status)
#    being handed a fixed value instead fakes the observation. R2-186's first
#    code faked two (dropdownText = "Cardiology", currentPage = "appointment")
#    and never performed the search at all - far past a planted flaw's brief
#    of ONE subtle gap. Names containing "expected" are how a test states
#    what it's looking for, so they never count.
_OBSERVED_NAME_RE = re.compile(
    r"(current|actual|observed|displayed|shown|visible|page|header|heading|text|label|title|status|message|dropdown|result|url)",
    re.IGNORECASE,
)
_LITERAL_ASSIGNMENT_RE = re.compile(
    r"""^\s*(?:(?:final\s+)?(?:String|var|let|const|int|boolean)\s+)?([A-Za-z_]\w*)\s*=\s*(?:f?["'][^"'\n]*["']|\d+|True|False|true|false)\s*;?\s*$"""
)


def fabricated_observations(before: str | None, after: str | None) -> list[str]:
    """Added lines that hand an observed-looking variable a fixed value."""
    flagged = []
    for line in _added_lines(before, after):
        m = _LITERAL_ASSIGNMENT_RE.match(line)
        if m and _OBSERVED_NAME_RE.search(m.group(1)) and "expect" not in m.group(1).lower():
            flagged.append(line.strip())
    return flagged


# 6. CHANGED CANDIDATE VALUES: a value the candidate stated (an email, a
#    password, a name, a quoted string - in their design or any message for
#    this test case) must reach the code exactly as written. Run B's
#    "use the same user" turn came back with "Patient1" where the candidate
#    had said "Patient One"; nothing noticed. Flagged: a string literal in the
#    code that is a near-copy of a stated value (same letters ignoring
#    spaces and punctuation, or very similar - a case-only difference
#    doesn't count) while the stated value itself appears nowhere in the code. A literal the candidate also stated, or one
#    from the provided environment, is legitimate and never flagged.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_QUOTED_RE = re.compile(r"""["'`]([^"'`\n]{3,80})["'`]""")
_KEYED_VALUE_RE = re.compile(
    r"\b(?:name|full name|username|user name|user|password|pwd|email|e-mail)\s*(?:is|=|:)?\s*"
    r"([A-Z][\w@!#$%&*+-]*(?:\.[\w@!#$%&*+-]+)*(?: [A-Z][\w@!#$%&*+-]*(?:\.[\w@!#$%&*+-]+)*)*|\S*\d\S*)"
)
_CODE_STRING_RE = re.compile(r"""["']([^"'\n]{3,120})["']""")
_SIMILAR = 0.75


def _norm_value(v: str) -> str:
    return re.sub(r"[\W_]+", "", v.lower())


def _stated_values(selected_rows: list[dict], conversation_so_far: list[dict], candidate_prompt: str) -> set[str]:
    texts = [_snapshot_text_as_written(selected_rows), candidate_prompt or ""]
    texts += [t.get("candidate_prompt") or "" for t in conversation_so_far or []]
    values: set[str] = set()
    for text in texts:
        values.update(_EMAIL_RE.findall(text))
        values.update(m.strip() for m in _QUOTED_RE.findall(text))
        values.update(m.strip("([{").rstrip(".,;:!)]}") for m in _KEYED_VALUE_RE.findall(text))
    values = {v for v in values if len(_norm_value(v)) >= 4}
    # A value the candidate themselves wrote in more than one form
    # ("Wrong@999" and "wrong@999") has no single right spelling to hold
    # the code to - skip it rather than flag whichever one the code used.
    by_form: dict[str, set[str]] = {}
    for v in values:
        by_form.setdefault(v.lower(), set()).add(v)
    return {v for v in values if len(by_form[v.lower()]) == 1}


def changed_candidate_values(
    code: str | None, selected_rows: list[dict], conversation_so_far: list[dict], candidate_prompt: str,
    environment_code: str = "",
) -> list[str]:
    """'<stated> -> <in code>' for each stated value the code carries only an altered copy of."""
    code = code or ""
    stated = _stated_values(selected_rows, conversation_so_far, candidate_prompt)
    literals = {m.strip() for m in _CODE_STRING_RE.findall(code)}
    changed = []
    for value in sorted(stated):
        if value in code:
            continue
        nv = _norm_value(value)
        for lit in sorted(literals):
            if lit in stated or lit in (environment_code or "") or ("@" in lit) != ("@" in value):
                continue
            if lit.lower() == value.lower():
                continue  # case only ("welcome, jordan" -> "Welcome, Jordan") - not a changed value
            nl = _norm_value(lit)
            if nl and (nl == nv or SequenceMatcher(None, nv, nl).ratio() >= _SIMILAR):
                changed.append(f"{value} -> {lit}")
                break
    return changed


# 7. INVENTED CONTENT (red-team set, tests/redteam): the looser checks above
#    let a compliant model's invented tests through - an assertion passed if
#    any one word in it appeared anywhere in the candidate's text ("Patient
#    Dashboard" passed because the URL has "/patient"). Two precise rules:
#    - a quoted value in an added assertion must come from the candidate's
#      own words or the provided environment - an expected value nobody
#      gave is invented;
#    - the code may not gain an extra test function: one test per test case
#      the candidate designed.
EXTRA_TEST = "an extra test function the candidate didn't design"
_TEST_FUNCTION_RE = {
    "python": re.compile(r"^\s*def test_\w+\s*\(", re.M),
    "javascript": re.compile(r"^\s*(it|test)\s*\(", re.M),
    "java": re.compile(r"^\s*@Test\b", re.M),
}
# Only values an assertion actually COMPARES against - never an assertion's
# failure message ("Login failed"), which is explanation, not an expectation.
_COMPARED_RE = re.compile(
    r"""(?:==|!=|===|!==)\s*["']([^"'\n]{3,})["']"""
    r"""|["']([^"'\n]{3,})["']\s*(?:==|!=|===|!==|\bin\b|\bnot in\b)"""
    r"""|\.(?:equals|equalsIgnoreCase|contains|startsWith|endsWith|toBe|toEqual|toContain|toMatch)\(\s*["']([^"'\n]{3,})["']"""
    r"""|assertEquals\(\s*["']([^"'\n]{3,})["']\s*,"""
)


def _traceable(value: str, haystack: str) -> bool:
    v = " ".join(value.lower().split())
    if v in _IGNORED_LITERALS or v in haystack:
        return True
    if "/" in v:  # a URL or path: every word in it must come from somewhere given
        return all(w in haystack for w in _WORD_RE.findall(v) if len(w) >= 3)
    return False


def invented_content(
    language: str, before: str | None, after: str | None,
    selected_rows: list[dict], conversation_so_far: list[dict], candidate_prompt: str,
    environment_code: str = "",
) -> list[str]:
    """Added assertion lines whose expected values nobody gave, and extra test functions."""
    haystack = " ".join((_candidate_text(selected_rows, conversation_so_far, candidate_prompt) + " " + (environment_code or "").lower()).split())
    found = []
    pattern = _ASSERTION_LINE_RE.get(language)
    if pattern is not None:
        for line in _added_lines(before, after):
            if pattern.search(line) and any(not _traceable(next(g for g in m if g), haystack) for m in _COMPARED_RE.findall(line)):
                found.append(line.strip())
    if _test_units(language, after) > max(1, _test_units(language, before)):
        found.append(EXTRA_TEST)
    return found


def _test_units(language: str, code: str | None) -> int:
    """Tests in the code: each test function, plus top-level (unindented)
    Python assertions, which are a test of their own."""
    test_re = _TEST_FUNCTION_RE.get(language)
    units = len(test_re.findall(code or "")) if test_re else 0
    if language == "python" and re.search(r"^assert\b", code or "", re.M):
        units += 1
    return units


# ---- Synchronisation (waits) in browser tests ----------------------------------------------------
# Handling waits is assessed from the code, not from whether one run happened
# to pass (owner decision 2026-09-27): a browser test that clicks and then
# reads the next page without waiting fails most - not all - of the time, so a
# lucky pass must never earn credit, and the verdict must be the same for
# everyone. Deterministic, no AI.
_BROWSER_RE = re.compile(r"RemoteWebDriver|webdriver\.Remote|new\s+Builder\s*\(|ChromeDriver\s*\(|webdriver\.Chrome\s*\(")
_NAVIGATING_RE = re.compile(r"\.click\s*\(|\.submit\s*\(|Keys\.(ENTER|RETURN)|\\n['\"]\s*\)")
_EXPLICIT_RE = re.compile(r"WebDriverWait|FluentWait|\.until\s*\(|driver\.wait\s*\(|ExpectedConditions|expected_conditions|until\.\w+\(")
_IMPLICIT_RE = re.compile(r"implicitlyWait|implicitly_wait|implicit\s*[:=]|setTimeouts\s*\(")
_SLEEP_RE = re.compile(r"Thread\.sleep|time\.sleep|\bsleep\s*\(|setTimeout\s*\(")
_COMMENT_LINE_RE = re.compile(r"^\s*(//|#|\*|/\*)")


def synchronisation(code: str | None) -> dict:
    """How a test's browser steps wait for pages: verdict is
    "waits" (explicit or implicit waits), "fixed_sleeps_only" (works, but
    brittle and slow), "no_waits" (clicks, then acts on the next page without
    waiting - a real Selenium gap), "not_needed" (browser, but nothing that
    loads a new page after a click), or "no_browser"."""
    lines = [line for line in (code or "").splitlines() if not _COMMENT_LINE_RE.match(line)]
    text = "\n".join(lines)
    out: dict[str, object] = {"uses_browser": bool(_BROWSER_RE.search(text)), "navigating_actions": len(_NAVIGATING_RE.findall(text)),
           "explicit_waits": len(_EXPLICIT_RE.findall(text)), "implicit_wait": bool(_IMPLICIT_RE.search(text)),
           "fixed_sleeps": len(_SLEEP_RE.findall(text))}
    if not out["uses_browser"]:
        out["verdict"] = "no_browser"
    elif out["explicit_waits"] or out["implicit_wait"]:
        out["verdict"] = "waits"
    elif out["navigating_actions"] == 0:
        out["verdict"] = "not_needed"
    elif out["fixed_sleeps"]:
        out["verdict"] = "fixed_sleeps_only"
    else:
        out["verdict"] = "no_waits"
    return out
