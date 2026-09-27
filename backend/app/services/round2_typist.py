"""
The Round 2 typing assistant for the real practice environment (agreed with
the owner 2026-09-27): it writes down and codes exactly what the candidate
says, knows nothing about the application, and never supplies or hints at a
step, value or check. The rules are in prompts/round2_typist_turn.txt; the
ones that must never depend on the model's obedience are enforced here, in
plain code:

  - code only when the candidate asks for it (or says yes to "shall I generate?")
  - nothing specific the candidate never said - an address, a status code, an
    HTTP method, a value, a table or column, an email, a number - may appear
    in a reply or in the code: one redraft, then a neutral reply without it
  - no reply repeats an earlier one word for word
  - requests to decide / invent / add are refused before the model is called
  - a step they didn't say how to do is an incomplete("...") call, so a Run
    can never show a test with gaps as passed (see run_status)
"""
import random
import re

from . import llm_service, round2_automation_policy

# The candidate's file before anything is written - the assistant writes the
# whole program from what they say (no provided helpers any more).
STARTERS = {
    "java": "// Your automated test. Tell the assistant what it should do - it writes this file from your words.\n"
            "public class Main {\n    public static void main(String[] args) throws Exception {\n    }\n}\n",
    "python": "# Your automated test. Tell the assistant what it should do - it writes this file from your words.\n",
    "javascript": "// Your automated test. Tell the assistant what it should do - it writes this file from your words.\n",
}

INCOMPLETE_MARKER = "INCOMPLETE:"
INCOMPLETE_EXIT = 3

CONVENTIONS = {
    "java": ("One file, public class Main with a main method. Browser: org.openqa.selenium RemoteWebDriver with "
             "new URL(System.getenv(\"SELENIUM_GRID_URL\")) and ChromeOptions --headless=new. API: java.net.http.HttpClient; "
             "for JSON, org.json (JSONObject), Gson or Jackson are available - use the one the candidate names; assertions from "
             "JUnit 5 (org.junit.jupiter.api.Assertions) or TestNG (org.testng.Assert) may be used inside main. "
             "Database: java.sql.DriverManager.getConnection(\"jdbc:sqlite:\" + System.getenv(\"PRACTICE_DB\")). "
             "Define static void incomplete(String step) that prints \"INCOMPLETE: \" + step and calls System.exit(3)."),
    "python": ("One script. Browser: selenium webdriver.Remote(os.environ[\"SELENIUM_GRID_URL\"], options=ChromeOptions with "
               "--headless=new). API: urllib.request, or requests if the candidate names it. Database: sqlite3.connect(os.environ[\"PRACTICE_DB\"]). Define "
               "incomplete(step) that prints \"INCOMPLETE: \" + step and exits with sys.exit(3)."),
    "javascript": ("One Node.js script. Browser: selenium-webdriver new Builder().usingServer(process.env.SELENIUM_GRID_URL)"
                   ".forBrowser(\"chrome\") with chrome Options --headless=new. API: the built-in fetch, or node-fetch / axios if the "
                   "candidate names one. Database: require(\"node:sqlite\")"
                   ".DatabaseSync(process.env.PRACTICE_DB). Define incomplete(step) that prints \"INCOMPLETE: \" + step and "
                   "calls process.exit(3)."),
}

_GENERATE_RE = re.compile(
    r"\b(generate|write (the |my |this )?(code|test|script|program)|create (the )?(code|test)|code (it|this|that)|"
    r"build (it|the test)|produce (the )?code|give me the code|let'?s (see|have) the code|go ahead)\b", re.I)
_YES_RE = re.compile(r"^\s*(yes|yeah|yep|ok(ay)?|sure|please do|do it|go on|that'?s (all|it|everything))\b", re.I)
_OFFERED_RE = re.compile(r"\b(generate|write|create) (the |your |this )?(code|test)\b", re.I)
_OWN_DESIGN_RE = re.compile(r"\b(round ?1|my (own )?(test case|steps|design|test data)|as (i )?(designed|wrote)|tc[- ]?\d+)\b", re.I)

