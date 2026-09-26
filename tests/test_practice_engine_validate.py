"""
The scenario-description checker (practice_engine.validate): a valid
description passes clean, and each kind of mistake the AI can make comes back
as one plain sentence naming where it is. No AI calls.
"""
import copy
import json
from pathlib import Path

import pytest

from app.services.practice_engine import validate

SPEC = json.loads((Path(__file__).parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())


def _action(spec, name):
    return next(a for a in spec["actions"] if a["name"] == name)


def _query(spec, name):
    return next(q for q in spec["queries"] if q["name"] == name)


def test_library_description_is_valid():
    assert validate.problems(SPEC) == []


def _set(path, value):
    def change(spec):
        target = spec
        for p in path[:-1]:
            target = target[p]
        target[path[-1]] = value
    return change


BROKEN = {
    "unknown top-level key": (lambda s: s.update(colour="red"), "unknown key 'colour'"),
    "field type": (lambda s: s["entities"]["Book"]["fields"].update(pages="integer"), "has type 'integer'"),
    "seed data type": (lambda s: s["data"]["Book"][0].update(available_copies="3"), "available_copies = '3' is not a valid int"),
    "seed date": (lambda s: s["data"]["Loan"].append({"id": "LN-999", "due_on": "2024-02-30"}) if "Loan" in s["data"] else s["data"].update(Loan=[{"id": "LN-999", "due_on": "2024-02-30"}]),
                  "due_on = '2024-02-30' is not a valid date"),
    "duplicate key": (lambda s: s["data"]["Book"].append(dict(s["data"]["Book"][0])), "is used twice"),
    "created id clash": (lambda s: s["data"].setdefault("Loan", []).append({"id": "LN-100", "status": "Active"}), "clashes with an id the app will create"),
    "undeclared seed field": (lambda s: s["data"]["Book"][0].update(colour="red"), "field 'colour' is not declared"),
    "unknown page": (lambda s: _action(s, "borrow_book").update(page="Checkout"), "page 'Checkout' is not one of the pages"),
    "unknown field in rule": (lambda s: _action(s, "borrow_book")["rules"].append({"when": {"eq": [{"field": "book.colour"}, "red"]}, "message": "x"}),
                              "'colour' is not a field of Book"),
    "unknown alias": (lambda s: _action(s, "borrow_book")["rules"].append({"when": {"eq": [{"field": "loan2.id"}, 1]}, "message": "x"}),
                      "no record called 'loan2'"),
    "undeclared input": (lambda s: _action(s, "borrow_book")["rules"].append({"when": {"empty": {"input": "isbn"}}, "message": "x"}),
                         "input 'isbn' is not one of this action's inputs"),
    "unknown operator": (lambda s: _action(s, "borrow_book")["rules"].append({"when": {"between": [1, 2]}, "message": "x"}), "'between' is not a condition"),
    "bad regex": (lambda s: _action(s, "borrow_book")["inputs"][0].setdefault("checks", []).append({"rule": "pattern", "value": "(?<=a)b", "message": "x"}),
                  "look-behind"),
    "check without message": (lambda s: _action(s, "borrow_book")["inputs"][0].setdefault("checks", []).append({"rule": "required"}),
                              "needs a message"),
    "old-style input check": (lambda s: _action(s, "borrow_book")["inputs"][0].update(required="Book is required"), "unknown key 'required'"),
    "reserved input name": (lambda s: _action(s, "borrow_book")["inputs"].append({"name": "class"}), "reserved word"),
    "helper name clash": (lambda s: _action(s, "borrow_book").update(name="login"), "built-in helpers"),
    "two helpers same name": (lambda s: _query(s, "my_loans").update(name="return_book"), "clashes with"),
    "format placeholder": (lambda s: _action(s, "borrow_book").update(message={"format": ["Due {due}", {}]}), "placeholder {due} has no value"),
    "date format": (lambda s: _action(s, "borrow_book").update(message={"format_date": [{"today": True}, "dd/mm/yy"]}), "format_date needs"),
    "set unknown record": (lambda s: _action(s, "borrow_book")["effects"].append({"set": {"record": "member", "field": "status", "value": "x"}}),
                           "set needs a stored record loaded earlier"),
    "fault on unknown action": (lambda s: s["faults"][0].update(applies_to=["pay_bill"]), "applies_to 'pay_bill'"),
    "users field": (lambda s: s["users"].update(login_field="username"), "login_field 'username'"),
    "query show field": (lambda s: _query(s, "search_books").update(show=["id", "colour"]), "show field 'colour'"),
    "entity name": (lambda s: s["entities"].update({"loan item": {"key": "id", "fields": {"id": "string"}}}), "capital letter"),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_each_mistake_is_named(case):
    change, expected = BROKEN[case]
    spec = copy.deepcopy(SPEC)
    change(spec)
    found = validate.problems(spec)
    assert any(expected in p for p in found), found


@pytest.mark.parametrize("pattern", ["(?<=a)b", "(?P<x>a)", "(?i)abc", "(a)\\1", "a{,3}", "[a-z&&[^b]]", "[[a]", "\\p{L}+", "("])
def test_patterns_that_differ_between_languages_are_refused(pattern):
    assert validate.regex_problem(pattern)


@pytest.mark.parametrize("pattern", ["[0-9]{9,18}", "[A-Z]{4}0[A-Z0-9]{6}", "\\d{3}-\\d{4}", "(yes|no)", "[^@\\s]+@[^@\\s]+\\.[a-z]{2,}", "\\w+ ?\\w*"])
def test_ordinary_patterns_are_accepted(pattern):
    assert validate.regex_problem(pattern) is None


def test_not_an_object():
    assert validate.problems([]) == ["The description must be a JSON object."]
