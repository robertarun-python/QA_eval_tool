"""
Every API endpoint against the inputs real users and attackers send - not
just the ones the page sends. Offline: fake AI mode, in-memory database.

For each route:
  - no login           -> refused (401), except the public ones;
  - the wrong role     -> refused (403);
  - ids that don't exist or aren't numbers -> a clean 4xx;
  - junk bodies (empty, wrong types, huge, odd characters) -> a clean 4xx.
Never a 500: a crash is a bug whatever the input.
"""
import json

import pytest
from fastapi.routing import APIRoute

from app.main import app

from .conftest import (
    CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, HR_EMAIL, HR_PASSWORD,
    _auth, _login, _publish_round4_scenario, _publish_scenario,
)

PUBLIC = {("POST", "/auth/login"), ("GET", "/health"), ("GET", "/"), ("POST", "/auth/logout"), ("GET", "/auth/me")}
ROUTES = sorted(
    ((m, r) for r in app.routes if isinstance(r, APIRoute) for m in r.methods if m not in ("HEAD", "OPTIONS")),
    key=lambda x: (x[1].path, x[0]),
)
BAD_IDS = ["999999", "-1", "abc", "1.5", "9" * 30]
HUGE = "x" * 200_000
ODD = "Ωmega \u202e rtl \x00 nul 😀 <script>alert(1)</script> ' OR 1=1 --"


def _fill(path: str, value: str) -> str:
    out, i = "", 0
    while i < len(path):
        if path[i] == "{":
            j = path.index("}", i)
            out += value
            i = j + 1
        else:
            out += path[i]
            i += 1
    return out


def _bodies(route: APIRoute):
    field = route.body_field
    model = getattr(field, "type_", None) or (field.field_info.annotation if field else None)
    names = list(getattr(model, "model_fields", {}) or {})
    yield "empty", {}
    yield "list", []
    yield "null", None
    yield "string", "hello"
    for name in names[:6]:
        for label, value in (("int", 12345), ("list", [1, 2]), ("dict", {"a": 1}), ("null", None),
                             ("huge", HUGE), ("odd", ODD), ("negative", -5), ("bool", True)):
            yield f"{name}={label}", {name: value}


def _request(client, method, url, token=None, body=...):
    client.cookies.clear()  # only the token passed here - never a login left in the client's jar
    kwargs = {"cookies": _auth(token)} if token else {}
    if body is not ...:
        kwargs["content"] = json.dumps(body)
        kwargs["headers"] = {"content-type": "application/json"}
    return client.request(method, url, **kwargs)


@pytest.fixture()
def world(client, monkeypatch):
    """HR, a candidate part-way through, and live scenarios for every round."""
    from app.config import settings
    monkeypatch.setattr(settings, "llm_fake_mode", True)
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    for n in (1, 3, 4):
        _publish_scenario(client, hr, monkeypatch, round_number=n, title=f"Round {n} scenario")
    _publish_round4_scenario(client, hr, monkeypatch)
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand))
    # A crash must come back as the 500 a user would get, not stop the sweep.
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False), hr, cand


def test_every_route_refuses_the_wrong_caller(world):
    client, hr, cand = world
    problems = []
    for method, route in ROUTES:
        if (method, route.path) in PUBLIC:
            continue
        url = _fill(route.path, "1")
        res = _request(client, method, url)
        if res.status_code not in (401, 403):
            problems.append(f"no login: {method} {route.path} -> {res.status_code}")
        wrong = cand if route.path.startswith("/hr") else hr if route.path.startswith("/candidate") else None
        if wrong:
            res = _request(client, method, url, wrong, {} if method in ("POST", "PUT", "PATCH") else ...)
            if res.status_code != 403:
                problems.append(f"wrong role: {method} {route.path} -> {res.status_code}")
    assert not problems, "\n".join(problems)


def test_no_route_crashes_on_bad_ids(world):
    client, hr, cand = world
    problems = []
    for method, route in ROUTES:
        if "{" not in route.path:
            continue
        token = hr if route.path.startswith("/hr") else cand
        for bad in BAD_IDS:
            res = _request(client, method, _fill(route.path, bad), token, {} if method in ("POST", "PUT", "PATCH") else ...)
            if res.status_code >= 500:
                problems.append(f"{method} {route.path} id={bad} -> {res.status_code}")
    assert not problems, "\n".join(problems)


def test_no_route_crashes_on_junk_bodies(world):
    client, hr, cand = world
    problems = []
    for method, route in ROUTES:
        if method not in ("POST", "PUT", "PATCH") or route.body_field is None:
            continue
        token = hr if route.path.startswith("/hr") else cand if route.path.startswith("/candidate") else None
        for label, body in _bodies(route):
            res = _request(client, method, _fill(route.path, "1"), token, body)
            if res.status_code >= 500:
                problems.append(f"{method} {route.path} body {label} -> {res.status_code}: {res.text[:120]}")
    assert not problems, "\n".join(problems)
