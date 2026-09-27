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


# ---------------------------------------------------------------------------- Login security and password reset
def _login(email, password, ok):
    return {"call": "UI.login", "args": [email, password], "expect": ok}


START = [{"call": "setup"}]
RESET = START + [{"call": "UI.open", "args": ["Forgot Password"], "expect": True},
                 {"call": "UI.request_reset", "args": ["asha@corp.test"], "expect": True}]
WEAK = "Password must be 8-20 characters with an upper-case letter, a digit and a special character"
SECURITY = [
    _c("Login with valid credentials", START + [_login("asha@corp.test", "Asha@2024", True), {"call": "UI.current_page", "expect": "Dashboard"},
       {"call": "UI.signed_in_user", "expect": "Asha Rao"}]),
    _c("Email is case-insensitive", START + [_login("ASHA@CORP.TEST", "Asha@2024", True)]),
    _c("Password is case-sensitive", START + [_login("asha@corp.test", "asha@2024", False), _msg("Invalid email or password"),
       {"call": "UI.signed_in_user", "expect": None}]),
    _c("Account locked after 3 wrong passwords", START + [_login("asha@corp.test", "x", False), _msg("Invalid email or password"),
       _login("asha@corp.test", "x", False), _login("asha@corp.test", "x", False), _msg("Account locked for 15 minutes"),
       _login("asha@corp.test", "Asha@2024", False), _msg("Account locked for 15 minutes"),
       _login("ben@corp.test", "Ben@2024", True)]),
    _c("Lock lifts after 15 minutes", START + [_login("asha@corp.test", "x", False)] * 3 + [
       {"call": "Test.advance_minutes", "args": [14]}, _login("asha@corp.test", "Asha@2024", False),
       {"call": "Test.advance_minutes", "args": [1]}, _login("asha@corp.test", "Asha@2024", True)]),
    _c("Wrong attempts reset after a successful login", START + [_login("asha@corp.test", "x", False), _login("asha@corp.test", "x", False),
       _login("asha@corp.test", "Asha@2024", True), {"call": "UI.logout"}, _login("asha@corp.test", "x", False),
       _login("asha@corp.test", "x", False), _login("asha@corp.test", "Asha@2024", True)]),
    _c("Reset password with valid code", RESET + [
       {"call": "Database.outbox", "expect_includes": [{"to": "asha@corp.test", "body": "Your code is 482913. It is valid for 10 minutes."}]},
       {"call": "UI.reset_password", "args": ["482913", "Newpass@1", "Newpass@1"], "expect": True}, _msg("Password changed. Please log in."),
       {"call": "UI.current_page", "expect": "Login"},
       _login("asha@corp.test", "Asha@2024", False), _login("asha@corp.test", "Newpass@1", True),
       {"call": "Database.get_user", "args": ["asha@corp.test"], "expect_includes": {"password": "Newpass@1", "prev1": "Asha@2024"}}]),
    _c("Expired reset code", RESET + [{"call": "Test.advance_minutes", "args": [11]},
       {"call": "UI.reset_password", "args": ["482913", "Newpass@1", "Newpass@1"], "expect": False}, _msg("Code has expired"),
       {"call": "Database.get_user", "args": ["asha@corp.test"], "expect_includes": {"password": "Asha@2024"}}]),
    _c("Weak new password", RESET + [{"call": "UI.reset_password", "args": ["482913", "password", "password"], "expect": False}, _msg(WEAK),
       {"call": "UI.reset_password", "args": ["482913", "Password1", "Password1"], "expect": False}, _msg(WEAK),
       {"call": "UI.reset_password", "args": ["482913", "Pa@1", "Pa@1"], "expect": False}, _msg(WEAK)]),
    _c("Reuse of a recent password", RESET + [{"call": "UI.reset_password", "args": ["482913", "Asha@2024", "Asha@2024"], "expect": False},
       _msg("You can't reuse your last 3 passwords"),
       {"call": "UI.reset_password", "args": ["482913", "Asha@2022", "Asha@2022"], "expect": False}, _msg("You can't reuse your last 3 passwords")]),
    _c("Confirm password mismatch", RESET + [{"call": "UI.reset_password", "args": ["482913", "Newpass@1", "Newpass@2"], "expect": False},
       _msg("Passwords do not match")]),
    _c("SQL injection in email field", START + [_login("' OR 1=1 --", "x", False), _msg("Invalid email or password"),
       {"call": "UI.signed_in_user", "expect": None}]),
]


