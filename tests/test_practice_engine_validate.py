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


def test_checklist_steps_must_call_a_helper_or_check_a_saved_value():
    from app.services.practice_app import engine_build, generator
    plan = engine_build.plan_view(SPEC)
    ok = [{"id": "a", "title": "a", "steps": [{"call": "setup"}, {"call": "Database.count_loan", "save_as": "n"},
                                              {"check": {"ref": "n"}, "expect": 5}, {"call": "Database.count_loan", "expect": {"ref": "n"}}]}]
    assert generator.validate_checklists(plan, ok, [])[2] == []
    bad_shape = [{"id": "b", "title": "b", "steps": [{"call": "setup"}, {"expect": 5}]}]
    assert "either calls a helper" in generator.validate_checklists(plan, bad_shape, [])[2][0]
    unsaved = [{"id": "c", "title": "c", "steps": [{"call": "setup"}, {"call": "Database.count_loan", "expect": {"ref": "never"}}]}]
    assert "no earlier step saved 'never'" in generator.validate_checklists(plan, unsaved, [])[2][0]


def test_matches_with_a_non_literal_pattern_gets_an_actionable_message():
    spec = copy.deepcopy(SPEC)
    _action(spec, "borrow_book")["rules"].append({"when": {"matches": [{"input": "book_id"}, {"input": "book_id"}]}, "message": "x"})
    assert any("must be written out" in p for p in validate.problems(spec))


def test_query_loads_are_checked_like_action_loads():
    from tests.test_practice_engine import BILLS
    assert validate.problems(BILLS) == []
    broken = copy.deepcopy(BILLS)
    broken["queries"][0]["load"][0]["entity"] = "Customer"
    assert any("load[0]" in p for p in validate.problems(broken))


def test_only_actions_that_change_stored_records_must_be_checked_in_the_database():
    from app.services.practice_app import engine_build
    spec = copy.deepcopy(SPEC)
    spec["actions"].append({"name": "pick_slot", "inputs": [{"name": "slot"}],
                            "effects": [{"set_session": {"key": "slot", "value": {"input": "slot"}}}], "message": "Slot selected"})
    plan = engine_build.plan_view(spec)
    by_name = {(h["layer"], h["name"]): h for h in plan["helpers"]}
    assert by_name[("UI", "pick_slot")]["changes_data"] is False and by_name[("UI", "borrow_book")]["changes_data"] is True
    assert "never true/false" in by_name[("UI", "search_books")]["returns"]


def test_named_values_are_checked():
    from tests.test_practice_engine import CHECKOUT
    assert validate.problems(CHECKOUT) == []
    broken = copy.deepcopy(CHECKOUT)
    broken["actions"][0]["message"] = {"var": "grand_total"}
    assert any("not a value computed earlier" in p for p in validate.problems(broken))
    out_of_order = copy.deepcopy(CHECKOUT)
    out_of_order["actions"][0]["compute"].reverse()
    assert any("not a value computed earlier" in p for p in validate.problems(out_of_order))
