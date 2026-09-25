"""
The practice-app factory (services/practice_app/generator.py), driven by a
FAKE AI - no paid calls. The fake hands back the hand-written Doctor
Appointment practice app (sometimes deliberately broken) so each test checks
how the factory reacts: accept a good app, send failures back to be fixed,
refuse an app that never gets fixed, and never crash.
"""
import json
from pathlib import Path

import pytest

from app.services import execution_service, llm_service
from app.services.practice_app import checker, generator

pytestmark = pytest.mark.skipif(
    not all(execution_service.toolchain_available(l) for l in checker.LANGUAGES),
    reason="needs Python, Node.js and a JDK to inspect all three languages",
)

CHECKLISTS = json.loads((Path(__file__).parent / "fixtures" / "practice_app" / "doctor_appointments.json").read_text())
REFERENCE_CASES = [{"title": c["title"], "steps": "see checklist", "expected_result": "as checked"} for c in CHECKLISTS]
APP = {lang: llm_service._load_prompt(f"round2_automation_helpers_appointments_{lang}.txt") for lang in checker.LANGUAGES}


def _plan():
    helpers = []
    for call in sorted({s["call"] for c in CHECKLISTS for s in c["steps"]}):
        layer, _, name = call.rpartition(".")
        helpers.append({"layer": layer or "global", "name": name, "params": [], "returns": "...", "behaviour": "..."})
    return {
        "app_name": "Doctor Appointment System", "summary": "Patients book doctor appointments.",
        "base_url": "https://healthconnect-staging.qa.mediportal.io/patient",
        "test_accounts": [{"login": "qa.patient.demo@testportal.io", "password": "Px!7mK@2024Test", "notes": "active patient"}],
        "data": "Dr. Sarah Johnson (Cardiology) ...", "pages": ["Login", "Doctor Search", "Doctor Profile", "Confirmation"],
        "helpers": helpers, "rules": ["A taken slot can't be booked."],
        "messages": [{"when": "a booking succeeds", "text": "Appointment confirmed"}], "not_supported": [],
    }


class FakeAI:
    """Answers each factory step by recognising its prompt."""

    def __init__(self, python=None, javascript=None, java=None, fixes=None, checklists=None):
        self.code = {"python": python or APP["python"], "javascript": javascript or APP["javascript"], "java": java or APP["java"]}
        self.fixes = fixes or {}                  # language -> list of replies, one per fix request
        self.checklists = checklists or [CHECKLISTS]
        self.prompts = []

    def __call__(self, prompt, max_tokens=4096):
        self.prompts.append(prompt)
        if "designing a small PRACTICE APP" in prompt:
            return json.dumps(_plan())
        if "writing machine-checkable CHECKLISTS" in prompt:
            return json.dumps(self.checklists.pop(0) if len(self.checklists) > 1 else self.checklists[0])
        if "failed its automatic inspection" in prompt:
            language = next(l for l, name in generator.LANGUAGE_NAMES.items() if prompt.startswith(f"This {name} "))
            return f"```{language}\n{self.fixes[language].pop(0)}\n```"
        if "as a single Python file" in prompt:
            return f"```python\n{self.code['python']}\n```"
        for language in ("javascript", "java"):
            if f"from Python to {generator.LANGUAGE_NAMES[language]}." in prompt:
                return f"```{language}\n{self.code[language]}\n```"
        raise AssertionError("unexpected prompt: " + prompt[:80])


def _run(monkeypatch, fake):
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    return generator.generate("Doctor Appointment System", "Patients search doctors and book slots.", REFERENCE_CASES)


def test_a_good_app_is_accepted_after_five_ai_calls(monkeypatch):
    result = _run(monkeypatch, FakeAI())
    assert result.error is None and result.ok, result.log
    assert result.ai_calls == 5  # plan, checklists, python, javascript, java
    assert result.env_code_by_language == {l: APP[l] + "\n" if not APP[l].endswith("\n") else APP[l] for l in checker.LANGUAGES}
    assert all(row["status"] == "works" for row in result.coverage())
    assert len(result.coverage()) == len(REFERENCE_CASES)


