"""
The patterns behind the ~20 failures the paid runs found, each turned into a
generator run over EVERY real description in the library - not only the
scenario where it first showed up. No AI calls.

A  the AI reaches for a word the format lacks ("let")    -> the words it will
   reach for next have a clear answer (tests/test_practice_engine.py has
   them working in all three languages)
B  right meaning, other shape ("" for empty, a dict for a name)   -> corrupt
   real descriptions; the checker never crashes, and what it accepts runs the
   same in Python, JavaScript and Java
C  empty or odd values at run time (an unset date crashed sign-in)   -> every
   action and lookup of every real description, called with blank, spaces,
   zero, huge, tiny, negative zero, quotes, emoji, lists...; never a crash,
   never "something went wrong", the same answer in all three languages
F  the assistant's guard misread normal language (apostrophes, wait limits)
   -> Java and JavaScript versions of the approved programs (the paid check
   was Python only), 30 faithful replies in varied styles, and the leak styles
   a helpful model uses

Found and fixed by these on first run (2026-09-27): a huge number or -0 typed
as input gave different messages in Python/JavaScript/Java (and crashed
Python); "checks": "" and a load without a key ran differently in Java; the
guard blocked a Java JSON body and require("selenium-webdriver"); a secret
inside a JSON text in the environment was never matched as a leak.
"""
import copy
import json
import random
from pathlib import Path

import pytest

from app.services import round2_automation_policy as policy
from app.services import round2_typist
from app.services.practice_app import checker
from app.services.practice_engine import render, runtime, validate

HERE = Path(__file__).parent / "fixtures" / "practice_engine"
BUILDS = sorted((HERE / "real_outputs" / "builds").glob("*.json"))
IMAGINED = HERE / "imagined"
OTHER_LANGUAGES = ("javascript", "java")


def _spec(path):
    return json.loads(path.read_text())["spec"]


# ------------------------------------------------------------------ pattern A


def test_every_word_the_ai_may_reach_for_gets_a_usable_answer():
    """The real AI wrote "let" and got only "unknown operator" - a paid repair
    round. Each hint must name something that exists."""
    known = validate.EXPR_OPS | validate.COND_OPS | {"compute", "id_format", "fixed"}
    for word, hint in validate.OP_HINTS.items():
        named = {w.strip('{}":,[]()') for w in hint.replace("/", " ").split()}
        assert named & known, f"{word!r}: the hint {hint!r} names no real operator"
        spec = {"app_name": "A", "base_url": "https://a.example.test", "now": "2024-01-01T10:00", "pages": ["Home"], "home_page": "Home",
                "entities": {"Note": {"key": "id", "fields": {"id": "string"}}}, "data": {"Note": []}, "actions": [{"name": "go", "effects": [], "message": {word: [1, 2]}}]}
        assert f"use {hint}" in " ".join(validate.problems(spec))


def test_an_unknown_word_without_a_hint_lists_the_real_ones():
    spec = {"app_name": "A", "base_url": "https://a.example.test", "now": "2024-01-01T10:00", "pages": ["Home"], "home_page": "Home",
            "entities": {"Note": {"key": "id", "fields": {"id": "string"}}}, "data": {"Note": []}, "actions": [{"name": "go", "effects": [], "message": {"frobnicate": 1}}]}
    assert "unknown operator 'frobnicate'; the operators are: abs, add, " in " ".join(validate.problems(spec))


# ------------------------------------------------------------------ pattern B

SWAPS = ["", None, [], {}, "x", 0, -1, True, 1.5, {"input": "nope"}, {"field": "nope.x"}, "2024-02-30", [None]]


