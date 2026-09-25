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
        helpers.append({"layer": layer or "global", "name": name, "params": [], "returns": "...", "behaviour": "...",
                        "changes_data": call in ("UI.book", "API.book")})
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

    def __init__(self, python=None, javascript=None, java=None, fixes=None, checklists=None, repairs=None):
        self.code = {"python": python or APP["python"], "javascript": javascript or APP["javascript"], "java": java or APP["java"]}
        self.fixes = fixes or {}                  # language -> list of replies, one per fix request
        self.checklists = checklists or [CHECKLISTS]
        self.repairs = repairs or []              # replies to "these checklists keep failing", in order
        self.prompts = []

    def __call__(self, prompt, max_tokens=4096):
        self.prompts.append(prompt)
        if "designing a small PRACTICE APP" in prompt:
            return json.dumps(_plan())
        if "writing machine-checkable CHECKLISTS" in prompt:
            return json.dumps(self.checklists.pop(0) if len(self.checklists) > 1 else self.checklists[0])
        if "fixing the app's code hasn't helped" in prompt:
            return json.dumps(self.repairs.pop(0)) if self.repairs else "[]"
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
    # plan, checklists, python, the fixes and one look at the checklists - no translations
    assert result.ai_calls == 3 + generator.MAX_FIX_ROUNDS + 1
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


def test_a_checklist_that_books_without_checking_the_database_is_regenerated(monkeypatch):
    """The first real trial proved only 3 of 28 checklists against what was really stored."""
    lazy = json.loads(json.dumps(CHECKLISTS))
    booking = next(c for c in lazy if c["id"] == "book-taken-slot")
    booking["steps"] = [s for s in booking["steps"] if not s["call"].startswith("Database.")]
    fake = FakeAI(checklists=[lazy, CHECKLISTS])
    result = _run(monkeypatch, fake)
    assert result.ok, result.log
    retry = [p for p in fake.prompts if "writing machine-checkable CHECKLISTS" in p][1]
    assert "book-taken-slot" in retry and "never checks the Database" in retry


def test_a_test_case_left_without_a_valid_checklist_still_shows_in_the_report(monkeypatch):
    bad = [c for c in json.loads(json.dumps(CHECKLISTS)) if c["id"] != "search-name"]
    fake = FakeAI(checklists=[bad, bad])
    result = _run(monkeypatch, fake)
    assert not result.ok
    row = next(r for r in result.coverage() if r["title"] == "Search doctor by name")
    assert row["status"] == "fails"


def test_facts_candidates_already_see_are_given_to_the_planner(monkeypatch):
    fake = FakeAI()
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    generator.generate("Doctor Appointment System", "...", REFERENCE_CASES,
                       known_facts={"fields": {"Test account email": "qa.patient.demo@testportal.io"}})
    assert "qa.patient.demo@testportal.io" in fake.prompts[0]


def test_progress_is_reported_through_every_step_in_order(monkeypatch):
    steps = []
    monkeypatch.setattr(llm_service, "_call_claude", FakeAI())
    result = generator.generate("Doctor Appointment System", "...", REFERENCE_CASES, progress=lambda i, d: steps.append(i))
    assert result.ok
    assert steps == list(range(len(generator.STEPS)))


def test_a_broken_progress_callback_never_breaks_a_build(monkeypatch):
    def boom(i, d):
        raise RuntimeError("screen went away")
    monkeypatch.setattr(llm_service, "_call_claude", FakeAI())
    assert generator.generate("Doctor Appointment System", "...", REFERENCE_CASES, progress=boom).ok