def test_login_security_and_password_reset():
    _run(_spec("security"), SECURITY)


# ---------------------------------------------------------------------------- Cinema seats
def _cine(user="user1@cine.test", password="One@123"):
    return [{"call": "UI.login", "args": [user, password], "expect": True}, {"call": "UI.open", "args": ["Seat Selection"], "expect": True}]


def _select(seats, ok):
    return {"call": "UI.select_seats", "args": [seats], "expect": ok}


def _seat(code, status):
    return {"call": "Database.get_seat", "args": [code], "expect_includes": {"status": status}}


PAY = [{"call": "UI.open", "args": ["Payment"], "expect": True}]
CINEMA = [
    _c("Book two regular seats", START + _cine() + [_select("A1,A2", True), {"call": "UI.last_result", "expect": {"total": 570.8}}] + PAY + [
       {"call": "UI.pay", "expect": True}, _msg("Booked BK-001. Total ₹570.80"), _seat("A1", "Sold"), _seat("A2", "Sold"),
       {"call": "Database.get_booking", "args": ["BK-001"], "expect_includes": {"seat_count": 2, "total": 570.8}}]),
    _c("Seven seats refused", START + _cine() + [_select("A1,A2,A3,A4,A5,A6,A7", False), _msg("You can book at most 6 seats"), _seat("A1", "Free")]),
    _c("Already sold seat", START + _cine() + [_select("B5", False), _msg("Seat B5 is not available")]),
    _c("Held seat can't be taken by another user", START + _cine() + [_select("C1", True), {"call": "UI.logout"}] + _cine("user2@cine.test", "Two@123") + [
       _select("C1", False), _msg("Seat C1 is not available"),
       {"call": "Database.get_seat", "args": ["C1"], "expect_includes": {"held_by": "user1@cine.test"}}]),
    _c("Hold released after 10 minutes", START + _cine() + [_select("C1", True), {"call": "UI.logout"}, {"call": "Test.advance_minutes", "args": [11]}]
       + _cine("user2@cine.test", "Two@123") + [_select("C1", True),
       {"call": "Database.get_seat", "args": ["C1"], "expect_includes": {"status": "Held", "held_by": "user2@cine.test"}}]),
    _c("Pay after hold expired", START + _cine() + [_select("A3", True), {"call": "Test.advance_minutes", "args": [11]}] + PAY + [
       {"call": "UI.pay", "expect": False}, _msg("Your seat hold has expired"), {"call": "Database.count_booking", "expect": 0}]),
    _c("Recliner pricing", START + _cine() + [_select("R1", True)] + PAY + [{"call": "UI.pay", "expect": True}, _msg("Booked BK-001. Total ₹485.40")]),
    _c("Booking after show start", START + [{"call": "Test.advance_minutes", "args": [90]}] + _cine() + [_select("A1", False),
       _msg("Booking is closed for this show")]),
    _c("Invalid seat code", START + _cine() + [_select("Z99", False), _msg("Seat Z99 does not exist")]),
    _c("Duplicate seat in selection", START + _cine() + [_select("A1,A1", False), _msg("Seat A1 selected twice"), _seat("A1", "Free")]),
    _c("Payment gateway timeout", START + _cine() + [_select("A4", True), {"call": "Test.simulate", "args": ["gateway_timeout"]}] + PAY + [
       {"call": "UI.pay", "expect": False}, _msg("Payment failed. Please try again."), _seat("A4", "Held"), {"call": "Database.count_booking", "expect": 0}]),
]


def test_cinema_seat_booking():
    _run(_spec("cinema"), CINEMA)
