"""The screens in the reference panel read like the real app: each shows the
test user's own starting data - their loans, one loan's EMI details, their
payment history - not an empty form (owner, 2026-09-27, after Round 1 on Loan:
"account number, EMI amount missing"). Never one of the app's messages - the
Round 1 answer key. Checked on the real Loan build and on every real
description in the library. No AI calls."""
import json
import re
from pathlib import Path

import pytest

from app.services.practice_engine import reference

LIB = Path(__file__).parent / "fixtures" / "practice_engine" / "real_outputs" / "builds"
LOAN = json.loads((LIB / "r1_36.json").read_text())["spec"]


def _text(source):
    return " ".join(re.sub(r"<[^>]+>", " ", re.sub(r"<style.*?</style>", "", source, flags=re.S)).split())


def _messages(spec):
    """Every message text the description can show - rules, lookups, actions."""
    found = []

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key in ("message", "missing", "none_message", "too_long") and len(node) > 12:
            found.append(node)
    for part in ("actions", "queries", "users", "faults"):
        walk(spec.get(part))
    return found


def test_loan_screens_show_the_test_users_own_data():
    pages = {p["name"]: _text(p["source"]) for p in reference.page_sources(LOAN)}
    assert "LN-45678" in pages["Loans"] and "5250.00" in pages["Loans"]
    assert all(v in pages["EMI Details"] for v in ("LN-45678", "3800.00", "1450.00", "125000.00"))
    assert "TXN000000001" in pages["Payment History"]


@pytest.mark.parametrize("path", sorted(LIB.glob("*.json")), ids=lambda p: p.stem)
def test_screens_never_show_the_apps_messages_and_never_crash(path):
    spec = json.loads(path.read_text())["spec"]
    shown = " ".join(_text(p["source"]) for p in reference.page_sources(spec))
    leaked = [m for m in _messages(spec) if m in shown]
    assert not leaked, f"answer-key messages on the reference screens: {leaked[:3]}"
