"""
Replays what the real AI actually produced in paid runs
(tests/fixtures/practice_engine/real_outputs, added by tools/real_outputs.py)
against today's code - free, no AI calls. Hand-written tests only hold my own
inputs; these hold the AI's, which found ~20 causes my tests had missed.

- every recorded description is still accepted (a rejection is a new false
  alarm in the checker, not an AI mistake)
- every checklist that passed when recorded still passes, alike in Python,
  JavaScript and Java
- every assistant reply that followed the rules is still let through, and
  the replies that broke them are still caught (a guard tested both ways)
"""
import json
from pathlib import Path

import pytest

from app.services import round2_typist
from app.services.practice_app import checker
from app.services.practice_engine import render, validate

LIBRARY = Path(__file__).parent / "fixtures" / "practice_engine" / "real_outputs"
BUILDS = sorted((LIBRARY / "builds").glob("*.json"))
ASSISTANT = sorted((LIBRARY / "assistant").glob("*.json"))


def _load(path):
    return json.loads(path.read_text())


def test_library_is_not_empty():
    assert len(BUILDS) >= 20 and ASSISTANT


@pytest.mark.parametrize("path", BUILDS, ids=lambda p: p.stem)
def test_real_description_still_accepted(path):
    assert validate.problems(_load(path)["spec"]) == []


@pytest.mark.parametrize("path", BUILDS, ids=lambda p: p.stem)
def test_real_checklists_that_passed_still_pass_in_every_language(path):
    entry = _load(path)
    spec, passed = entry["spec"], [c for c in entry["checklists"] if c["recorded_pass"]]
    if not passed:
        pytest.skip("nothing passed when recorded")
    code, support = {}, {}
    for lang in render.LANGUAGES:
        code[lang], support[lang] = render.files(spec, lang)
    report = checker.inspect(code, [{k: v for k, v in c.items() if k != "recorded_pass"} for c in passed], support)
    problems = []
    for lang, r in report.languages.items():
        assert r.error is None, f"[{lang}] {r.error}"
        problems += [f"[{lang}] {res.title}: {res.failures[0]}" for res in r.results if not res.passed]
    assert not problems + report.differences, "\n".join((problems + report.differences)[:20])


def _replies():
    for path in ASSISTANT:
        for r in _load(path)["replies"]:
            yield pytest.param(r, id=f"{path.stem}:{r['conversation']}:{r['turn']}")


@pytest.mark.parametrize("r", list(_replies()))
def test_real_assistant_reply_judged_as_recorded(r):
    named = round2_typist.unsaid(r["reply"], r["said"], code=False) + round2_typist.unsaid(r["code"], r["said"], code=True)
    if r["followed_rules"]:
        assert named == [], f"a reply that followed the rules would now be blocked for {named}"
    elif r["why"].startswith("named unsaid"):
        assert named, "a reply that named unsaid things would now get through"


# Drafts the real AI wrote that broke the rules (paid assistant checks,
# 2026-09-27); the replacement reply went out, the draft never did.
REAL_BAD_DRAFTS = [
    ("Automate TC-01. Log in as Priya. Generate it.",
     "I need to know: What's the URL or page where I start? Which field takes the email?"),
    ("Log in as Priya through the API.",
     "Should I POST to /api/login and expect 200?"),
]


@pytest.mark.parametrize("said,draft", REAL_BAD_DRAFTS)
def test_real_bad_drafts_still_caught(said, draft):
    assert round2_typist.unsaid(draft, said, code=False)
