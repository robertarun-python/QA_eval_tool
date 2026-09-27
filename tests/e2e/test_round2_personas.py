"""A candidate's whole journey in a real browser, in every language: Round 1 on the
live scenario, then Round 2 on a real practice app - pick the language and the
test case, talk to the assistant, write a Selenium test in the editor, Run it
(real Chrome through the practice environment's fenced Grid), see it pass,
submit. The AI is the fake one (no cost); everything else is real. Part of the
gate before the owner tests anything (failure-first-review section 8)."""
import io
import json
import re
import sqlite3
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect

from .conftest import HR, PASSWORDS, login

pytestmark = pytest.mark.e2e
PERSONA = ("candidate5@example.com", PASSWORDS["CANDIDATE5_PASSWORD"])  # no other browser test uses it

PROGRAMS = {
    "java": """import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeOptions;
import org.openqa.selenium.remote.RemoteWebDriver;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import java.net.URL;
import java.time.Duration;

public class Main {
    public static void main(String[] args) throws Exception {
        WebDriver driver = new RemoteWebDriver(new URL(System.getenv("SELENIUM_GRID_URL")), new ChromeOptions().addArguments("--headless=new"));
        try {
            driver.get(System.getenv("PRACTICE_APP_URL"));
            driver.findElement(By.id("email")).sendKeys("testuser@library.test");
            driver.findElement(By.id("password")).sendKeys("Test@123");
            driver.findElement(By.id("login")).click();
            String who = new WebDriverWait(driver, Duration.ofSeconds(10))
                .until(ExpectedConditions.visibilityOfElementLocated(By.id("signed-in-user"))).getText();
            System.out.println(who.equals("Test User") ? "PASS signed in as " + who : "FAIL signed in as " + who);
        } finally {
            driver.quit();
        }
    }
}
""",
    "python": """import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

options = webdriver.ChromeOptions()
options.add_argument("--headless=new")
driver = webdriver.Remote(os.environ["SELENIUM_GRID_URL"], options=options)
try:
    driver.get(os.environ["PRACTICE_APP_URL"])
    driver.find_element(By.ID, "email").send_keys("testuser@library.test")
    driver.find_element(By.ID, "password").send_keys("Test@123")
    driver.find_element(By.ID, "login").click()
    who = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.ID, "signed-in-user"))).text
    print(("PASS" if who == "Test User" else "FAIL") + " signed in as " + who)
finally:
    driver.quit()
""",
    "javascript": """const { Builder, By, until } = require("selenium-webdriver");
const chrome = require("selenium-webdriver/chrome");
(async () => {
  const driver = await new Builder().forBrowser("chrome").usingServer(process.env.SELENIUM_GRID_URL)
    .setChromeOptions(new chrome.Options().addArguments("--headless=new")).build();
  try {
    await driver.get(process.env.PRACTICE_APP_URL);
    await driver.findElement(By.id("email")).sendKeys("testuser@library.test");
    await driver.findElement(By.id("password")).sendKeys("Test@123");
    await driver.findElement(By.id("login")).click();
    const who = await (await driver.wait(until.elementLocated(By.id("signed-in-user")), 10000)).getText();
    console.log((who === "Test User" ? "PASS" : "FAIL") + " signed in as " + who);
  } finally {
    await driver.quit();
  }
})();
""",
}


def _engine_round2(e2e_server):
    from app.services.practice_engine import reference
    spec = json.loads((Path(__file__).parent.parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())
    con = sqlite3.connect(e2e_server["log"].parent / "e2e.db")
    try:
        (sid, config) = con.execute("SELECT id, config_json FROM scenarios WHERE round_number = 2 AND is_live = 1").fetchone()
        (r1_id, r1_title) = con.execute("SELECT id, title FROM scenarios WHERE round_number = 1 AND is_live = 1").fetchone()
        from app.services import round2_typist
        config = {**json.loads(config or "{}"), "mode": "ai_test_automation", "practice_spec": spec,
                  "reference_panel": reference.reference_panel(spec), "environment_code_by_language": dict(round2_typist.STARTERS),
                  "environment_support_by_language": {}, "paired_round1_scenario_id": r1_id, "paired_round1_title": r1_title}
        con.execute("UPDATE scenarios SET config_json = ? WHERE id = ?", (json.dumps(config), sid))
        con.commit()
    finally:
        con.close()


def _reset_persona(e2e_server):
    with httpx.Client(base_url=e2e_server["base_url"]) as hr:
        assert hr.post("/auth/login", json={"identifier": HR[0], "password": HR[1]}).status_code == 200
        res = hr.post("/candidates/upload" if False else "/hr/candidates/upload",
                      files={"file": ("p.txt", io.BytesIO(f"email,exam_date\n{PERSONA[0]},2026-09-27\n".encode()), "text/plain")})
        assert res.status_code == 200, res.text


def _open_round(page, n):
    page.wait_for_load_state("networkidle")
    if not page.locator(".modal-overlay.modal-open").first.is_visible():
        page.click(f'button[onclick="loadRound({n})"]')


@pytest.mark.parametrize("language", ["java", "python", "javascript"])
def test_a_candidate_goes_from_round1_to_a_passing_round2_run(app_page, e2e_server, language):
    _engine_round2(e2e_server)
    _reset_persona(e2e_server)
    page = app_page
    login(page, PERSONA)

    # Round 1 on the live scenario, with the app's reference in view
    _open_round(page, 1)
    page.click("text=Got it - Start Round 1")
    expect(page.locator("details.hint-box", has_text="Reference: the application")).to_be_visible()
    row = page.locator("#tc-rows tr").first
    row.locator(".tc-title").fill("Sign in with a valid account")
    row.locator(".tc-pre").fill("The member testuser@library.test exists")
    row.locator(".tc-steps").fill("1. Open the app\n2. Enter the email and password\n3. Click Log in")
    row.locator(".tc-data").fill("testuser@library.test / Test@123")
    row.locator(".tc-expected").fill("The page shows the signed-in user Test User")
    page.locator("#round1-submit-btn").click()
    expect(page.locator('button[onclick="loadRound(2)"]')).to_be_enabled()

    # Round 2: language, test case, the assistant, the code, Run, submit
    _open_round(page, 2)
    page.select_option("#r4a-intro-language-select", language)
    page.click("#r4a-intro-start-btn")
    page.select_option("#r4a-next-pick-select", index=1)
    page.click("#r4a-automate-btn")
    page.fill("#r4a-prompt-0", "Sign in as testuser@library.test with Test@123 and check the signed-in user is Test User. Generate the code.")
    page.click(".r4a-ask-btn")
    expect(page.locator("body")).to_contain_text("Fake AI mode - the assistant would write down")
    editor = page.locator("#r4a-code-0")
    expect(editor).to_be_visible()
    editor.fill(PROGRAMS[language])
    page.click(".r4a-run-btn")
    expect(page.locator("#r4a-run-result-0")).to_contain_text(re.compile("PASS signed in as Test User"), timeout=90_000)
    page.click("#r4a-submit-btn")
    expect(page.locator('button[onclick="loadRound(3)"]')).to_be_enabled()