@pytest.mark.parametrize("breakage", ["true/false result", "no earlier step saved"])
def test_a_checklist_whose_ref_cant_hold_its_value_is_regenerated(monkeypatch, breakage):
    """The Library build's checklists expected Borrow to return true AND used
    its result as the borrowing id - no app can pass that, so every fix undid
    the last one. Caught before any code is written now."""
    bad = json.loads(json.dumps(CHECKLISTS))
    booking = next(c for c in bad if c["id"] == "api-booking")
    saved = next(s for s in booking["steps"] if s.get("save_as") == "booked")
    if breakage == "true/false result":
        saved["expect"] = True
        saved.pop("expect_includes", None)
    else:
        saved.pop("save_as")
    fake = FakeAI(checklists=[bad, CHECKLISTS])
    result = _run(monkeypatch, fake)
    assert result.ok, result.log
    retry = [p for p in fake.prompts if "writing machine-checkable CHECKLISTS" in p][1]
    assert "api-booking" in retry and breakage in retry


def test_a_helper_the_design_says_returns_true_false_cannot_be_used_as_an_id():
    plan = {"helpers": [{"layer": "UI", "name": "click_borrow", "returns": "Boolean: true if borrow succeeded"},
                        {"layer": "UI", "name": "click_return", "returns": "None"}]}
    checklist = {"id": "return-book", "steps": [
        {"call": "setup"}, {"call": "UI.click_borrow", "save_as": "borrowed"},
        {"call": "UI.click_return", "args": [{"ref": "borrowed"}]}]}
    assert "true/false result of UI.click_borrow" in generator._ref_problem(checklist, generator._true_false_helpers(plan))


_BENEFICIARY_PLAN = {
    "data": "1. id=BEN001, name='Rajesh Kumar', account_number='1234567890123456', user='testuser@bank.test'",
    "helpers": [
        {"layer": "UI", "name": "submit", "returns": "Boolean", "changes_data": True},
        {"layer": "Database", "name": "delete", "returns": "Boolean", "changes_data": True},
        {"layer": "Database", "name": "exists", "returns": "Boolean", "changes_data": False},
    ],
}


def _missing_start_record(steps):
    plan = _BENEFICIARY_PLAN
    return generator._missing_start_record_problem({"id": "c", "steps": steps}, plan, generator._data_changing_helpers(plan))


def test_a_check_that_a_starting_record_is_missing_is_sent_back():
    """The Beneficiary build: a refused add was checked as 'not stored' using
    an account number the design had pre-loaded - it can never pass."""
    problem = _missing_start_record([{"call": "setup"}, {"call": "UI.submit", "expect": False},
                                     {"call": "Database.exists", "args": ["1234567890123456", "testuser@bank.test"], "expect": False}])
    assert "'1234567890123456' is in the design's starting data" in problem


@pytest.mark.parametrize("steps", [
    # removed first - by id, so the account number never appears in that step
    [{"call": "Database.delete", "args": ["BEN001"], "expect": True},
     {"call": "Database.exists", "args": ["1234567890123456", "testuser@bank.test"], "expect": False}],
    # a value that isn't in the starting data, with the owner (who is) second
    [{"call": "Database.exists", "args": ["5555666677778888", "testuser@bank.test"], "expect": False}],
    # only part of a starting value
    [{"call": "Database.exists", "args": ["12345678", "testuser@bank.test"], "expect": False}],
])
def test_legitimate_missing_record_checks_are_not_flagged(steps):
    assert _missing_start_record(steps) is None


def _with_wrong_message():
    """The checklists with one that contradicts the design: it expects
    "Booked!" where the design (and the correct app) says "Appointment confirmed"."""
    wrong = json.loads(json.dumps(CHECKLISTS))
    booking = next(c for c in wrong if c["id"] == "book-free-slot")
    booking["steps"] = json.loads(json.dumps(booking["steps"]).replace("Appointment confirmed", "Booked!"))
    return wrong, next(c for c in CHECKLISTS if c["id"] == "book-free-slot")


def test_a_checklist_that_contradicts_the_design_is_rewritten_not_fought(monkeypatch):
    """Every stuck build today was a wrong checklist: fixing the (correct)
    code can never pass it. After one code fix it goes back to be checked."""
    wrong, right = _with_wrong_message()
    fake = FakeAI(checklists=[wrong], fixes={"python": [APP["python"], APP["python"]]}, repairs=[[right]])
    result = _run(monkeypatch, fake)
    assert result.ok, result.log
    assert "rewrote 1 failing checklist(s): book-free-slot" in result.log
    repair_prompt = next(p for p in fake.prompts if "fixing the app's code hasn't helped" in p)
    assert "Booked!" in repair_prompt and "book-free-slot" in repair_prompt
    assert '"id": "api-booking"' not in repair_prompt  # only the failing checklists are sent back
    assert next(c for c in result.checklists if c["id"] == "book-free-slot") == right


