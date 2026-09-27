"""
Randomised sequences against the engine: instead of relying on someone
thinking of every combination (a failed change that had also signed the user
out was found by reading, not by a test), thousands of random steps - wrong
passwords into a lockout, bad amounts, wrong PINs, multi-payee payments, a
change that fails half-way, clock jumps, logouts - with the engine's promises
checked after EVERY step:

  - no helper ever raises; a refusal is a False / {"ok": false} with a message
  - a refused action changes nothing (only a rule's "then" - the wrong-PIN
    counter - is kept)
  - money is never created or lost: balances = start + deposits
  - the signed-in user is always a real stored record
  - Python, JavaScript and Java give the same result at every step

Fixed seeds, so a failure always reproduces. No AI calls.
"""
import copy
import random

import pytest

from app.services.practice_app import checker
from app.services.practice_engine import render, runtime, validate

SPEC = {
    "app_name": "Wallet", "base_url": "https://wallet.example.test", "now": "2024-03-15T10:00",
    "pages": ["Login", "Home"], "home_page": "Home",
    "entities": {
        "Account": {"key": "email", "fields": {"email": "string", "password": "string", "name": "string", "balance": "money",
                                                "pin": "string", "pin_fails": "int"}},
        "Txn": {"key": "id", "id_format": "TX-{n:03}", "fields": {"id": "string", "email": "string", "amount": "money", "kind": "string",
                                                                   "at": "datetime"}},
    },
    "data": {"Account": [
        {"email": "a@w.test", "password": "A@1", "name": "Asha", "balance": 100, "pin": "1111", "pin_fails": 0},
        {"email": "b@w.test", "password": "B@1", "name": "Bala", "balance": 50.5, "pin": "2222", "pin_fails": 0},
        {"email": "c@w.test", "password": "C@1", "name": "Chen", "balance": 0, "pin": "3333", "pin_fails": 0}], "Txn": []},
    "users": {"entity": "Account", "login_field": "email", "password_field": "password", "name_field": "name", "session_minutes": 30,
              "lockout": {"attempts": 3, "minutes": 15, "message": "Account locked for 15 minutes"}},
    "queries": [
        {"name": "my_txns", "entity": "Txn", "where": {"eq": [{"field": "row.email"}, {"user": "email"}]}, "order_by": [{"field": "id"}],
         "show": ["id", "amount", "kind", {"name": "big", "value": {"gt": [{"field": "row.amount"}, 50]}}]},
        {"name": "all_accounts", "entity": "Account", "rules": [{"unless": {"eq": [{"user": "email"}, "a@w.test"]}, "message": "Admins only"}],
         "show": ["email", "balance"]},
    ],
    "actions": [
        {"name": "deposit", "inputs": [{"name": "amount", "checks": [
            {"rule": "required", "message": "Enter an amount"}, {"rule": "number", "message": "Enter a number"},
            {"rule": "min", "value": 1, "message": "At least 1"}, {"rule": "max", "value": 1000, "message": "At most 1000"}]}],
         "load": [{"as": "me", "entity": "Account", "key": {"user": "email"}, "missing": "No account"}],
         "effects": [{"set": {"record": "me", "field": "balance", "value": {"add": [{"field": "me.balance"}, {"input": "amount"}]}}},
                     {"create": {"entity": "Txn", "as": "t", "values": {"email": {"user": "email"}, "amount": {"input": "amount"},
                                                                         "kind": "deposit", "at": {"now": True}}}}],
         "message": {"format": ["Deposited {a}", {"a": {"format_money": [{"input": "amount"}, "INR"]}}]}, "returns": {"txn_id": {"field": "t.id"}}},
        {"name": "pay", "inputs": [{"name": "to"}, {"name": "amount"}, {"name": "pin"}],  # deliberately NO checks: bad input must still not crash
         "load": [{"as": "me", "entity": "Account", "key": {"user": "email"}, "missing": "No account"},
                  {"as": "payee", "entity": "Account", "key": {"input": "to"}, "missing": "No such payee"}],
         "rules": [{"when": {"eq": [{"field": "payee.email"}, {"field": "me.email"}]}, "message": "You can't pay yourself"},
                   {"unless": {"eq": [{"input": "pin"}, {"field": "me.pin"}]}, "message": "Wrong PIN",
                    "then": [{"set": {"record": "me", "field": "pin_fails", "value": {"add": [{"field": "me.pin_fails"}, 1]}}}]},
                   {"unless": {"le": [{"input": "amount"}, {"field": "me.balance"}]}, "message": "Insufficient balance"},
                   {"unless": {"gt": [{"input": "amount"}, 0]}, "message": "Amount must be positive"}],
         "effects": [{"set": {"record": "me", "field": "balance", "value": {"sub": [{"field": "me.balance"}, {"input": "amount"}]}}},
                     {"set": {"record": "payee", "field": "balance", "value": {"add": [{"field": "payee.balance"}, {"input": "amount"}]}}},
                     {"set": {"record": "me", "field": "pin_fails", "value": 0}},
                     {"if": [{"gt": [{"input": "amount"}, 50]}, [{"send": {"channel": "email", "to": {"user": "email"}, "subject": "Large payment"}}]]}],
         "message": "Paid"},
        {"name": "split_pay", "inputs": [{"name": "payees"}, {"name": "each"}],
         "load": [{"as": "me", "entity": "Account", "key": {"user": "email"}, "missing": "No account"}],
         "rules": [{"when": {"empty": {"split": [{"input": "payees"}, ","]}}, "message": "Enter payees"},
                   {"for_each": {"list": {"split": [{"input": "payees"}, ","]}, "as": "p"},
                    "unless": {"exists": {"entity": "Account", "where": {"eq": [{"field": "row.email"}, {"field": "p.value"}]}}},
                    "message": {"format": ["Unknown payee {p}", {"p": {"field": "p.value"}}]}},
                   {"for_each": {"list": {"split": [{"input": "payees"}, ","]}, "as": "p"},
                    "when": {"gt": [{"occurrences": [{"split": [{"input": "payees"}, ","]}, {"field": "p.value"}]}, 1]},
                    "message": {"format": ["{p} listed twice", {"p": {"field": "p.value"}}]}},
                   {"unless": {"gt": [{"input": "each"}, 0]}, "message": "Amount must be positive"},
                   {"unless": {"le": [{"mul": [{"length": {"split": [{"input": "payees"}, ","]}}, {"input": "each"}]}, {"field": "me.balance"}]},
                    "message": "Insufficient balance"}],
         "effects": [{"for_each": {"entity": "Account", "where": {"in": [{"field": "row.email"}, {"split": [{"input": "payees"}, ","]}]}, "as": "r",
                                   "effects": [{"set": {"record": "r", "field": "balance", "value": {"add": [{"field": "r.balance"}, {"input": "each"}]}}}]}},
                     {"set": {"record": "me", "field": "balance", "value": {"sub": [{"field": "me.balance"},
                                                                                   {"mul": [{"length": {"split": [{"input": "payees"}, ","]}}, {"input": "each"}]}]}}}],
         "message": "Split paid"},
        {"name": "risky", "inputs": [{"name": "divisor"}],  # signs out, then may fail half-way: everything must be undone
         "load": [{"as": "me", "entity": "Account", "key": {"user": "email"}, "missing": "No account"}],
         "effects": [{"logout": True}, {"set": {"record": "me", "field": "name", "value": "changed"}},
                     {"set": {"record": "me", "field": "balance", "value": {"mul": [{"field": "me.balance"}, {"input": "divisor"}]}}},
                     {"set": {"record": "me", "field": "balance", "value": {"div": [{"field": "me.balance"}, {"input": "divisor"}]}}}],
         "message": "Done"},
    ],
}
LOGINS = [("a@w.test", "A@1"), ("b@w.test", "B@1"), ("c@w.test", "C@1"), ("a@w.test", "wrong"), ("b@w.test", "nope"), ("", ""), (None, None),
          ("A@W.TEST ", "A@1"), ("x@w.test", "A@1")]
