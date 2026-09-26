"""
The hardest "breaker" scenarios (tests/stress/breaker_scenarios.json),
described by hand in the engine format, with a checklist for every
automatable Round 1 test case - run in Python, JavaScript and Java. Proves the
format and engine can express them; whether the AI writes such descriptions
is measured separately (paid, with the owner's approval). No AI calls.
"""
import json
from pathlib import Path


from app.services.practice_app import checker
from app.services.practice_engine import render, validate

HERE = Path(__file__).parent / "fixtures" / "practice_engine" / "breakers"


def _spec(name):
    return json.loads((HERE / f"{name}_spec.json").read_text())


def _run(spec, checklists):
    assert validate.problems(spec) == []
    code, support = {}, {}
    for lang in render.LANGUAGES:
        code[lang], support[lang] = render.files(spec, lang)
    report = checker.inspect(code, checklists, support)
    problems = []
    for lang, r in report.languages.items():
        assert r.error is None, r.error
        problems += [f"[{lang}] {res.title}: {res.failures[0]}" for res in r.results if not res.passed]
    problems += report.differences
    assert not problems, "\n".join(problems[:30])


def _c(title, steps):
    return {"id": title[:40], "title": title, "steps": steps}


# ---------------------------------------------------------------------------- UPI
ASHA = [{"call": "setup"}, {"call": "UI.login", "args": ["9876543210", "Pay@123"], "expect": True}, {"call": "UI.open", "args": ["Send Money"], "expect": True}]
RAVI = [{"call": "setup"}, {"call": "UI.login", "args": ["9876500000", "Rich@123"], "expect": True}, {"call": "UI.open", "args": ["Send Money"], "expect": True}]


def _pay(upi, amount, pin, ok):
    return {"call": "UI.send_money", "args": [upi, amount, pin], "expect": ok}


def _msg(text):
    return {"call": "UI.visible_message", "expect": text}


def _balance(mobile, value):
    return {"call": "Database.get_account", "args": [mobile], "expect_includes": {"balance": value}}


UPI = [
    _c("Send money with valid UPI ID and PIN", ASHA + [_pay("ravi@okbank", 500, "1234", True),
       _msg("Payment of ₹500.00 to ravi@okbank successful"), _balance("9876543210", 9500),
       {"call": "Database.outbox", "expect": [{"channel": "sms", "to": "9876543210", "body": "₹500.00 sent to ravi@okbank. Ref TX-0001"}]},
       {"call": "UI.open", "args": ["History"]}, {"call": "UI.history", "expect": [{"id": "TX-0001", "to_upi": "ravi@okbank", "amount": 500, "status": "Success"}]}]),
    _c("Amount above per-transaction limit", RAVI + [_pay("asha@okbank", 50001, "4321", False), _msg("Maximum Rs 50,000 per transaction"),
       _balance("9876500000", 200000), {"call": "Database.count_txn", "expect": 0}]),
    _c("Amount exactly at per-transaction limit", RAVI + [_pay("asha@okbank", 50000, "4321", True), _balance("9876500000", 150000)]),
    _c("Zero or negative amount", ASHA + [_pay("ravi@okbank", 0, "1234", False), _msg("Enter an amount of at least Rs 1"),
       _pay("ravi@okbank", -10, "1234", False), _msg("Enter an amount of at least Rs 1"), _balance("9876543210", 10000)]),
    _c("Amount with more than 2 decimals", ASHA + [_pay("ravi@okbank", 10.555, "1234", False), _msg("Amount can have at most 2 decimal places"),
       _pay("ravi@okbank", "10.55", "1234", True), _balance("9876543210", 9989.45)]),
    _c("Invalid UPI ID format", ASHA + [_pay("ravi.okbank", 100, "1234", False), _msg("Enter a valid UPI ID"), {"call": "Database.count_txn", "expect": 0}]),
    _c("Insufficient balance", ASHA + [_pay("ravi@okbank", 10001, "1234", False), _msg("Insufficient balance"), _balance("9876543210", 10000)]),
    _c("Wrong PIN three times blocks UPI", ASHA + [_pay("ravi@okbank", 100, "0000", False), _msg("Incorrect UPI PIN"),
       _pay("ravi@okbank", 100, "0000", False), _msg("Incorrect UPI PIN"),
       _pay("ravi@okbank", 100, "0000", False), _msg("UPI is blocked for 24 hours after 3 wrong PINs"),
       _pay("ravi@okbank", 100, "1234", False), _msg("UPI is blocked for 24 hours after 3 wrong PINs"),
       _balance("9876543210", 10000), {"call": "Database.count_txn", "expect": 0}]),
    _c("UPI unblocks after 24 hours", ASHA + [_pay("ravi@okbank", 100, "0000", False)] * 3 + [
       {"call": "Test.advance_minutes", "args": [1441]}, {"call": "UI.login", "args": ["9876543210", "Pay@123"], "expect": True},
       {"call": "UI.open", "args": ["Send Money"]}, _pay("ravi@okbank", 100, "1234", True), _balance("9876543210", 9900)]),
    _c("Daily limit across several payments", RAVI + [_pay("asha@okbank", 50000, "4321", True), _pay("asha@okbank", 50000, "4321", True),
       _pay("asha@okbank", 1, "4321", False), _msg("Daily limit of Rs 1,00,000 reached"), _balance("9876500000", 100000),
       {"call": "Test.advance_minutes", "args": [1440]}, {"call": "UI.login", "args": ["9876500000", "Rich@123"]},
       {"call": "UI.open", "args": ["Send Money"]}, _pay("asha@okbank", 1, "4321", True)]),
    _c("Bank server down", ASHA + [{"call": "Test.simulate", "args": ["bank_down"]}, _pay("ravi@okbank", 100, "1234", False),
       _msg("Bank server not responding, please try later"), _balance("9876543210", 10000), {"call": "Database.outbox", "expect": []}]),
]


def test_upi_payments():
    _run(_spec("upi"), UPI)
