"""
The reference panel candidates see (practice_engine.reference): everything a
tester would be given on a real project, built by code from the description -
and never the app's rules or the messages it shows. No AI calls.
"""
import json
from pathlib import Path

import pytest

from app.services.practice_engine import reference

FIXTURES = Path(__file__).parent / "fixtures" / "practice_engine"
SPECS = {"library": FIXTURES / "library_spec.json", **{p.stem.replace("_spec", ""): p for p in (FIXTURES / "breakers").glob("*_spec.json")}}


def _messages(spec):
    """Every message the app can show - none may appear in the panel."""
    out = set()
    users = spec.get("users") or {}
    out.update((users.get("messages") or {}).values())
    if users.get("lockout"):
        out.add(users["lockout"]["message"])

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("message", "missing", "none_message", "too_long") and isinstance(v, str):
                    out.add(v)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(spec.get("queries"))
    walk(spec.get("actions"))
    walk(spec.get("faults"))
    return {m for m in out if len(m) > 12}


@pytest.mark.parametrize("name", sorted(SPECS))
def test_the_panel_shows_what_a_tester_needs_and_no_rule_or_message(name):
    spec = json.loads(SPECS[name].read_text())
    panel = reference.reference_panel(spec)
    text = json.dumps(panel, ensure_ascii=False)
    leaked = [m for m in _messages(spec) if m in text]
    assert not leaked, leaked
    assert "unless" not in text and '"when"' not in text  # no rule logic
    assert {t["table"] for t in panel["database"]} == {e.lower() for e in spec["entities"]} or len(panel["database"]) == len(spec["entities"])
    assert len(panel["pages"]) == len(spec["pages"]) and all(p["source"].startswith("<!doctype html>") for p in panel["pages"])
    assert [c["name"] for c in panel["connect"]] == ["PRACTICE_APP_URL", "PRACTICE_API_URL", "PRACTICE_DB", "SELENIUM_GRID_URL"]


def test_library_panel_details():
    spec = json.loads(SPECS["library"].read_text())
    panel = reference.reference_panel(spec)
    borrow = next(a for a in panel["api"] if a["path"] == "/api/borrow-book")
    assert borrow == {"method": "POST", "path": "/api/borrow-book", "body": ["book_id"], "query": [], "success": 201,
                      "returns": ["message", "loan_id", "due_on"], "sign_in": True}
    search = next(a for a in panel["api"] if a["path"] == "/api/search-books")
    assert search["method"] == "GET" and search["query"] == ["term"] and "available_copies" in search["fields"]
    book = next(t for t in panel["database"] if t["table"] == "book")
    assert book["key"] == "id" and {"name": "available_copies", "type": "INTEGER"} in book["columns"] and len(book["rows"]) == 6
    assert panel["accounts"][0] == {"login": "testuser@library.test", "password": "Test@123", "name": "Test User"}
    details = next(p for p in panel["pages"] if p["name"] == "Book Details")["source"]
    assert 'id="borrow-book"' in details and 'id="signed-in-user"' in details and 'id="message" role="alert"></p>' in details
    login = next(p for p in panel["pages"] if p["name"] == "Login")["source"]
    assert 'id="email"' in login and "signed-in-user" not in login
    assert panel["failures"] == ["network_down"]