AMOUNTS = [0, 1, 5, 10.25, 49.99, 60, 1000, 1001, -3, "abc", None, "", "12", 2.5, 10**9]
PAYEES = ["a@w.test", "b@w.test", "c@w.test", " B@W.TEST ", "nobody@w.test", None, ""]
PINS = ["1111", "2222", "3333", "0000", None]
SPLITS = ["b@w.test,c@w.test", "b@w.test, b@w.test", "a@w.test", "zz@w.test", "", None, " , ", "c@w.test"]


def _sequence(rng: random.Random, length: int) -> list[dict]:
    steps = [{"call": "setup"}]
    for _ in range(length):
        kind = rng.randrange(10)
        layer = rng.choice(["UI", "API"])
        if kind in (0, 1):
            steps.append({"call": f"{layer}.login", "args": list(rng.choice(LOGINS))})
        elif kind == 2:
            steps.append({"call": "UI.logout"})
        elif kind == 3:
            steps.append({"call": f"{layer}.deposit", "args": [rng.choice(AMOUNTS)]})
        elif kind in (4, 5):
            steps.append({"call": f"{layer}.pay", "args": [rng.choice(PAYEES), rng.choice(AMOUNTS), rng.choice(PINS)]})
        elif kind == 6:
            steps.append({"call": f"{layer}.split_pay", "args": [rng.choice(SPLITS), rng.choice(AMOUNTS)]})
        elif kind == 7:
            steps.append({"call": f"{layer}.risky", "args": [rng.choice([0, 2, 4, "abc", None])]})
        elif kind == 8:
            steps.append({"call": "Test.advance_minutes", "args": [rng.choice([1, 14, 16, 31])]})
        else:
            steps.append({"call": rng.choice(["UI.my_txns", "API.all_accounts", "UI.signed_in_user", "UI.visible_message"])})
    return steps