def test_a_rewrite_that_checks_less_is_refused(monkeypatch):
    """The AI can't make a checklist pass by dropping what it checks."""
    wrong, _ = _with_wrong_message()
    weakened = json.loads(json.dumps(next(c for c in wrong if c["id"] == "book-free-slot")))
    weakened["steps"] = [s for s in weakened["steps"] if "Booked!" not in json.dumps(s)]
    fake = FakeAI(checklists=[wrong], fixes={"python": [APP["python"], APP["python"]]}, repairs=[[weakened]])
    result = _run(monkeypatch, fake)
    assert not result.ok
    assert "the failing checklists match the design - kept as they were" in result.log


def test_a_rewrite_must_keep_its_id_and_title_and_be_valid():
    plan = _plan()
    original = next(c for c in CHECKLISTS if c["id"] == "book-free-slot")
    retitled = {**original, "title": "Something easier"}
    unknown_helper = {**original, "steps": original["steps"] + [{"call": "UI.print_receipt", "expect": True}]}
    for bad in (retitled, unknown_helper):
        kept, replaced = generator.accept_repairs(plan, CHECKLISTS, [bad], {"book-free-slot"})
        assert replaced == [] and kept == CHECKLISTS
    # and only checklists that actually failed can be replaced
    other = next(c for c in CHECKLISTS if c["id"] != "book-free-slot")
    changed_other = {**other, "steps": other["steps"] + [{"call": "setup"}]}
    assert generator.accept_repairs(plan, CHECKLISTS, [changed_other], {"book-free-slot"})[1] == []


def test_translations_keep_string_values_like_field_names_as_in_python(monkeypatch):
    """The Beneficiary JavaScript renamed its fields to camelCase, so a field
    name passed as a string ("account_number") matched nothing - and the fix
    rounds, told to use camelCase, kept it. Translate and fix prompts both
    carry the rule now."""
    bad_js = APP["javascript"].replace('"Appointment confirmed"', '"Booked!"')
    fake = FakeAI(javascript=bad_js, fixes={"javascript": [APP["javascript"]]})
    _run(monkeypatch, fake)
    rule = "must be exactly the same string as in the Python version"
    translations = [p for p in fake.prompts if "from Python to" in p]
    js_fix = next(p for p in fake.prompts if p.startswith("This JavaScript "))
    assert len(translations) == 2 and all(rule in p for p in translations) and rule in js_fix


def _reuse(python):
    return {"plan": _plan(), "checklists": CHECKLISTS, "unsupported": [], "python": python}


def test_a_python_app_that_still_passes_is_reused_and_only_translated(monkeypatch):
    """Generate again after only a translation failed: two AI calls, not ten."""
    fake = FakeAI()
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = generator.generate("Doctor Appointment System", "...", REFERENCE_CASES, reuse=_reuse(APP["python"]))
    assert result.ok, result.log
    assert result.ai_calls == 2 and all("from Python to" in p for p in fake.prompts)
    assert any(line.startswith("reused the last build's design") for line in result.log)
    assert result.env_code_by_language["python"] == APP["python"]


def test_a_saved_python_app_that_fails_is_ignored_and_rebuilt(monkeypatch):
    broken = APP["python"].replace('"Appointment confirmed"', '"Booked!"')
    fake = FakeAI()
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = generator.generate("Doctor Appointment System", "...", REFERENCE_CASES, reuse=_reuse(broken))
    assert result.ok, result.log
    assert result.ai_calls == 5  # the full build: plan, checklists, python, two translations
    assert "the last build's Python app no longer passes every checklist - building from scratch" in result.log
