"""
Real practice-app builds (tests/fixtures/practice_app/real_builds) - the
design, checklists and code the real AI produced, for Round 1 scenarios HR
actually built. Everything in the factory that doesn't call the AI must keep
giving the same verdict on them: the checker, the checklist validators, the
repair guard, the retry merge. No AI calls.

Until 2026-09-25 the factory was only tested against one hand-written app, which
never contains the contradictions real AI output does - so every factory change
was first tested by a paid live build. Add each new real build here (the good
and the failed) so a change can't quietly break an app that worked.
"""
import json
from pathlib import Path

import pytest

from app.services import execution_service
from app.services.practice_app import checker, generator

FIXTURES = Path(__file__).parent / "fixtures" / "practice_app" / "real_builds"
GOOD = ["library", "credit_card"]


def _load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _languages(build):
    return {lang: code for lang, code in build["env_code_by_language"].items() if execution_service.toolchain_available(lang)}


@pytest.mark.parametrize("name", GOOD)
def test_a_real_build_that_worked_still_passes_every_checklist_in_every_language(name):
    build = _load(name)["build"]
    report = checker.inspect(_languages(build), build["checklists"])
    failures = [f"[{lang}] {r.id}: {f}" for lang, rep in report.languages.items() for r in rep.results for f in r.failures]
    errors = [rep.error for rep in report.languages.values() if rep.error]
    assert not errors and not failures and not report.differences, "\n".join(errors + failures + report.differences)


@pytest.mark.parametrize("name", GOOD)
def test_the_checklist_validators_raise_no_false_alarm_on_a_real_build_that_worked(name):
    fixture = _load(name)
    build = fixture["build"]
    unsupported_titles = {u["title"].strip().lower() for u in fixture["unsupported"]}
    reference = [c for c in fixture["reference_cases"] if c["title"].strip().lower() not in unsupported_titles]
    runnable, _, problems = generator.validate_checklists(build["plan"], build["checklists"], reference)
    assert problems == [] and runnable == build["checklists"]


@pytest.mark.parametrize("name", GOOD)
def test_nothing_in_a_real_build_that_worked_would_be_rewritten(name):
    build = _load(name)["build"]
    ids = {c["id"] for c in build["checklists"]}
    kept, replaced = generator.accept_repairs(build["plan"], build["checklists"], build["checklists"], ids)
    assert replaced == [] and kept == build["checklists"]


def test_the_failed_beneficiary_build_still_fails_the_same_way():
    """The checker's verdict on a real failure is stable - the same failing
    checklists every run - so a change to it shows up here, not in a paid build."""
    build = _load("beneficiary_failed")["build"]
    report = checker.inspect({"python": build["env_code_by_language"]["python"]}, build["checklists"])
    failing = sorted(r.id for r in report.languages["python"].results if not r.passed)
    assert report.languages["python"].passed == 16 and len(failing) == 3, failing


def test_a_real_retry_cannot_drop_a_real_good_checklist():
    """Replays the Beneficiary failure's shape on real data: a retry that breaks
    good checklists (an invented "assert" step) must not replace them."""
    build = _load("library")["build"]
    first = json.loads(json.dumps(build["checklists"]))
    first[0]["steps"].append({"call": "UI.no_such_helper"})
    retried = json.loads(json.dumps(build["checklists"]))
    for c in retried[1:]:
        c["steps"].append({"call": "assert", "args": [True]})
    valid, _, _ = generator.validate_checklists(build["plan"], first, [])
    merged = generator.merge_checklist_retry(first, valid, retried)
    runnable, _, problems = generator.validate_checklists(build["plan"], merged, [])
    assert problems == [] and runnable == build["checklists"]


def _replay(name):
    """The approval decision on a real build, from its saved Python app - as the
    factory would decide it after the Python step."""
    fixture = _load(name)
    build = fixture["build"]
    refs = fixture["reference_cases"]
    result = generator.PracticeAppResult(
        plan=build["plan"], checklists=build["checklists"], unsupported=fixture["unsupported"],
        reference_titles=[c["title"] for c in refs],
        high_priority_titles={c["title"].strip().lower() for c in refs if c.get("priority") == "High"},
    )
    result.report = checker.inspect({"python": build["env_code_by_language"]["python"]}, build["checklists"])
    return result


@pytest.mark.parametrize("name, verified, approvable", [
    ("measured_bus_1", (10, 12), False),   # 83%, and two High-priority cases unverified
    ("measured_bus_2", (11, 12), False),   # 92%, but "Cannot select already occupied seat" is High priority
    ("measured_library", (26, 28), True),  # 93%; the unverified ones are Medium (one "not supported")
])
def test_the_approval_rules_judge_the_measured_builds_as_reviewed(name, verified, approvable):
    result = _replay(name)
    assert result.verified() == verified
    assert (result.approval_problem() is None) == approvable, result.approval_problem()
