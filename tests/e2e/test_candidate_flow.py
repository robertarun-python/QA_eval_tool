"""
A candidate goes through all four rounds in a real browser against a
fake-AI server (see conftest.py). One test per round, in order - each round
unlocks the next, so they share one candidate and run sequentially.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import CANDIDATE, HR, login

pytestmark = pytest.mark.e2e


def _open_round(page, n):
    """The app opens the next unlocked round on its own (with its start
    screen); click the round in the nav only if that isn't already showing."""
    page.wait_for_load_state("networkidle")
    if not page.locator(".modal-overlay.modal-open").first.is_visible():  # each round has its own start screen
        page.click(f'button[onclick="loadRound({n})"]')


def test_round1_manual_test_cases(app_page):
    page = app_page
    login(page, CANDIDATE)
    _open_round(page, 1)
    expect(page.locator("#round-intro-overlay")).to_contain_text("20 minutes")  # the round's limit, not a stale one
    page.click("text=Got it - Start Round 1")

    row = page.locator("#tc-rows tr").first
    expect(row).to_be_visible()
    row.locator(".tc-title").fill("Login with valid credentials")
    row.locator(".tc-pre").fill("A registered account exists")
    row.locator(".tc-steps").fill("1. Open the login page\n2. Enter the email and password\n3. Click Login")
    row.locator(".tc-data").fill("email = jordan@example.com; password = Passw0rd!")
    row.locator(".tc-expected").fill("The dashboard shows 'Welcome, Jordan'")
    # Card layout: scenario full width, the paired fields side by side.
    title_box, pre_box, data_box = (row.locator(s).bounding_box() for s in (".tc-title", ".tc-pre", ".tc-data"))
    assert title_box["width"] > pre_box["width"] * 1.8
    assert abs(pre_box["y"] - data_box["y"]) < 5 and data_box["x"] > pre_box["x"]

    submit = page.locator("#round1-submit-btn")
    expect(submit).to_be_enabled()
    submit.click()
    expect(page.locator('button[onclick="loadRound(2)"]')).to_be_enabled()


def test_round2_ai_assisted_automation(app_page):
    page = app_page
    login(page, CANDIDATE)
    _open_round(page, 2)
    page.select_option("#r4a-intro-language-select", "python")
    page.click("#r4a-intro-start-btn")

    page.select_option("#r4a-next-pick-select", index=1)
    page.click("#r4a-automate-btn")
    page.fill("#r4a-prompt-0", "Encode step 1: log in with UI.login using my test data")
    page.click(".r4a-ask-btn")

    editor = page.locator("#r4a-code-0")
    expect(editor).to_be_visible()
    expect(editor).to_have_value(re.compile("fake AI mode - instruction: Encode step 1"))
    # The practice environment is the read-only block above; the editor's
    # line numbers carry on from the real file, not from 1.
    expect(page.locator(".r4a-env summary")).to_contain_text("read-only")
    first_line = int(page.locator("#r4a-code-0-gutter span").first.inner_text())
    assert first_line > 1

    page.click(".r4a-run-btn")
    expect(page.locator("#r4a-run-result-0")).to_contain_text(re.compile("PASS|FAIL|RAN - NOTHING REPORTED"))

    page.click("#r4a-submit-btn")
    expect(page.locator('button[onclick="loadRound(3)"]')).to_be_enabled()


def test_round3_ai_prompted_coding(app_page):
    page = app_page
    login(page, CANDIDATE)
    _open_round(page, 3)
    page.click("text=Got it")

    expect(page.locator(".round3-io-format")).to_contain_text("Input:")
    expect(page.locator("#timer")).to_contain_text(re.compile(r"(19|20):\d\d"))
    page.fill("#round3-coding-message", "Read one line of input into a list called nums.")
    page.click("#round3-coding-send-btn")
    expect(page.locator("#round3-coding-turns")).to_contain_text("Fake AI mode")
    expect(page.locator("#round3-coding-code")).to_contain_text("instruction: Read one line of input")

    page.click("#round3-coding-run-btn")  # really executes the code in the sandbox
    expect(page.locator("#round3-terminal-status")).to_contain_text("exited with code 0", timeout=30000)

    page.click("text=Submit Round 3")
    expect(page.locator('button[onclick="loadRound(4)"]')).to_be_enabled()


def test_round4_debugging_investigation(app_page):
    page = app_page
    login(page, CANDIDATE)
    _open_round(page, 4)
    page.click("text=Got it - Start Round 4")

    area = page.locator(".inv-area").first
    start_height = area.bounding_box()["height"]
    area.fill("Checked the payment service logs around the failure.\nFound repeated gateway timeouts.\nRuled out the database.")
    assert area.bounding_box()["height"] > start_height  # rows grow with the text instead of scrolling
    page.fill("#inv-root-cause", "The payment gateway times out under load; confirmed in the logs.")
    submit = page.locator("#round2-submit-btn")
    expect(submit).to_be_enabled()
    submit.click()
    expect(page.locator("text=You've already submitted this round").or_(page.locator('button[onclick="loadRound(4)"] .tick-num'))).to_be_visible()


def test_hr_sees_the_candidates_scored_rounds(app_page):
    """Last in this file on purpose: it needs the candidate above to have
    finished all four rounds (each scored by the fake AI). It lived in
    test_hr.py and failed whenever that file ran on its own."""
    page = app_page
    login(page, HR)
    page.click("button[onclick=\"selectHRPage('candidates')\"]")
    page.click(f'button[aria-label="View report for {CANDIDATE[0]}"]')
    detail = page.locator("#candidate-detail-view")
    expect(detail).to_be_visible()
    expect(detail).to_contain_text("placeholder feedback")  # the fake scorer's text reached HR's report
    expect(detail.locator("text=Override score").first).to_be_visible()
