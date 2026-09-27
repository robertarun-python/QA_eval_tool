"""HR's side in a real browser against the fake-AI server (see conftest.py)."""
import pytest
from playwright.sync_api import expect

from .conftest import HR, login

pytestmark = pytest.mark.e2e


def test_settings_show_ai_mode_and_round_time_limits(app_page):
    page = app_page
    login(page, HR)
    page.click("button[onclick=\"selectHRPage('settings')\"]")
    expect(page.locator("#hr-page-settings")).to_be_visible()
    expect(page.locator("#ai-health")).to_contain_text("AI mode: fake")
    for n in (1, 2, 3, 4):
        expect(page.locator(f"#set-time-r{n}")).to_be_visible()


def test_fake_mode_banner_is_on_every_page(app_page):
    expect(app_page.locator(".fake-ai-banner")).to_contain_text("FAKE AI MODE")


def test_a_refresh_on_round_2_keeps_round_2s_own_view(app_page):
    """A refresh lands back on the round HR had open. On Round 2 it used to
    show Round 1's scenario library (Create + Review) for Round 2's
    scenarios, and Review crashed on their non-test-case reference."""
    page = app_page
    login(page, HR)
    page.click('button[onclick="selectHRRound(2)"]')
    expect(page.locator("#round4-settings-panel")).to_be_visible()
    page.reload()
    expect(page.locator("#round4-settings-panel")).to_be_visible()
    expect(page.locator("#round4-settings-panel")).not_to_be_empty()
    expect(page.locator("#create-scenario-row")).to_be_hidden()
    expect(page.locator("#screening-history-panel")).to_be_hidden()


def _set_live_round1_build(e2e_server, summary):
    """The live Round 1 scenario's practice-app summary, written straight to
    the test server's database (fake AI mode can't really build one)."""
    import json
    import sqlite3
    con = sqlite3.connect(e2e_server["log"].parent / "e2e.db")
    try:
        (config,) = con.execute("SELECT config_json FROM scenarios WHERE round_number = 1 AND is_live = 1").fetchone()
        config = json.loads(config or "{}")
        if summary is None:
            config.pop("practice_app", None)
        else:
            config["practice_app"] = summary
        con.execute("UPDATE scenarios SET config_json = ? WHERE round_number = 1 AND is_live = 1", (json.dumps(config),))
        con.commit()
    finally:
        con.close()


def test_round_2_shows_only_the_generate_flow_when_round_1_changed(app_page, e2e_server):
    """The live Round 2 wasn't built for the live Round 1: none of its old
    content shows - just Generate, its progress (button disabled), then the
    results and Approve."""
    page = app_page
    card = page.locator("#round4-settings-panel")
    try:
        login(page, HR)
        page.click('button[onclick="selectHRRound(2)"]')
        expect(card.locator("#r2-generate-btn")).to_have_text("Generate Round 2")
        expect(card).to_contain_text("Login page test design")
        expect(card).not_to_contain_text("Save instructions")
        expect(card).not_to_contain_text("test environment")

        # Fake AI mode refuses to build: the reason shows and the button comes back.
        card.locator("#r2-generate-btn").click()
        expect(card.locator("#practice-app-r2-status")).to_contain_text("fake AI mode")
        expect(card.locator("#r2-generate-btn")).to_be_enabled()

        _set_live_round1_build(e2e_server, {"status": "building", "started_at": "2026-09-25T10:00:00", "step": 2, "step_detail": ""})
        page.reload()
        expect(card).to_contain_text("IN PROGRESS")
        expect(card).to_contain_text("Building the Python version")
        expect(card.locator("#r2-generate-btn")).to_be_disabled()

        _set_live_round1_build(e2e_server, {"status": "ready", "working": 1, "total": 1,
                                            "coverage": [{"title": "Valid login", "status": "works", "details": []}]})
        page.reload()
        expect(card).to_contain_text("1 of 1 test cases work")
        expect(card).to_contain_text("Valid login")
        expect(card.get_by_role("button", name="Approve - use it for Round 2")).to_be_visible()

        # Mostly verified: still approvable, with the unverified test case named.
        _set_live_round1_build(e2e_server, {"status": "ready", "working": 1, "total": 2, "coverage": [
            {"title": "Valid login", "status": "works", "details": []},
            {"title": "Locked account", "status": "fails", "details": ["step 3 returned true, expected false"]}],
            "unverified": [{"title": "Locked account", "status": "fails", "details": ["step 3 returned true, expected false"]}]})
        page.reload()
        expect(card).to_contain_text("1 test case could not be verified")
        expect(card).to_contain_text("Locked account")
        expect(card.get_by_role("button", name="Approve - use it for Round 2")).to_be_visible()
    finally:
        _set_live_round1_build(e2e_server, None)


