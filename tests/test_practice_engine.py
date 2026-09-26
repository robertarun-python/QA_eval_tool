"""
The practice-app engine (services/practice_engine): a scenario description
runs on one proven runtime, and the generated file keeps the shape checker.py
and candidate execution rely on. Checked here through the real checker with
checklists for the Library app's Round 1 test cases. No AI calls.
"""
import json
from pathlib import Path

import pytest

from app.services.practice_app import checker
from app.services.practice_engine import render

SPEC = json.loads((Path(__file__).parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())
LOGIN = [{"call": "setup"}, {"call": "UI.login", "args": ["testuser@library.test", "Test@123"], "expect": True}]


RENDER = {lang: (lambda spec, lang=lang: render.files(spec, lang)) for lang in render.LANGUAGES}


def _inspect(language, spec, checklists):
    """Runs checklists against the app exactly as candidates get it: their file plus the engine file next to it."""
    candidate, support = RENDER[language](spec)
    return checker.inspect({language: candidate}, checklists, {language: support})


@pytest.fixture(params=sorted(RENDER), autouse=True)
def language(request):
    return request.param


def _one(steps, language=None):
    """Runs one checklist in the language under test - every engine test runs in every language."""
    language = language or _LANGUAGE[0]
    report = _inspect(language, SPEC, [{"id": "c", "title": "c", "steps": steps}])
    lang = report.languages[language]
    assert lang.error is None, lang.error
    result = lang.results[0]
    assert result.passed, f"[{language}] {result.failures}"


_LANGUAGE = ["python"]


@pytest.fixture(autouse=True)
def _current_language(language):
    _LANGUAGE[0] = language


def test_login_valid_invalid_empty_and_blocked():
    _one(LOGIN + [{"call": "UI.current_page", "expect": "Home"}, {"call": "UI.signed_in_user", "expect": "Test User"}])
    _one([{"call": "setup"}, {"call": "UI.login", "args": ["testuser@library.test", "wrong"], "expect": False},
          {"call": "UI.visible_message", "expect": "Invalid email or password"}, {"call": "UI.current_page", "expect": "Login"}])
    _one([{"call": "setup"}, {"call": "UI.login", "args": ["", "x"], "expect": False}, {"call": "UI.visible_message", "expect": "Email is required"}])
    _one([{"call": "setup"}, {"call": "UI.login", "args": ["blocked@library.test", "Block@123"], "expect": False},
          {"call": "UI.visible_message", "expect": "Your membership is suspended"}])
    _one([{"call": "setup"}, {"call": "API.login", "args": ["TESTUSER@library.test", "Test@123"], "expect_includes": {"ok": True, "user": "Test User"}}])


def test_search_partial_case_insensitive_no_results_blank_and_long():
    search = LOGIN + [{"call": "UI.open", "args": ["Search"], "expect": True}]
    _one(search + [{"call": "UI.search_books", "args": ["fitzgerald"], "expect": [
        {"id": "BK-001", "title": "The Great Gatsby", "author": "F. Scott Fitzgerald", "isbn": "978-0-7432-7356-5", "available_copies": 3},
        {"id": "BK-006", "title": "This Side of Paradise", "author": "F. Scott Fitzgerald", "isbn": "978-0-684-84248-6", "available_copies": 2}]}])
    _one(search + [{"call": "UI.search_books", "args": ["zzz"], "expect": []}, {"call": "UI.visible_message", "expect": "No books found"}])
    _one(search + [{"call": "UI.search_books", "args": ["  "], "expect": []}, {"call": "UI.visible_message", "expect": "Please enter a title, author or ISBN"}])
    _one(search + [{"call": "UI.search_books", "args": ["x" * 101], "expect": []},
                   {"call": "UI.visible_message", "expect": "Search text must be 100 characters or fewer"}])
    _one(search + [{"call": "UI.search_books", "args": ["' OR 1=1 --"], "expect": []}])  # injection text is just text


def test_borrow_updates_stock_creates_a_loan_with_the_due_date_and_emails():
    _one(LOGIN + [
        {"call": "UI.open", "args": ["Search"]},
        {"call": "UI.open_book", "args": ["BK-001"], "expect_includes": {"available_copies": 3}},
        {"call": "UI.current_page", "expect": "Book Details"},
        {"call": "UI.borrow_book", "args": ["BK-001"], "expect": True},
        {"call": "UI.visible_message", "expect": "Book borrowed successfully. Due date: 24-Feb-2024"},
        {"call": "UI.current_page", "expect": "Borrow Confirmation"},
        {"call": "UI.last_result", "expect": {"loan_id": "LN-006", "due_on": "2024-02-24"}},
        {"call": "Database.get_book", "args": ["BK-001"], "expect_includes": {"available_copies": 2}},
        {"call": "Database.get_loan", "args": ["LN-006"], "expect_includes": {"member": "testuser@library.test", "status": "Borrowed"}},
        {"call": "Database.outbox", "expect": [{"channel": "email", "to": "testuser@library.test", "subject": "Borrowed: The Great Gatsby"}]},
    ])


def test_refusals_change_nothing():
    _one(LOGIN + [{"call": "UI.open", "args": ["Search"]}, {"call": "UI.open_book", "args": ["BK-002"]},
                  {"call": "UI.borrow_book", "args": ["BK-002"], "expect": False},
                  {"call": "UI.visible_message", "expect": "No copies available"},
                  {"call": "Database.count_loan", "expect": 5}])
    _one([{"call": "setup"}, {"call": "UI.login", "args": ["maxuser@library.test", "Max@123"]},
          {"call": "UI.open", "args": ["Search"]}, {"call": "API.borrow_book", "args": ["BK-003"],
                                                   "expect_includes": {"ok": False, "error": "You have already borrowed this book"}},
          {"call": "API.borrow_book", "args": ["BK-002"], "expect_includes": {"ok": False, "error": "No copies available"}}])
    _one(LOGIN + [{"call": "Test.simulate", "args": ["network_down"]},
                  {"call": "API.borrow_book", "args": ["BK-001"], "expect_includes": {"ok": False, "error": "Network error. Please try again."}},
                  {"call": "Database.get_book", "args": ["BK-001"], "expect_includes": {"available_copies": 3}}])


def test_limit_boundary_return_and_borrow_again():
    _one([{"call": "setup"}, {"call": "UI.login", "args": ["maxuser@library.test", "Max@123"]},
          {"call": "API.borrow_book", "args": ["BK-002"], "expect_includes": {"ok": False}},
          {"call": "UI.open", "args": ["My Books"]},
          {"call": "UI.my_loans", "save_as": "loans"},
          {"call": "UI.return_book", "args": [{"ref": "loans.0.id"}], "expect": True},
          {"call": "UI.visible_message", "expect": "Book returned successfully"},
          {"call": "Database.get_book", "args": ["BK-003"], "expect_includes": {"available_copies": 5}},
          {"call": "API.borrow_book", "args": ["BK-003"], "expect_includes": {"ok": True, "loan_id": "LN-006"}},
          {"call": "API.borrow_book", "args": ["BK-001"], "expect_includes": {"ok": False, "error": "You have already borrowed this book"}}])


def test_session_expiry_and_login_required():
    _one([{"call": "setup"}, {"call": "UI.open", "args": ["Search"], "expect": False}, {"call": "UI.visible_message", "expect": "Please log in first"}])
    _one(LOGIN + [{"call": "Test.advance_minutes", "args": [31]}, {"call": "UI.open", "args": ["Search"], "expect": False},
                  {"call": "UI.visible_message", "expect": "Your session has expired. Please log in again."},
                  {"call": "UI.current_page", "expect": "Login"}])


def test_actions_only_on_their_page_and_teardown_removes_what_a_test_created():
    _one(LOGIN + [{"call": "UI.borrow_book", "args": ["BK-001"], "expect": False},
                  {"call": "UI.visible_message", "expect": "Borrow is not available on this page"}])
    _one(LOGIN + [{"call": "API.borrow_book", "args": ["BK-001"], "save_as": "r"},
                  {"call": "teardown", "args": [{"ref": "r.loan_id"}]},
                  {"call": "Database.count_loan", "expect": 5},
                  {"call": "teardown", "args": ["LN-001"]},  # seed data is never removed
                  {"call": "Database.count_loan", "expect": 5}])


SIGNATURES = {"python": "UI.borrow_book(book_id)", "javascript": "UI.borrowBook(bookId)", "java": "boolean UI.borrowBook(Object bookId)"}


def test_the_candidate_file_is_short_documents_every_helper_and_holds_no_answers(language):
    candidate, support = RENDER[language](SPEC)
    assert "testuser@library.test / Test@123 (Test User)" in candidate and "network_down" in candidate
    assert SIGNATURES[language] in candidate and "TODO: write your automated test(s) below" in candidate
    # The Round 2 assistant returns the whole file on every turn - it must stay small.
    assert len(candidate) < 9000, len(candidate)
    # The app's rules, messages and other data live in the engine file only.
    for hidden in ("No copies available", "BK-002", "You have reached your borrowing limit"):
        assert hidden not in candidate and hidden in "".join(support.values())


# ---- one behaviour in every language: the values that most often differ between languages
CALC = {
    "app_name": "Calculator", "base_url": "https://calc.example.test", "now": "2024-02-10T10:00",
    "pages": ["Home"], "home_page": "Home",
    "entities": {"Txn": {"key": "id", "fields": {"id": "string", "amount": "money"}}},
    "data": {"Txn": [{"id": "T1", "amount": 0.1}, {"id": "T2", "amount": 0.2}, {"id": "T3", "amount": 1234.555}]},
    "actions": [{"name": "calc", "inputs": [{"name": "text"}], "returns": {
        "round_2675": {"round": 2.675}, "round_1005": {"round": [1.005, 2]}, "round_0": {"round": [2.5, 0]},
        "tenth_times_3": {"mul": [0.1, 3]}, "ten_by_4": {"div": [10, 4]}, "sum": {"sum": {"entity": "Txn", "field": "amount"}},
        "inr": {"format_money": [1234567.5, "INR"]}, "usd": {"format_money": [1234567.5, "USD"]},
        "neg": {"format_money": [-50, "INR"]}, "small": {"format_money": [999, "INR"]}, "gbp": {"format_money": [0.005, "GBP"]},
        "leap": {"format_date": ["2024-02-29", "DD-Mon-YYYY"]}, "us": {"format_date": ["2024-02-09", "MM/DD/YYYY"]},
        "month_end": {"add_months": ["2024-01-31", 1]}, "year_end": {"add_days": ["2024-12-31", 1]},
        "between": {"days_between": ["2024-01-01", "2024-03-01"]}, "today": {"today": True},
        "text": {"format": ["{a} and {b}", {"a": {"div": [10, 4]}, "b": 3}]}, "concat": {"concat": ["x", 2.5, True, None]},
        "upper": {"upper": {"input": "text"}}, "length": {"length": {"input": "text"}},
        "trim": {"trim": "  a b  "}, "ordered": {"lt": ["apple", "banana"]}, "digits": {"matches": ["١٢", "\\d+"]},
        "word": {"matches": [{"input": "text"}, "\\w+"]}, "upper_zero": {"upper": 0}, "lower_money": {"lower": 2.5}, "len_money": {"length": 2.5}, "len_none": {"length": None}, "trim_none": {"trim": None},
        "monthly_rate": {"div": [{"div": [10.5, 12]}, 100]}, "emi_factor": {"round": [{"mul": [100000, {"div": [10.5, 1200]}]}, 2]},
        "money_text": {"format": ["{m}", {"m": 1234.855}]}, "weekday": {"weekday": "2024-03-16"}, "working": {"working_days": ["2024-03-15", "2024-03-18"]},
        "time": {"time": "2024-03-15T16:05"}, "between_min": {"minutes_between": ["2024-03-15T10:00", "2024-03-17T09:30"]},
        "seats": {"split": [" A1, A2 ,,A3 ", ","]}, "dupes": {"occurrences": [{"split": ["A1,a1,B2", ","]}, "A1"]}}}],
}
CALC_EXPECT = {
    "ok": True, "round_2675": 2.68, "round_1005": 1.01, "round_0": 3, "tenth_times_3": 0.3, "ten_by_4": 2.5, "sum": 1234.855,
    "inr": "₹12,34,567.50", "usd": "$1,234,567.50", "neg": "-₹50.00", "small": "₹999.00", "gbp": "£0.01",
    "leap": "29-Feb-2024", "us": "02/09/2024", "month_end": "2024-02-29", "year_end": "2025-01-01", "between": 60, "today": "2024-02-10",
    "text": "2.50 and 3", "concat": "x2.50true", "upper": "STRASSE\U0001F600", "length": 7, "trim": "a b", "ordered": True,
    "digits": False, "word": False, "upper_zero": "0", "lower_money": "2.50", "len_money": 4, "len_none": 0, "trim_none": "", "monthly_rate": 0.00875, "emi_factor": 875, "money_text": "1234.86", "weekday": "Sat", "working": 2,
    "time": "16:05", "between_min": 2850, "seats": ["A1", "A2", "A3"], "dupes": 2,
}


def test_numbers_money_dates_and_text_are_identical_in_every_language(language):
    report = _inspect(language, CALC, [{"id": "c", "title": "c", "steps": [
        {"call": "setup"}, {"call": "API.calc", "args": ["straße\U0001F600"], "expect_includes": CALC_EXPECT}]}])
    lang = report.languages[language]
    assert lang.error is None, lang.error
    assert lang.results[0].passed, f"[{language}] {lang.results[0].failures}"
