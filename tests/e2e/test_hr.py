"""HR's side in a real browser against the fake-AI server (see conftest.py)."""
import pytest
from playwright.sync_api import expect

from .conftest import CANDIDATE, HR, login

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


def test_hr_sees_the_candidates_scored_rounds(app_page):
    """Runs after test_candidate_flow.py (file order): the candidate has
    finished all four rounds, each scored by the fake AI."""
    page = app_page
    login(page, HR)
    page.click("button[onclick=\"selectHRPage('candidates')\"]")
    page.click(f'button[aria-label="View report for {CANDIDATE[0]}"]')
    detail = page.locator("#candidate-detail-view")
    expect(detail).to_be_visible()
    expect(detail).to_contain_text("placeholder feedback")  # the fake scorer's text reached HR's report
    expect(detail.locator("text=Override score").first).to_be_visible()
