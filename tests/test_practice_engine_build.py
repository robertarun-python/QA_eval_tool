"""
The engine-based practice-app factory (practice_app.engine_build) with a
scripted AI: the AI writes a description, never code; the three language
files are rendered; failures are corrected in the description. No AI calls.
"""
import copy
import json
from pathlib import Path

from app.services import llm_service
from app.services.practice_app import engine_build

SPEC = json.loads((Path(__file__).parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())
CASES = [
    {"title": "Borrow an available book", "priority": "High", "steps": "Log in, open BK-001, borrow", "expected_result": "Loan created, copies drop to 2"},
    {"title": "Borrow a book with no copies", "priority": "Medium", "steps": "Open BK-002, borrow", "expected_result": "No copies available"},
]
LOGIN = [{"call": "setup"}, {"call": "UI.login", "args": ["testuser@library.test", "Test@123"], "expect": True}]
CHECKLISTS = [
    {"id": "borrow", "title": "Borrow an available book", "steps": LOGIN + [
        {"call": "UI.open", "args": ["Search"], "expect": True},
        {"call": "UI.open_book", "args": ["BK-001"]},
        {"call": "UI.borrow_book", "args": ["BK-001"], "expect": True},
        {"call": "UI.visible_message", "expect": "Book borrowed successfully. Due date: 24-Feb-2024"},
        {"call": "Database.get_book", "args": ["BK-001"], "expect_includes": {"available_copies": 2}}]},
    {"id": "no-copies", "title": "Borrow a book with no copies", "steps": LOGIN + [
        {"call": "UI.open", "args": ["Search"], "expect": True},
        {"call": "UI.open_book", "args": ["BK-002"]},
        {"call": "UI.borrow_book", "args": ["BK-002"], "expect": False},
        {"call": "UI.visible_message", "expect": "No copies available"},
        {"call": "Database.count_loan", "args": ["book", "BK-002"], "expect": 0}]},
]


class FakeAI:
    """Replies by which prompt it is given; records every call."""
    def __init__(self, describes, fixes=(), repairs=()):
        self.describes, self.fixes, self.repairs = list(describes), list(fixes), list(repairs)
        self.calls = []

    def __call__(self, prompt, max_tokens=None, **_):
        if "You do NOT write code" in prompt:
            kind, reply = "describe", self.describes.pop(0)
        elif "Correct the description" in prompt:
            kind, reply = "fix", self.fixes.pop(0)
        elif "the checklists themselves may be what's wrong" in prompt:
            kind, reply = "repair", self.repairs.pop(0)
        elif "CHECKLISTS for the practice app" in prompt:
            kind, reply = "checklists", CHECKLISTS
        else:
            raise AssertionError("unexpected prompt")
        self.calls.append(kind)
        return json.dumps(reply)


def _describe(spec):
    return {"spec": spec, "summary": "A library", "rules": ["Up to 5 books"], "not_supported": [],
            "screens": [{"name": "Login", "elements": [{"type": "button", "text": "Log in"}]}]}


def _build(monkeypatch, fake, reuse=None):
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    return engine_build.generate("Library", "A library app", CASES, reuse=reuse)


def test_a_good_description_is_ready_after_two_ai_calls_in_every_language(monkeypatch):
    fake = FakeAI([_describe(SPEC)])
    result = _build(monkeypatch, fake)
    assert result.error is None, result.log
    assert result.ok and fake.calls == ["describe", "checklists"]
    assert set(result.env_code_by_language) == {"python", "javascript", "java"}
    assert result.report.all_passed and not result.report.differences
    assert result.plan["engine_spec"] == SPEC and result.plan["screens"]
    borrow = next(h for h in result.plan["helpers"] if h["layer"] == "UI" and h["name"] == "borrow_book")
    assert borrow["changes_data"] and borrow["returns"] == "true/false"


def test_description_problems_go_back_to_the_ai(monkeypatch):
    broken = copy.deepcopy(SPEC)
    broken["actions"][0]["page"] = "Checkout"
    fake = FakeAI([_describe(broken), _describe(SPEC)])
    result = _build(monkeypatch, fake)
    assert result.ok, result.log
    assert fake.calls == ["describe", "describe", "checklists"]
    assert any("'Checkout' is not one of the pages" in line for line in result.log)


def test_a_description_that_never_validates_is_reported_not_built(monkeypatch):
    broken = copy.deepcopy(SPEC)
    broken["actions"][0]["page"] = "Checkout"
    fake = FakeAI([_describe(broken)] * 3)
    result = _build(monkeypatch, fake)
    assert result.error and "Checkout" in result.error and not result.env_code_by_language


def test_a_failing_test_case_is_fixed_in_the_description(monkeypatch):
    missing_rule = copy.deepcopy(SPEC)
    missing_rule["actions"][0]["rules"] = missing_rule["actions"][0]["rules"][1:]  # "No copies available" forgotten
    fake = FakeAI([_describe(missing_rule)], repairs=[[CHECKLISTS[1]]], fixes=[{"spec": SPEC}])
    result = _build(monkeypatch, fake)
    assert result.ok, result.log
    assert fake.calls == ["describe", "checklists", "repair", "fix"]
    assert result.plan["engine_spec"] == SPEC


def test_a_correction_that_makes_it_worse_is_not_kept(monkeypatch):
    missing_rule = copy.deepcopy(SPEC)
    missing_rule["actions"][0]["rules"] = missing_rule["actions"][0]["rules"][1:]
    worse = copy.deepcopy(missing_rule)
    worse["actions"][0]["message"] = "Borrowed"
    fake = FakeAI([_describe(missing_rule)], repairs=[[CHECKLISTS[1]]], fixes=[{"spec": worse}, {"spec": worse}])
    result = _build(monkeypatch, fake)
    assert not result.ok and result.plan["engine_spec"] == missing_rule
    assert result.verified() == (1, 2)


def test_generate_again_continues_from_the_last_description_without_describing_again(monkeypatch):
    first = _build(monkeypatch, FakeAI([_describe(SPEC)]))
    fake = FakeAI([])
    result = _build(monkeypatch, fake, reuse={"plan": first.plan, "checklists": first.checklists, "unsupported": []})
    assert result.ok and fake.calls == []


def test_an_ai_failure_is_reported_not_crashed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("API down")
    result = _build(monkeypatch, boom)
    assert result.error == "RuntimeError: API down"
