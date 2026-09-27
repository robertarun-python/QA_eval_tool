"""Round 1 shows the app's structure - screens, API, database - so candidates can
write concrete steps and test data, without the test controls (their negative test
ideas). A real browser against the fake-AI server (see conftest.py)."""
import json
import sqlite3
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PASSWORDS, login

pytestmark = pytest.mark.e2e
CANDIDATE3 = ("candidate3@example.com", PASSWORDS["CANDIDATE3_PASSWORD"])  # an account no other browser test uses


def test_round1_shows_screens_api_and_database_but_not_the_test_controls(app_page, e2e_server):
    from app.services.practice_engine import reference
    spec = json.loads((Path(__file__).parent.parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())
    con = sqlite3.connect(e2e_server["log"].parent / "e2e.db")
    try:
        (sid, config) = con.execute("SELECT id, config_json FROM scenarios WHERE round_number = 2 AND is_live = 1").fetchone()
        (r1_id, r1_title) = con.execute("SELECT id, title FROM scenarios WHERE round_number = 1 AND is_live = 1").fetchone()
        config = {**json.loads(config or "{}"), "practice_spec": spec, "reference_panel": reference.reference_panel(spec),
                  "paired_round1_scenario_id": r1_id, "paired_round1_title": r1_title}
        con.execute("UPDATE scenarios SET config_json = ? WHERE id = ?", (json.dumps(config), sid))
        con.commit()
    finally:
        con.close()
    page = app_page
    login(page, CANDIDATE3)
    page.wait_for_load_state("networkidle")
    if not page.locator(".modal-overlay.modal-open").first.is_visible():
        page.click('button[onclick="loadRound(1)"]')
    page.click("text=Got it - Start Round 1")
    ref = page.locator("details.hint-box", has_text="Reference: the application")
    expect(ref).to_be_visible()
    for tab in ("Pages", "API", "Database"):
        expect(ref.get_by_role("button", name=tab, exact=True)).to_be_visible()
    expect(ref.get_by_role("button", name="Test controls")).to_have_count(0)
    ref.get_by_role("button", name="API", exact=True).click()
    expect(ref).to_contain_text("/api/login")
    expect(ref).not_to_contain_text("network_down")  # a simulated failure's name would hint a negative test
