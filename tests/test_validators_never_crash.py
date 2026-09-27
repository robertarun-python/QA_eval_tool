"""
The description and checklist checkers get whatever the AI replies. They must
report problems, never crash: a dict where a name was expected crashed two
paid builds (2026-09-27). Thousands of random corruptions of real
descriptions and checklists, fixed seeds. No AI calls.
"""
import copy
import json
import random
from pathlib import Path

from app.services.practice_app import engine_build, generator
from app.services.practice_engine import validate

FIX = Path(__file__).parent / "fixtures" / "practice_engine"
SPECS = [json.loads(p.read_text()) for p in [FIX / "library_spec.json", *sorted((FIX / "breakers").glob("*_spec.json"))]]
ODD = [None, 0, -1, 1.5, True, "", "x", [], [1, "a"], {}, {"a": 1}, {"eq": [1]}, [[{}]], "a.b.c", {"ref": 5}]


def _nodes(value, path=()):
    yield path
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _nodes(v, path + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _nodes(v, path + (i,))


def _corrupt(value, rng):
    value = copy.deepcopy(value)
    for _ in range(rng.randint(1, 3)):
        paths = [p for p in _nodes(value) if p]
        path = rng.choice(paths)
        parent = value
        for key in path[:-1]:
            parent = parent[key]
        if isinstance(parent, dict) and rng.random() < 0.2:
            parent.pop(path[-1], None)
        else:
            parent[path[-1]] = copy.deepcopy(rng.choice(ODD))
    return value


def test_the_description_checker_never_crashes():
    rng = random.Random(7)
    for n in range(1500):
        spec = _corrupt(rng.choice(SPECS), rng)
        found = validate.problems(spec)
        assert isinstance(found, list), n
    assert validate.problems(SPECS[0]) == []


def test_the_checklist_checker_never_crashes():
    rng = random.Random(11)
    plan = engine_build.plan_view(SPECS[0])
    base = [{"id": "a", "title": "a", "steps": [{"call": "setup"}, {"call": "Database.count_loan", "save_as": "n"},
                                                {"check": {"ref": "n"}, "expect": 5}, {"call": "UI.login", "args": ["x", "y"], "expect": True}]}]
    for n in range(1500):
        checklists = _corrupt(base, rng)
        runnable, unsupported, problems = generator.validate_checklists(plan, checklists, [])
        assert isinstance(problems, list), n


def test_merging_a_retry_and_accepting_repairs_never_crash():
    rng = random.Random(13)
    plan = engine_build.plan_view(SPECS[0])
    base = [{"id": "a", "title": "a", "steps": [{"call": "setup"}, {"call": "Database.count_loan", "expect": 5}]},
            {"id": "b", "title": "b", "steps": [{"call": "setup"}]}]
    for n in range(800):
        first, retried = _corrupt(base, rng), _corrupt(base, rng)
        merged = generator.merge_checklist_retry(first if isinstance(first, list) else [], [], retried)
        assert isinstance(merged, list), n
        valid = [c for c in base]
        generator.accept_repairs(plan, valid, retried, {"a", "b"})
