"""
Contract between the page (static/app.js) and the API (FastAPI routes): every
request the page makes must hit a route that exists, with a method it
accepts, and - when the body is written inline - send only fields that
route's schema knows (FastAPI silently DROPS unknown fields, so a renamed or
misspelled field never errors; the value just vanishes) and every required
one.

That drift is how Run B's Round 2 submit broke: the client sent a body the
endpoint didn't accept for more than one test case. Nothing checked the two
sides against each other until this file. Static - it reads app.js and the
app's route table; no server, no browser.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest
from fastapi.routing import APIRoute

from app.main import app

APP_JS = (Path(__file__).parent.parent / "backend" / "app" / "static" / "app.js").read_text()


def _matching(text: str, start: int, open_ch: str, close_ch: str) -> int:
    """Index just past the bracket that closes the one at `start`, skipping
    string and template literals."""
    depth, i, quote = 0, start, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError(f"unbalanced {open_ch} at {start}")


def _top_level_keys(obj_literal: str) -> tuple[set[str], bool]:
    """Keys of a JS object literal's top level; the bool is True when a
    spread (...x) makes the key set unknowable."""
    inner, parts, depth, cur, quote = obj_literal.strip()[1:-1], [], 0, "", None
    for ch in inner:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'`":
            quote = ch
        elif ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    parts.append(cur)
    keys, spread = set(), False
    for part in (p.strip() for p in parts if p.strip()):
        if part.startswith("..."):
            spread = True
            continue
        m = re.match(r"""["']?([A-Za-z_$][\w$]*)["']?\s*(:|$)""", part)
        if m:
            keys.add(m.group(1))
    return keys, spread


def _page_calls(js: str = APP_JS):
    """(line, path_pattern, method, body_keys | None, spread) for every api(...) call."""
    APP_JS = js  # noqa: N806 - the parser below reads this name
    calls = []
    for m in re.finditer(r"\bapi\(\s*", APP_JS):
        start = m.end()
        quote = APP_JS[start]
        if quote not in "\"'`":
            continue  # api(variable) - path not static
        end = APP_JS.index(quote, start + 1)
        raw_path = APP_JS[start + 1:end]
        path = re.sub(r"\$\{[^}]*\}", "{x}", raw_path).split("?")[0]
        rest = APP_JS[end + 1:end + 1 + 4000]
        method, keys, spread = "GET", None, False
        opts = re.match(r"\s*,\s*\{", rest)
        if opts:
            obj_start = end + 1 + opts.end() - 1
            options = APP_JS[obj_start:_matching(APP_JS, obj_start, "{", "}")]
            mm = re.search(r"""method:\s*["'](\w+)["']""", options)
            method = mm.group(1).upper() if mm else "GET"
            bm = re.search(r"JSON\.stringify\(\s*\{", options)
            if bm:
                body_start = bm.end() - 1
                keys, spread = _top_level_keys(options[body_start:_matching(options, body_start, "{", "}")])
        calls.append((APP_JS.count("\n", 0, m.start()) + 1, path, method, keys, spread))
    return calls


ROUTES = [r for r in app.routes if isinstance(r, APIRoute)]


def _route_for(path: str, method: str):
    for route in ROUTES:
        pattern = "^" + re.sub(r"\{[^}]+\}", "[^/]+", route.path) + "$"
        candidate = path.replace("{x}", "__x__")
        if re.match(pattern, candidate) and method in route.methods:
            return route
    return None


def _body_model(route):
    field = route.body_field
    if field is None:
        return None
    annotation = getattr(field, "type_", None) or field.field_info.annotation
    return annotation if hasattr(annotation, "model_fields") else None


CALLS = _page_calls()


def test_the_page_makes_the_expected_number_of_calls():
    # A parser that silently found nothing would make every other test here pass.
    assert len(CALLS) >= 100


@pytest.mark.parametrize("line,path,method,keys,spread", CALLS, ids=[f"app.js:{c[0]} {c[2]} {c[1]}" for c in CALLS])
def test_page_request_matches_an_api_route(line, path, method, keys, spread):
    route = _route_for(path, method)
    assert route is not None, f"app.js:{line} calls {method} {path}, which the API doesn't have"
    model = _body_model(route)
    if keys is None or model is None:
        return
    fields = model.model_fields
    unknown = keys - set(fields) - {a.alias for a in fields.values() if a.alias}
    if model.model_config.get("extra") != "allow":
        assert not unknown, f"app.js:{line} sends {sorted(unknown)} to {method} {route.path}, which ignores them (fields: {sorted(fields)})"
    if not spread:
        missing = {name for name, f in fields.items() if f.is_required()} - keys
        assert not missing, f"app.js:{line} doesn't send required {sorted(missing)} to {method} {route.path}"


def _problems(js: str) -> list[str]:
    out = []
    for line, path, method, keys, spread in _page_calls(js):
        try:
            test_page_request_matches_an_api_route(line, path, method, keys, spread)
        except AssertionError as e:
            out.append(str(e))
    return out


@pytest.mark.parametrize("snippet,expected", [
    ('api("/candidate/round/9/nowhere", { method: "POST" })', "doesn't have"),
    ('api("/hr/settings", { method: "DELETE" })', "doesn't have"),
    ('api("/candidate/round/3/turn", { method: "POST", body: JSON.stringify({ candidate_prompt: p, candidate_promt: p }) })', "ignores them"),
    ('api("/candidate/round/3/turn", { method: "POST", body: JSON.stringify({}) })', "required"),
    ('api(`/candidate/round/2/auto/submit`, { method: "POST", body: JSON.stringify({ entry: e }) })', "ignores them"),
])
def test_the_checker_catches_drift(snippet, expected):
    """Guards against a checker that passes by finding nothing."""
    problems = _problems(snippet)
    assert problems and expected in problems[0], problems


def test_inline_bodies_are_actually_checked():
    checked = sum(1 for _, path, method, keys, _ in CALLS if keys is not None and _body_model(_route_for(path, method) or ROUTES[0]))
    assert checked >= 20, checked