# Specific things a reply or the code could name.
# A string literal in code, honouring escapes: Java and JavaScript JSON bodies are written
# "{\"email\": ...}", and reading \" as the end of the text made the code between two
# literals look like quoted application words (found by replaying the real replies in Java).
_CODE_STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.){0,400})"|\'((?:[^\'\\\n]|\\.){0,400})\'|`((?:[^`\\]|\\.){0,400})`')
# Library names a program imports are plumbing, not the application: require("selenium-webdriver").
_MODULE_RE = re.compile(r"""\brequire\s*\(\s*["'][^"'\n]+["']\s*\)|\b(?:from|import)\s+["'][^"'\n]+["']""")
# In prose, an apostrophe inside a word (you're, Priya's) is never a quotation mark.
_PROSE_QUOTED_RE = re.compile(r"""(?<!\w)["'`“‘]([^"'`“”‘’\n]{1,200})["'`”’](?!\w)""")
# A wait's time limit is plumbing, not knowledge of the application (measured: "10" in
# WebDriverWait(driver, 10) withheld a strong candidate's code).
_WAIT_LIMIT_RE = re.compile(r"WebDriverWait\s*\([^)]*\)|Duration\.of\w+\(\s*\d+\s*\)|implicitly_?[wW]ait\s*\(\s*[\d.]+|"
                            r"implicit\s*:\s*\d+|\.wait\s*\([^;]*?,\s*\d+\s*\)|(?:time\.)?sleep\s*\(\s*[\d.]+\s*\)|timeout\s*=\s*[\d.]+")
# A path has a name after the slash: "//" (a Java/JavaScript comment) is not one - read as a path,
# it withheld every program with a comment (persona browser test, 2026-09-27).
_PATH_RE = re.compile(r"(?<![\w.:/])/[A-Za-z0-9_\-.{}][A-Za-z0-9_\-./{}]*")
_STATUS_RE = re.compile(r"\b[1-5]\d\d\b")
_METHOD_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")
# What a program prints or logs is the program reporting what happened, not knowledge of the
# application (measured: print(f"FAIL: got {actual_name}") withheld a strong candidate's code).
_REPORTING_RE = re.compile(r"(?:\bprint|console\.(?:log|error)|System\.(?:out|err)\.print(?:ln|f)?|\bincomplete)\s*\((?:[^()\n]|\([^()\n]*\))*\)")
# Things of the application a reply must never bring up unless the candidate did (measured: asked
# to generate with details missing, the model replied "I need to know: what's the URL or page...").
_STRUCTURE_RE = re.compile(r"\b(urls?|web ?address(?:es)?|endpoints?|paths?|routes?|pages?|screens?|fields?|buttons?|links?|locators?|"
                           r"selectors?|xpaths?|css|ids?|methods?|status(?: codes?)?|headers?|tables?|columns?|rows?|queries|query|"
                           r"credentials?|usernames?|passwords?|tokens?|elements?|"
                           # screen parts an assistant guessing at the app reaches for (found by writing leak variants)
                           r"spinners?|loaders?|modals?|pop-?ups?|dialogs?|toasts?|banners?|dropdowns?|drop-downs?|checkbox(?:es)?|"
                           r"radio(?: buttons?)?|tabs?|menus?|forms?|icons?|iframes?|captchas?)\b", re.I)
# A name joined to one of those ("the Dashboard page", "the members table"): the candidate
# having said "page" doesn't make every page name theirs.
_NAMED_STRUCTURE_RE = re.compile(r"\b([A-Za-z][\w-]*)\s+(pages?|screens?|buttons?|fields?|tabs?|tables?|links?|forms?|menus?|"
                                 r"sections?|columns?|endpoints?|dialogs?|modals?|pop-?ups?)\b", re.I)
