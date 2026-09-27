"""The address Round 1 shows for the app (its base_url) works in Round 2's
browser: the owner's own Round 2 opened https://loan-emi.example.test - as
Round 1 had shown it - and got a blocked-address exception (2026-09-27). The
fence sends it to this Run's practice app; look-alike addresses stay blocked.
Real browser, no AI calls."""
import json
from pathlib import Path

import pytest

from app.services import execution_service
from app.services.practice_engine import practice_run

SPEC = json.loads((Path(__file__).parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())
pytestmark = pytest.mark.skipif(not execution_service.toolchain_available("java")
                                or not (practice_run.vendor() / "selenium-server.jar").exists(),
                                reason="java or the Selenium vendor files are missing (tools/setup_vendor.sh)")

PROGRAM = """import org.openqa.selenium.By;
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
            for (String bad : new String[] {"https://library.example.test.evil.com/", "https://library.example.test@evil.com/", "https://example.com/"}) {
                try { driver.get(bad); System.out.println("OPENED " + bad); }
                catch (Exception e) { System.out.println("BLOCKED " + bad + " | " + e.getMessage().split("\\\\n")[0]); }
            }
            driver.get("https://library.example.test/page/login");
            driver.findElement(By.id("email")).sendKeys("testuser@library.test");
            driver.findElement(By.id("password")).sendKeys("Test@123");
            driver.findElement(By.id("login")).click();
            String who = new WebDriverWait(driver, Duration.ofSeconds(10))
                .until(ExpectedConditions.visibilityOfElementLocated(By.id("signed-in-user"))).getText();
            System.out.println("SIGNED IN AS " + who);
        } finally {
            driver.quit();
        }
    }
}
"""


def test_the_round1_address_opens_the_practice_app_and_look_alikes_stay_blocked():
    result = practice_run.run("java", PROGRAM, SPEC)
    out = result.stdout or ""
    assert result.exit_code == 0, out + (result.stderr or "")[-1500:]
    assert "SIGNED IN AS Test User" in out
    assert "OPENED" not in out and out.count("BLOCKED") == 3
    assert "use the address in PRACTICE_APP_URL" in out  # the blocked message says what to use
