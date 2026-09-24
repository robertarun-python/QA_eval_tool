"""What a candidate sees around the rounds, in a real browser (see conftest.py)."""
import pytest
from playwright.sync_api import expect

from .conftest import CANDIDATE, login

pytestmark = pytest.mark.e2e


def test_candidate_on_a_phone_is_asked_to_use_a_computer(e2e_server, browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(e2e_server["base_url"])
    login(page, CANDIDATE)
    expect(page.locator(".small-screen-notice")).to_be_visible()
    expect(page.locator(".small-screen-notice")).to_contain_text("laptop or desktop")
    ctx.close()


def test_no_experimental_entry_when_nothing_is_published(app_page):
    login(app_page, CANDIDATE)
    app_page.wait_for_load_state("networkidle")
    expect(app_page.locator("#candidate-progressive-nav")).to_be_empty()
    expect(app_page.locator(".small-screen-notice")).to_be_hidden()  # desktop: the rounds, not the notice
