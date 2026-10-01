"""
HR's scenarios page has two "+" signs (owner, 2026-10-01): the "+ New Scenario" button on the right worked,
the "+" beside "Create a New Scenario" on the left was a picture and did nothing. Both now take HR to the
form, cursor in the title. Fake-AI server, throwaway database.
"""
import pytest
from playwright.sync_api import expect

from .conftest import HR, login

pytestmark = pytest.mark.e2e


def test_both_plus_signs_take_hr_to_the_new_scenario_form(e2e_server, app_page):
    page = app_page
    login(page, HR)
    page.click("#hr-round-nav button:has-text('Manual test cases')")
    left = page.locator("#create-scenario-panel .panel-icon")
    right = page.locator("button:has-text('+ New Scenario')")
    for plus in (left, right):
        page.locator("body").click(position={"x": 5, "y": 5})           # focus elsewhere first
        assert page.evaluate("document.activeElement.id") != "s-title"
        plus.click()
        expect(page.locator("#s-title")).to_be_focused()
    expect(left).to_have_attribute("aria-label", "New scenario")