_NAME_NOT_SPECIFIC = {w for w in """the a an this that these those same next previous first last your my their its our which what
another other correct new each every one any some no main given right wrong login log sign in on of to for from with
different current whole entire following specific particular separate final second third earlier later same"""
                     .split()}
# A value written without quotes: letters with a digit or a symbol ("Pass@1234", "BK-009").
_VALUE_TOKEN_RE = re.compile(r"(?<![\w@#$%&*!-])(?=[\w@#$%&*!.-]*[A-Za-z])(?=[\w@#$%&*!.-]*[\d@#$%&*!])[A-Za-z0-9][\w@#$%&*!.-]*[\w@#$%&*!]")
_ORDINAL_RE = re.compile(r"^\d+(st|nd|rd|th)$", re.I)
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*|\d+(?:\.\d+)?")

# Never specific: the project conventions and the plumbing every test uses.
_GENERIC = {w.lower() for w in """
PRACTICE_APP_URL PRACTICE_API_URL PRACTICE_DB SELENIUM_GRID_URL INCOMPLETE PASS PASSED FAIL FAILED OK
Content-Type application json application/json Authorization Bearer token utf-8 UTF-8 jdbc sqlite jdbc:sqlite: chrome
headless new --headless=new select from where and or not null is count set update insert into values delete order by asc desc
limit as on join like in true false none api page ui step check result expected actual got value values the a an to of
normalize-space following following-sibling preceding preceding-sibling ancestor descendant parent self text contains
starts-with input button label link span div
""".split()}
_GENERIC_NUMBERS = {"0", "1", "2", "3", "-1", "100"}  # indexes, exit codes, percentages - never application values

_REFUSE = [
    "That's your call to make - tell me what you want and I'll write it down.",
    "I can't decide that for you. What would you like the test to do?",
    "That part is up to you. Let me know what you want, in your own words.",
]
_ASK = [
    "What would you like the test to do next?",
    "Tell me the next thing the test should do.",
    "What's next for this test?",
]
_BLOCKED = [
    "I can only write what you've told me, and some of it I'd have to guess. Tell me how each step should be done and I'll write it.",
    "Part of this I'd have to make up, and I won't do that. Tell me exactly how each step should be done, then ask me again.",
    "I need more from you before I can write it without guessing - how should each step be done?",
]
_NOTE = ("\n\nYour previous draft named things the candidate never said: {terms}. Rewrite it without them - "
         "if you need one of them, ask the candidate for it instead, without naming it or offering examples.")
_WRITE_NOTE = ("\n\nThey asked for the code: write the complete program now. For every step they didn't say how to do, put "
               "incomplete(\"<their own words for that step>\") where it belongs. Don't list or describe what is missing.")
_REPEAT_NOTE = "\n\nYour previous draft repeated a sentence you already wrote in this conversation. Say it differently."
# The code named things the candidate never said (measured, Java check 2026-09-27: asked "Generate it" with no
# element ids given, the model guessed locators both times): the step goes in as incomplete() instead.
_CODE_NOTE = ("\n\nYour previous code used things the candidate never said: {terms}. Write the program again without "
              "them: every step whose element, address, value or check they didn't give becomes incomplete(\"<their own "
              "words for that step>\"). Don't mention what is missing in your reply.")


def candidate_text(design: dict | None, conversation: list[dict], prompt: str) -> str:
    """Everything the candidate has said - their Round 1 test case too, once they've told the assistant to use it."""
    said = [t.get("candidate_prompt") or "" for t in conversation] + [prompt or ""]
    text = " ".join(said)
    if design and _OWN_DESIGN_RE.search(text):
        text += " " + " ".join(str(design.get(k) or "") for k in ("title", "preconditions", "steps", "test_data", "expected_result"))
    return text