def _paths(node, at=()):
    yield at
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _paths(v, at + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _paths(v, at + (i,))


def _corrupt(spec, rng):
    """One change of the kinds the real AI made: a value swapped for "" / null /
    another type, a key dropped, a value wrapped in a list, a key's case
    changed, a number written as text."""
    s = copy.deepcopy(spec)
    at = rng.choice([p for p in _paths(s) if p])
    parent = s
    for p in at[:-1]:
        parent = parent[p]
    key, kind = at[-1], rng.randrange(5)
    if kind == 0:
        parent[key] = rng.choice(SWAPS)
    elif kind == 1 and isinstance(parent, dict):
        del parent[key]
    elif kind == 2:
        parent[key] = [parent[key]]
    elif kind == 3 and isinstance(parent, dict) and isinstance(key, str):
        parent[key.upper()] = parent.pop(key)
    elif kind == 4 and isinstance(parent[key], (int, float)) and not isinstance(parent[key], bool):
        parent[key] = str(parent[key])
    else:
        parent[key] = ""
    return s


def _login(spec):
    users = spec.get("users")
    if not users:
        return None
    first = (spec["data"].get(users["entity"]) or [{}])[0]
    return [first.get(users.get("login_field", "email")), first.get(users.get("password_field", "password"))]


@pytest.mark.parametrize("path", BUILDS, ids=lambda p: p.stem)
def test_corrupted_real_descriptions_never_crash_the_checker_or_the_engine(path):
    spec, rng = _spec(path), random.Random(path.stem)
    for _ in range(300):
        s = _corrupt(spec, rng)
        problems = validate.problems(s)  # must never raise
        assert all(isinstance(p, str) for p in problems)
        if problems:
            continue
        engine = runtime.Engine(s)
        engine.reset()
        if _login(s):
            engine.api_login(*_login(s))
        for a in s.get("actions") or []:
            engine.api_action(a["name"], {i["name"]: "" for i in a.get("inputs") or []})
        for q in s.get("queries") or []:
            engine.api_query(q["name"], {})


# ------------------------------------------------------------------ pattern C

ODD = [None, "", "   ", "0", "-1", "abc", "9" * 30, "2024-02-30", "2024-13-01T25:61", "O'Brien\"; DROP TABLE x;--", "😀",
       0, -5, 1e20, 1e-7, 2.675, 1e300, -0.0, True, [1, 2], {"a": 1}]


def _odd_steps(spec):
    """Every action and lookup called with every odd value, expecting exactly
    what Python's engine answers - so the other languages must agree."""
    engine = runtime.Engine(spec)
    engine.reset()
    steps = [{"call": "setup"}]
    login = _login(spec)
    if login:
        steps.append({"call": "API.login", "args": login, "expect": engine.api_login(*login)})
    unexpected = []
    for kind, items in (("action", spec.get("actions") or []), ("query", spec.get("queries") or [])):
        for item in items:
            names = [i["name"] for i in item.get("inputs") or []]
            if kind == "query":
                names = list(dict.fromkeys(([item["match"]["input"]] if item.get("match") else [])
                                           + ([item["key_input"]] if item.get("key_input") else []) + names))
            for value in ODD:
                call = engine.api_action if kind == "action" else engine.api_query
                answer = call(item["name"], {n: value for n in names})  # must never raise
                if answer.get("error") == runtime.UNEXPECTED:
                    unexpected.append(f"{item['name']}({value!r})")
                steps.append({"call": f"API.{item['name']}", "args": [value] * len(names), "expect": answer})
    return steps, unexpected


def _disagreements(spec, steps):
    code, support = {}, {}
    for lang in OTHER_LANGUAGES:
        code[lang], support[lang] = render.files(spec, lang)
    report = checker.inspect(code, [{"id": "odd", "title": "odd", "steps": steps}], support)
    out = []
    for lang, r in report.languages.items():
        if r.error:
            out.append(f"[{lang}] {r.error}")
        out += [f"[{lang}] {f}" for res in r.results if not res.passed for f in res.failures[:2]]
    return out


@pytest.mark.parametrize("path", BUILDS, ids=lambda p: p.stem)
def test_odd_inputs_on_real_descriptions_answer_alike_in_every_language(path):
    spec = _spec(path)
    steps, unexpected = _odd_steps(spec)
    assert not unexpected, f"'something went wrong' for {unexpected[:5]}"
    problems = _disagreements(spec, steps)
    assert not problems, "\n".join(problems[:6])


@pytest.mark.parametrize("path", BUILDS[::5], ids=lambda p: p.stem)
def test_accepted_corrupted_descriptions_answer_alike_in_every_language(path):
    spec, rng, done = _spec(path), random.Random("langs" + path.stem), 0
    while done < 2:
        s = _corrupt(spec, rng)
        if validate.problems(s):
            continue
        done += 1
        steps, _ = _odd_steps(s)
        problems = _disagreements(s, steps)
        assert not problems, "\n".join(problems[:6])


def test_a_list_slot_given_as_text_is_sent_back():
    spec = copy.deepcopy(_spec(BUILDS[0]))
    spec["actions"][0]["inputs"] = [dict(spec["actions"][0]["inputs"][0], checks="")] + spec["actions"][0]["inputs"][1:] \
        if spec["actions"][0].get("inputs") else ""
    assert any("must be a list" in p for p in validate.problems(spec))


def test_a_load_without_a_key_is_sent_back():
    for path in BUILDS:
        spec = copy.deepcopy(_spec(path))
        action = next((a for a in spec.get("actions") or [] if a.get("load")), None)
        if action:
            del action["load"][0]["key"]
            assert any("needs as, an entity that exists, key and missing" in p for p in validate.problems(spec))
            return
    pytest.fail("no real description loads a record")


# ------------------------------------------------------------------ pattern F

ASSISTANT = json.loads((HERE / "real_outputs" / "assistant" / "r4_typist.json").read_text())["replies"]
SAID = {r["conversation"]: r["said"] for r in ASSISTANT}


@pytest.mark.parametrize("name,conversation", [("typist_ui_java", "strong UI"), ("typist_api_java", "strong API"),
                                               ("typist_ui_js", "strong UI"), ("typist_api_js", "strong API")])
def test_faithful_java_and_javascript_programs_are_not_blocked(name, conversation):
    """Imagined, not recorded: what the assistant writes for a Java or
    JavaScript candidate who said the same as the approved Python ones."""
    code = (IMAGINED / f"{name}.txt").read_text()
    assert round2_typist.unsaid(code, SAID[conversation], code=True) == []


def test_an_invented_value_inside_a_java_json_body_is_still_caught():
    code = (IMAGINED / "typist_api_java.txt").read_text().replace("Pass@123", "Admin@999")
    assert set(round2_typist.unsaid(code, SAID["strong API"], code=True)) & {"Admin@999", "Admin", "999"}


# the strong UI candidate, who also said "use my Round 1 TC-01" (so its design counts as said)
SAID_UI = SAID["strong UI"] + " " + SAID["uses own round 1"]
FAITHFUL = [
    "Got it: you'll open PRACTICE_APP_URL, type priya@library.test into id email and Pass@123 into id password, then click id login.",
    "Understood — Priya’s email goes into the element with id “email”.",
    "So after clicking login, we wait until the page title contains “Home”.",
    "Noted. Anything else for TC-01, or shall I write it up now?",
    "Okay, step 2 is typing Pass@123 into id password.",
    "You've given me 3 steps so far. Want me to generate the code?",
    "I don't know that value — I'll need it from you.",
    "How would you like that judged? Tell me in your own words.",
    "Should I write it in Java 17 style, or is plain Java fine?",
    "That's everything you described for the Home page check. Generate now?",
    "Right, a different value from before - which one do you want?",
    "Is it on a different page, or the current page?",
    "Your 2nd step is entering the password.",
    "Okay, I'll print one line per check, UTF-8 safe.",
    "Fine — I'll leave the element with id login as the click target.",
]
LEAKS = ["Should I go to the Dashboard page first?", "Is the button labelled “Log In”?", "Try the /members endpoint.",
         "Maybe it returns 401?", "I'd use PUT for that.", "The username might be admin@library.test.", "Is it the members table?",
         "Do you mean the Borrow button?", "Use the id 'signin-btn'.", "The password could be Pass@1234.",
         "Perhaps wait for the spinner to disappear?", "Is the login done via the API or via the form?"]
# Not covered: an invented message written without quotes ("Should it show Welcome back, Priya!?").


@pytest.mark.parametrize("reply", FAITHFUL)
def test_faithful_replies_in_varied_styles_are_not_blocked(reply):
    assert round2_typist.unsaid(reply, SAID_UI, code=False) == []


@pytest.mark.parametrize("reply", LEAKS)
def test_leaks_in_the_styles_a_helpful_model_uses_are_caught(reply):
    weak = next(r["said"] for r in ASSISTANT if r["conversation"] == "weak candidate" and r["turn"] == 1)
    assert round2_typist.unsaid(reply, weak, code=False)


def test_scorer_sees_whole_strings_from_java_json_bodies():
    code = (IMAGINED / "typist_api_java.txt").read_text()
    literals = policy.untraceable_literals(code, [{"title": "TC-01", "test_data": "priya@library.test / Pass@123"}])
    assert '{"email": "priya@library.test", "password": "Pass@123"}' in literals
    assert not any("contains(" in lit for lit in literals)


def test_a_secret_inside_a_json_text_in_the_environment_is_a_leak():
    env = 'String body = "{\\"password\\": \\"Secret#77\\"}";'
    assert policy.leaked_environment_values("try Secret#77", env, [{"title": "TC-01"}], [], "hi") == ["Secret#77"]