def test_review_brings_the_scenario_into_view(app_page):
    """The review panel opens below the scenario list; on a laptop screen that
    is off-screen, so Review looked like it did nothing."""
    page = app_page
    page.set_viewport_size({"width": 1280, "height": 500})
    login(page, HR)
    page.click('button[onclick="selectHRRound(1)"]')
    page.get_by_role("button", name="Review").first.click()
    detail = page.locator("#scenario-detail")
    expect(detail).to_be_visible()
    # Its top scrolled up to the top of the window, not just a sliver at the bottom.
    page.wait_for_function("() => { const top = document.getElementById('scenario-detail').getBoundingClientRect().top;"
                           " return top >= 0 && top < 150; }", timeout=5000)


def test_hr_sees_the_reference_panel_candidates_get_in_round_2(app_page, e2e_server):
    """HR double-checks what candidates see: the Round 2 scenario shows the same
    reference panel (pages, API, database) the candidate's Round 2 screen draws."""
    import json
    import sqlite3
    from pathlib import Path

    from app.services.practice_engine import reference
    spec = json.loads((Path(__file__).parent.parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())
    con = sqlite3.connect(e2e_server["log"].parent / "e2e.db")
    try:
        (sid, config) = con.execute("SELECT id, config_json FROM scenarios WHERE round_number = 2 AND is_live = 1").fetchone()
        (r1_id, r1_title) = con.execute("SELECT id, title FROM scenarios WHERE round_number = 1 AND is_live = 1").fetchone()
        config = {**json.loads(config or "{}"), "practice_spec": spec, "reference_panel": reference.reference_panel(spec),
                  "paired_round1_scenario_id": r1_id, "paired_round1_title": r1_title}  # as approval pairs them
        con.execute("UPDATE scenarios SET config_json = ? WHERE id = ?", (json.dumps(config), sid))
        con.commit()
    finally:
        con.close()
    page = app_page
    login(page, HR)
    page.click('button[onclick="selectHRRound(2)"]')
    preview = page.locator("#candidate-reference-preview")
    expect(preview).to_contain_text("What candidates see in Round 2")
    expect(preview).to_contain_text("candidates see in Round 2 right now")
    expect(page.locator("#round4-settings-panel")).not_to_contain_text("No test environment generated yet")
    preview.get_by_role("button", name="API").click()
    expect(preview).to_contain_text("/api/login")
    preview.get_by_role("button", name="Database").click()
    expect(preview.locator('[data-r4a-ref-panel="database"]')).to_be_visible()
    # Pages are shown as screens (a locked-down frame), with the source folded underneath.
    preview.get_by_role("button", name="Pages").click()
    screen = preview.locator("iframe.r4a-page-screen").first
    expect(screen).to_be_visible()
    assert screen.get_attribute("sandbox") == ""  # no scripts, no navigation, no form submission
    expect(preview.frame_locator("iframe.r4a-page-screen").first.locator("form#login-form")).to_be_visible()
    expect(preview.frame_locator("iframe.r4a-page-screen").first.locator("#login")).to_be_visible()
    # The source reads like the browser's Inspect-element view: labelled so, one element per line, indented.
    first_page = preview.locator("details.r4a-page-source").first
    first_page.get_by_text("Page source - like Inspect element in the browser").click()
    html = first_page.locator("pre.r4a-page-html").inner_text()
    assert len(html.splitlines()) > 15, html[:300]
    assert any(line.startswith("    ") and 'id="login"' in line for line in html.splitlines()), html[:600]
    assert "<script" not in html.lower()