def wants_code(prompt: str, conversation: list[dict]) -> bool:
    if _GENERATE_RE.search(prompt or ""):
        return True
    last = conversation[-1]["response_message"] if conversation else ""
    return bool(_YES_RE.search(prompt or "") and _OFFERED_RE.search(last or ""))


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


def unsaid(text: str | None, said: str, *, code: bool) -> list[str]:
    """Specific things in `text` (a reply, or code) the candidate never said."""
    if not text:
        return []
    haystack = _norm(said)
    found = []

    def check(term: str) -> None:
        t = term.strip().strip(".,;:!?")
        if not t or t.lower() in _GENERIC or t in _GENERIC_NUMBERS or _norm(t) in haystack:
            return
        found.append(t)

    for m in _EMAIL_RE.findall(text):
        check(m)
    # An XPath in code ("//label[normalize-space()='Password']/following::input") is a locator, not an
    # address: its "/following" isn't a path. Its words are still checked with the other literals below.
    for m in _PATH_RE.findall(_XPATH_LITERAL_RE.sub('""', text) if code else text):
        check(m)
    for m in _METHOD_RE.findall(text):
        check(m)
    for m in _STATUS_RE.findall(text):
        check(m)
    scanned = _REPORTING_RE.sub(" ", _MODULE_RE.sub(" ", text)) if code else text
    literals = ([re.sub(r"\\(.)", r"\1", "".join(g)) for g in _CODE_STRING_RE.findall(scanned)] if code
                else _PROSE_QUOTED_RE.findall(scanned))
    for literal in literals:
        if literal.lower() in _GENERIC or literal.startswith("INCOMPLETE"):
            continue
        # a whole literal the candidate said is fine; otherwise every word in it must be theirs
        if _norm(literal) in haystack:
            continue
        for word in _WORD_RE.findall(literal):
            if len(word) > 1 or word.isdigit():
                check(word)
    if not code:
        said_words = {w.lower().rstrip("s") for w in re.findall(r"[A-Za-z]+", said or "")}
        for m in _STRUCTURE_RE.findall(text):
            if m.lower().split()[0].rstrip("s") not in said_words:
                found.append(m)
        for name, part in _NAMED_STRUCTURE_RE.findall(text):
            if name.lower() not in _NAME_NOT_SPECIFIC and name.lower().rstrip("s") not in said_words:
                found.append(f"{name} {part}")
        for token in _VALUE_TOKEN_RE.findall(text):
            if not _ORDINAL_RE.match(token):
                check(token)
    if code:
        stripped = _CODE_STRING_RE.sub(" ", _WAIT_LIMIT_RE.sub(" ", scanned))
        for n in _NUMBER_RE.findall(stripped):
            check(n)
    seen, out = set(), []
    for t in found:
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def _pick(pool: list[str], used: set[str]) -> str:
    fresh = [p for p in pool if _norm(p) not in used]
    return random.choice(fresh or pool)


def _repeats(reply: str, earlier: list[str]) -> bool:
    sentences = {_norm(s) for e in earlier for s in re.split(r"(?<=[.?!])\s+", e or "") if len(s.split()) >= 4}
    return any(_norm(s) in sentences for s in re.split(r"(?<=[.?!])\s+", reply or "") if len(s.split()) >= 4)