def test_a_python_mistake_goes_back_to_the_ai_and_the_fix_is_accepted(monkeypatch):
    broken = APP["python"].replace('"Appointment confirmed"', '"Booked!"')
    fake = FakeAI(python=broken, fixes={"python": [APP["python"]]})
    result = _run(monkeypatch, fake)
    assert result.ok and result.ai_calls == 6, result.log
    fix_prompt = next(p for p in fake.prompts if "failed its automatic inspection" in p)
    assert "Booked!" in fix_prompt and "Appointment confirmed" in fix_prompt


def test_a_translation_that_behaves_differently_is_sent_back_and_fixed(monkeypatch):
    # The Java app as it was before the inspector found it returned less from UI.open.
    old_java = APP["java"].replace(
        "static PageResponse open(String path) { return new PageResponse(BASE_URL + path, 200); }",
        "static String open(String path) { return BASE_URL + path; }",
    )
    assert old_java != APP["java"]
    fake = FakeAI(java=old_java, fixes={"java": [APP["java"]]})
    result = _run(monkeypatch, fake)
    assert result.ok, result.log
    fix_prompt = next(p for p in fake.prompts if p.startswith("This Java "))
    assert "UI.open" in fix_prompt


def test_an_app_that_is_never_fixed_is_refused_and_not_paid_to_translate(monkeypatch):
    broken = APP["python"].replace('"Appointment confirmed"', '"Booked!"')
    fake = FakeAI(python=broken, fixes={"python": [broken, broken]})
    result = _run(monkeypatch, fake)
    assert not result.ok and result.error is None
    assert result.ai_calls == 3 + generator.MAX_FIX_ROUNDS  # plan, checklists, python, fixes - no translations
    assert not any("from Python to" in p for p in fake.prompts)
    failing = [row for row in result.coverage() if row["status"] == "fails"]
    assert any(row["title"] == "Book an available slot and see the confirmation" for row in failing)


def test_a_translation_that_is_never_fixed_is_refused(monkeypatch):
    bad_js = APP["javascript"].replace('"Appointment confirmed"', '"Booked!"')
    fake = FakeAI(javascript=bad_js, fixes={"javascript": [bad_js, bad_js]})
    result = _run(monkeypatch, fake)
    assert not result.ok and result.error is None
    assert result.ai_calls == 5 + generator.MAX_FIX_ROUNDS
    assert result.report.languages["python"].passed == len(CHECKLISTS)
    assert result.report.languages["javascript"].passed < len(CHECKLISTS)


def test_checklists_that_use_helpers_the_design_lacks_are_regenerated(monkeypatch):
    bad = json.loads(json.dumps(CHECKLISTS))
    bad[0]["steps"].append({"call": "UI.print_receipt"})
    fake = FakeAI(checklists=[bad, CHECKLISTS])
    result = _run(monkeypatch, fake)
    assert result.ok, result.log
    retry = [p for p in fake.prompts if "writing machine-checkable CHECKLISTS" in p][1]
    assert "UI.print_receipt" in retry


def test_a_test_case_the_app_cannot_support_is_listed_not_faked(monkeypatch):
    with_gap = json.loads(json.dumps(CHECKLISTS)) + [{"id": "sms", "title": "SMS reminder is sent", "unsupported": "no SMS in the app"}]
    fake = FakeAI(checklists=[with_gap])
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = generator.generate("Doctor Appointment System", "...", REFERENCE_CASES + [{"title": "SMS reminder is sent"}])
    assert result.ok
    assert {"title": "SMS reminder is sent", "status": "not supported", "details": ["no SMS in the app"]} in result.coverage()


def test_an_ai_failure_is_reported_not_crashed(monkeypatch):
    def boom(prompt, max_tokens=4096):
        raise RuntimeError("API unavailable")
    result = _run(monkeypatch, boom)
    assert not result.ok and "API unavailable" in result.error


def test_ground_truth_comes_from_the_same_design():
    text = generator.ground_truth(_plan())
    assert "qa.patient.demo@testportal.io / Px!7mK@2024Test" in text
    assert "'Appointment confirmed'" in text
    assert "A taken slot can't be booked." in text