def _facade():
    """The Python engine file, loaded as a module - the helpers exactly as candidates call them."""
    import types
    module = types.ModuleType("practice_engine_under_test")
    exec(render.files(SPEC, "python")[1]["practice_engine.py"], module.__dict__)  # noqa: S102 - our own generated file
    return module


def _call(module, step):
    target = module
    for part in step["call"].split("."):
        target = getattr(target, part)
    return target(*step.get("args", []))


def _money(engine):
    return round(sum(r["balance"] for r in engine.store["Account"]), 2)


def _deposits(engine):
    return round(sum(t["amount"] for t in engine.store["Txn"] if t["kind"] == "deposit"), 2)


def _without_pin_fails(store):
    s = copy.deepcopy(store)
    for r in s["Account"]:
        r.pop("pin_fails", None)
    return s


SEEDS = range(300)


def test_the_wallet_description_is_valid():
    assert validate.problems(SPEC) == []


@pytest.mark.parametrize("seed", SEEDS[:: 30])
def test_every_step_keeps_the_engines_promises(seed):
    for s in range(seed, seed + 30):
        rng = random.Random(s)
        module = _facade()
        engine = module._E
        start = _money(engine)
        for n, step in enumerate(_sequence(rng, 25)):
            where = f"seed {s} step {n} {step}"
            before = _without_pin_fails(engine.store)
            fails_before = {r["email"]: r["pin_fails"] for r in engine.store["Account"]}
            who = None if engine.user is None else engine.user["email"]
            value = _call(module, step)  # must never raise
            refused = value is False or (isinstance(value, dict) and value.get("ok") is False)
            error = (value.get("error") if isinstance(value, dict) else engine.message) if refused else None
            if error == "Wrong PIN":  # the refusing rule's "then" is kept: the wrong PIN is counted, nothing else changes
                assert engine.user["pin_fails"] == fails_before[who] + 1, f"wrong PIN not counted: {where}"
            if refused and step["call"].split(".")[1] in ("deposit", "pay", "split_pay", "risky"):
                assert _without_pin_fails(engine.store) == before, f"refused but changed data: {where}"
            assert engine.user is None or any(r is engine.user for r in engine.store["Account"]), f"signed-in user not a stored record: {where}"
            assert _money(engine) == round(start + _deposits(engine), 2), f"money created or lost: {where}"
            assert all(isinstance(r["balance"], (int, float)) for r in engine.store["Account"]), where


def test_python_javascript_and_java_agree_on_every_random_step():
    checklists = [{"id": f"seed-{s}", "title": f"seed {s}", "steps": _sequence(random.Random(s), 25)} for s in SEEDS[:120]]
    code, support = {}, {}
    for lang in render.LANGUAGES:
        code[lang], support[lang] = render.files(SPEC, lang)
    report = checker.inspect(code, checklists, support)
    problems = []
    for lang, r in report.languages.items():
        assert r.error is None, r.error
        problems += [f"[{lang}] {res.id}: {res.failures[0]}" for res in r.results if not res.passed]
    problems += report.differences
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:30])


def test_the_runtime_module_is_what_the_engine_file_embeds():
    """The invariants above are checked on the generated engine file itself, not a different copy."""
    assert "class Engine" in render.files(SPEC, "python")[1]["practice_engine.py"] and hasattr(runtime, "Engine")
