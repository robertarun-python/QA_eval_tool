"""
Candidates' code will call the practice app with anything: nothing, the wrong
type, huge or odd text, injection strings. Every helper must answer (True /
False / a result / a refusal message) and never crash - identically in
Python, JavaScript and Java. No AI calls.
"""
import json
from pathlib import Path

from app.services.practice_app import checker
from app.services.practice_engine import render

SPEC = json.loads((Path(__file__).parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())
HOSTILE = [None, "", "   ", "x" * 5000, "' OR '1'='1", "<script>alert(1)</script>", "Robert'); DROP TABLE books;--",
           "éè\U0001F600中", "-1", "0", "1e9", "BK-001 ", "bk-001", -5, 0, 3.5, 10**12, True, False]
LOGIN = [{"call": "setup"}, {"call": "UI.login", "args": ["testuser@library.test", "Test@123"]}]


def _checklists():
    lists = []
    for h in render.helpers(SPEC):
        if h["call"] in ("Test.simulate",):  # an unknown failure name is refused loudly on purpose (a typo in the test)
            continue
        for n, value in enumerate(HOSTILE):
            args = [value] * len(h["params"])
            page = next((q.get("page") for q in SPEC["queries"] + SPEC["actions"] if h["call"].endswith("." + q["name"])), None)
            call = {"call": h["call"], "args": args}
            if h["call"] == "Test.advance_minutes" and not (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 5256000):
                call["expect_error"] = True  # a test-code mistake: refused loudly, the same in every language
            steps = LOGIN + ([{"call": "UI.open", "args": [page]}] if page else []) + [call]
            # Afterwards the app must still work normally (a refused test-code mistake ends the run there).
            steps += [] if call.get("expect_error") else [{"call": "UI.logout"}, {"call": "UI.login", "args": ["testuser@library.test", "Test@123"], "expect": True},
                      {"call": "Database.count_book", "expect": 6}]
            lists.append({"id": f"{h['call']}#{n}", "title": h["call"], "steps": steps})
    return lists


def test_hostile_inputs_never_crash_and_every_language_agrees():
    checklists = _checklists()
    code, support = {}, {}
    for lang in render.LANGUAGES:
        code[lang], support[lang] = render.files(SPEC, lang)
    report = checker.inspect(code, checklists, support)
    problems = []
    for lang, r in report.languages.items():
        assert r.error is None, r.error
        problems += [f"[{lang}] {res.id}: {res.failures[0]}" for res in r.results if not res.passed]
    problems += report.differences
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])
