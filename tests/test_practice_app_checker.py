"""
The practice-app inspector (services/practice_app/checker.py): runs the same
language-neutral checklists against a Round 2 practice app in Python,
JavaScript and Java, and reports failures and cross-language differences.
No AI calls. Languages whose toolchain isn't installed are skipped.
"""
import json
from pathlib import Path

import pytest

from app.services import execution_service, llm_service
from app.services.practice_app import checker

FIXTURE = Path(__file__).parent / "fixtures" / "practice_app" / "doctor_appointments.json"
CHECKLISTS = json.loads(FIXTURE.read_text())


def _available(languages=checker.LANGUAGES):
    return [l for l in languages if execution_service.toolchain_available(l)]


def _env(language):
    return llm_service._load_prompt(f"round2_automation_helpers_appointments_{language}.txt")


def test_doctor_appointment_app_passes_every_checklist_identically_in_every_language():
    languages = _available()
    report = checker.inspect({l: _env(l) for l in languages}, CHECKLISTS)
    failures = [f"[{l}] {c.id}: {f}" for l, r in report.languages.items() for c in r.results for f in c.failures]
    errors = [r.error for r in report.languages.values() if r.error]
    assert not errors and not failures and not report.differences, "\n".join(errors + failures + report.differences)
    assert all(r.passed == len(CHECKLISTS) for r in report.languages.values())
    assert report.all_passed


def test_a_wrong_message_is_reported_as_a_failed_step():
    broken = _env("python").replace('"Appointment confirmed"', '"Booked!"')
    report = checker.inspect({"python": broken}, CHECKLISTS)
    result = next(c for c in report.languages["python"].results if c.id == "book-free-slot")
    assert not result.passed
    assert any("UI.visible_message" in f and "Booked!" in f and "Appointment confirmed" in f for f in result.failures)
    assert not report.all_passed


@pytest.mark.skipif(not _available(["javascript"]), reason="node not installed")
def test_languages_that_disagree_are_reported_even_when_no_checklist_says_which_is_right():
    # The JavaScript app offers one slot fewer; only "dates-and-slots" pins the slots down,
    # but the difference must show up wherever the two apps are compared.
    js = _env("javascript").replace('"15:30"', '"16:00"')
    report = checker.inspect({"python": _env("python"), "javascript": js}, CHECKLISTS)
    assert any("available_slots" in d and "16:00" in d for d in report.differences)
    assert not report.all_passed


def test_an_app_that_does_not_load_is_reported_not_crashed():
    report = checker.inspect({"python": "def broken(:\n"}, CHECKLISTS[:2])
    result = report.languages["python"].results[0]
    assert not result.passed and "failed to load" in result.failures[0]


def test_a_missing_helper_is_reported_clearly():
    checklist = [{"id": "x", "steps": [{"call": "UI.cancel_appointment", "args": ["APT-0001"]}]}]
    report = checker.inspect({"python": _env("python")}, checklist)
    assert "UI.cancel_appointment" in report.languages["python"].results[0].failures[0]


@pytest.mark.parametrize("actual, expected, includes", [
    ({"a": 1, "b": 2}, {"a": 1}, True),
    ({"a": 1}, {"a": 2}, False),
    (["x", "y"], ["y"], True),
    (["x"], ["z"], False),
    ("Welcome back, Alex", "Welcome", True),
    ([{"id": 1, "n": "a"}], [{"n": "a"}], True),
])
def test_includes(actual, expected, includes):
    assert checker._includes(actual, expected) is includes


def test_numbers_compare_by_value_but_true_is_not_one():
    assert checker._same(150, 150.0)
    assert not checker._same(True, 1)


def test_a_ref_can_pick_an_item_out_of_a_returned_list_in_every_language():
    # "mine.0.id" - the first appointment in a returned list. Python and Java
    # used to resolve it to null, so a checklist that returned a book picked
    # from "My Books" failed in every language but JavaScript.
    checklist = {"id": "list-item-ref", "title": "Pick from a list", "steps": [
        {"call": "setup"},
        {"call": "API.book", "args": ["qa.patient.demo@testportal.io", "D-102", "2025-01-17", "14:00"], "expect_includes": {"ok": True}},
        {"call": "Database.appointments_for", "args": ["qa.patient.demo@testportal.io"], "save_as": "mine"},
        {"call": "Database.find_appointment", "args": [{"ref": "mine.0.id"}], "expect_includes": {"status": "confirmed", "doctor_id": "D-102"}},
    ]}
    languages = _available()
    report = checker.inspect({l: _env(l) for l in languages}, [checklist])
    failures = [f"[{l}] {f}" for l, r in report.languages.items() for c in r.results for f in c.failures]
    assert not failures and not report.differences, "\n".join(failures + report.differences)
    assert set(report.languages) == set(languages) and all(r.passed == 1 for r in report.languages.values())