@llm_service.candidate_turn
def turn(language: str, design: dict, conversation: list[dict], current_code: str, candidate_prompt: str) -> dict:
    """One reply: {"response_kind": "clarify"|"refuse"|"code_edit", "response_message", "code_after"}."""
    earlier = [t.get("response_message") or "" for t in conversation]
    used = {_norm(e) for e in earlier}
    if round2_automation_policy.is_prohibited(candidate_prompt):
        return {"response_kind": "refuse", "response_message": _pick(_REFUSE, used), "code_after": None}
    allow_code = wants_code(candidate_prompt, conversation)
    said = candidate_text(design, conversation, candidate_prompt)
    prompt = llm_service._load_prompt("round2_typist_turn.txt").format(
        language=language, conventions=CONVENTIONS.get(language, ""),
        design=llm_service._as_data(_design_text(design)), current_code=llm_service._as_data(current_code or ""),
        conversation=llm_service._as_data("\n".join(f"Candidate: {t.get('candidate_prompt')}\nYou: {t.get('response_message')}"
                                                    for t in conversation) or "(none yet)"),
        candidate_prompt=llm_service._as_data(candidate_prompt),
    )
    note = ""
    reply, code = "", None
    for attempt in range(2):
        raw = llm_service._call_claude_json(prompt + note, max_tokens=llm_service._CODE_REPLY_TOKENS)
        reply = str((raw or {}).get("reply") or "").strip() if isinstance(raw, dict) else ""
        code = (raw or {}).get("code") if isinstance(raw, dict) and allow_code else None
        code = code if isinstance(code, str) and code.strip() else None
        bad = unsaid(reply, said, code=False) + unsaid(code, said, code=True)
        missing_code = allow_code and code is None
        if not bad and reply and not _repeats(reply, earlier) and not missing_code:
            break
        bad_code = unsaid(code, said, code=True)
        note = (_CODE_NOTE.format(terms=", ".join(bad_code)) if bad_code
                else _NOTE.format(terms=", ".join(bad)) if bad else (_WRITE_NOTE if missing_code else _REPEAT_NOTE))
    else:
        # Still naming something unsaid (or repeating): nothing of it reaches the candidate.
        if unsaid(code, said, code=True):
            code = None
        if code is None and allow_code:
            # No code reaches them: a reply saying "here's the code" would be a broken promise.
            reply = _pick(_BLOCKED, used)
        elif not reply or unsaid(reply, said, code=False) or _repeats(reply, earlier):
            reply = _pick(_ASK, used)
    if code is not None:
        todo = _still_to_do(code, said)
        if todo:
            # Said by the tool, not left to the model: the owner's Round 2 (2026-09-27) got code
            # full of incomplete() with no word about why, and was left guessing.
            reply = (reply + "\n\n" + _TODO_NOTE + "\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(todo, 1))).strip()
        return {"response_kind": "code_edit", "response_message": reply, "code_after": code}
    return {"response_kind": "clarify", "response_message": reply, "code_after": None}


_XPATH_LITERAL_RE = re.compile(r""""\(?\.?//(?:[^"\\\n]|\\.)*"|'\(?\.?//(?:[^'\\\n]|\\.)*'""")
_TODO_NOTE = ("Before this can run, tell me for each of these steps of yours how to find it on the screen "
              "(what you see on it, or its id) or which exact value to use:")
_INCOMPLETE_RE = re.compile(r"""incomplete\(\s*(?:"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)')\s*\)""")


def _still_to_do(code: str, said: str) -> list[str]:
    """The candidate's own step words the program still marks incomplete() - listed for
    them in the reply. A step worded with anything they never said is left out."""
    steps = []
    for a, b in _INCOMPLETE_RE.findall(code or ""):
        step = " ".join(re.sub(r"\\(.)", r"\1", a or b).split())
        if step and step not in steps and not unsaid(f'"{step}"', said, code=False):
            steps.append(step)
    return steps[:10]


def _design_text(design: dict | None) -> str:
    if not design:
        return "(none)"
    return "\n".join(f"{k.replace('_', ' ')}: {design.get(k)}" for k in ("title", "preconditions", "steps", "test_data", "expected_result")
                     if design.get(k))


def run_status(exit_code: int | None, stdout: str, timed_out: bool, infra_error: bool) -> str:
    """What the Run shows: "passed" only for a complete test that exited cleanly -
    a test with an incomplete("...") step is never "passed"."""
    if infra_error:
        return "error"
    if INCOMPLETE_MARKER in (stdout or "") or exit_code == INCOMPLETE_EXIT:
        return "incomplete"
    if timed_out:
        return "timed_out"
    return "passed" if exit_code == 0 else "failed"
