"""
A candidate's Run against the real practice environment, end to end, as it
will really happen: their code in the sandbox drives a real Chrome through the
Selenium Grid, calls the practice app's API and reads its database - Java
first (most candidates use Java + Selenium), then Python and JavaScript.
Offline, no AI calls; skipped where the browser tools aren't installed
(tools/setup_vendor.sh).
"""
import json
from pathlib import Path

import pytest

from app.services import execution_service
from app.services.practice_engine import practice_run

SPEC = json.loads((Path(__file__).parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())

needs_tools = pytest.mark.skipif(not (practice_run.vendor() / "selenium-server.jar").exists(), reason="browser tools not installed")

JAVA = r'''
import java.net.URI;
import java.net.URL;
import java.net.http.*;
import java.sql.*;
import org.openqa.selenium.*;
import org.openqa.selenium.chrome.ChromeOptions;
import org.openqa.selenium.remote.RemoteWebDriver;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import java.time.Duration;

public class Main {
    public static void main(String[] args) throws Exception {
        String app = System.getenv("PRACTICE_APP_URL");
        ChromeOptions options = new ChromeOptions();
        options.addArguments("--headless=new");
        WebDriver driver = new RemoteWebDriver(new URL(System.getenv("SELENIUM_GRID_URL")), options);
        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(10));
        try {
            driver.get(app);
            driver.findElement(By.id("email")).sendKeys("testuser@library.test");
            driver.findElement(By.id("password")).sendKeys("Test@123");
            driver.findElement(By.id("login")).click();
            wait.until(ExpectedConditions.titleContains("Home"));
            driver.findElement(By.id("nav-search")).click();
            wait.until(ExpectedConditions.titleContains("Search"));
            driver.findElement(By.id("book_id")).sendKeys("BK-001");
            driver.findElement(By.id("open-book")).click();
            wait.until(ExpectedConditions.titleContains("Book Details"));
            driver.findElement(By.id("book_id")).sendKeys("BK-001");
            driver.findElement(By.id("borrow-book")).click();
            wait.until(ExpectedConditions.titleContains("Borrow Confirmation"));
            System.out.println("UI: " + driver.findElement(By.id("message")).getText());
        } finally { driver.quit(); }
        HttpClient http = HttpClient.newHttpClient();
        HttpResponse<String> r = http.send(HttpRequest.newBuilder(URI.create(System.getenv("PRACTICE_API_URL") + "login"))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString("{\"email\":\"testuser@library.test\",\"password\":\"wrong\"}")).build(),
            HttpResponse.BodyHandlers.ofString());
        System.out.println("API: " + r.statusCode());
        try (Connection c = DriverManager.getConnection("jdbc:sqlite:" + System.getenv("PRACTICE_DB"));
             ResultSet rs = c.createStatement().executeQuery("SELECT available_copies FROM book WHERE id = 'BK-001'")) {
            rs.next();
            System.out.println("DB: " + rs.getInt(1));
        }
    }
}
'''

PYTHON = r'''
import json, os, sqlite3, urllib.request, urllib.error
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

options = webdriver.ChromeOptions()
options.add_argument("--headless=new")
driver = webdriver.Remote(os.environ["SELENIUM_GRID_URL"], options=options)
wait = WebDriverWait(driver, 10)
try:
    driver.get(os.environ["PRACTICE_APP_URL"])
    driver.find_element(By.ID, "email").send_keys("testuser@library.test")
    driver.find_element(By.ID, "password").send_keys("Test@123")
    driver.find_element(By.ID, "login").click()
    wait.until(EC.title_contains("Home"))
    driver.find_element(By.ID, "nav-search").click()
    wait.until(EC.title_contains("Search"))
    driver.find_element(By.ID, "book_id").send_keys("BK-001")
    driver.find_element(By.ID, "open-book").click()
    wait.until(EC.title_contains("Book Details"))
    driver.find_element(By.ID, "book_id").send_keys("BK-001")
    driver.find_element(By.ID, "borrow-book").click()
    wait.until(EC.title_contains("Borrow Confirmation"))
    print("UI:", driver.find_element(By.ID, "message").text)
finally:
    driver.quit()
req = urllib.request.Request(os.environ["PRACTICE_API_URL"] + "login", data=json.dumps({"email": "testuser@library.test", "password": "wrong"}).encode(),
                             headers={"Content-Type": "application/json"}, method="POST")
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    print("API:", e.code)
print("DB:", sqlite3.connect(os.environ["PRACTICE_DB"]).execute("SELECT available_copies FROM book WHERE id = 'BK-001'").fetchone()[0])
'''

JAVASCRIPT = r'''
const { Builder, By, until } = require("selenium-webdriver");
const chrome = require("selenium-webdriver/chrome");
const { DatabaseSync } = require("node:sqlite");
(async () => {
  const options = new chrome.Options().addArguments("--headless=new");
  const driver = await new Builder().usingServer(process.env.SELENIUM_GRID_URL).forBrowser("chrome").setChromeOptions(options).build();
  try {
    await driver.get(process.env.PRACTICE_APP_URL);
    await driver.findElement(By.id("email")).sendKeys("testuser@library.test");
    await driver.findElement(By.id("password")).sendKeys("Test@123");
    await driver.findElement(By.id("login")).click();
    await driver.wait(until.titleContains("Home"), 10000);
    await driver.findElement(By.id("nav-search")).click();
    await driver.wait(until.titleContains("Search"), 10000);
    await driver.findElement(By.id("book_id")).sendKeys("BK-001");
    await driver.findElement(By.id("open-book")).click();
    await driver.wait(until.titleContains("Book Details"), 10000);
    await driver.findElement(By.id("book_id")).sendKeys("BK-001");
    await driver.findElement(By.id("borrow-book")).click();
    await driver.wait(until.titleContains("Borrow Confirmation"), 10000);
    console.log("UI: " + await driver.findElement(By.id("message")).getText());
  } finally { await driver.quit(); }
  const r = await fetch(process.env.PRACTICE_API_URL + "login", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "testuser@library.test", password: "wrong" }) });
  console.log("API: " + r.status);
  const db = new DatabaseSync(process.env.PRACTICE_DB);
  console.log("DB: " + db.prepare("SELECT available_copies FROM book WHERE id = 'BK-001'").get().available_copies);
})().catch((e) => { console.error(e); process.exit(1); });
'''

EXPECTED = ["UI: Book borrowed successfully. Due date: 24-Feb-2024", "API: 401", "DB: 2"]


@needs_tools
@pytest.mark.parametrize("language, code", [("java", JAVA), ("python", PYTHON), ("javascript", JAVASCRIPT)])
def test_a_selenium_api_and_database_test_runs_in_the_sandbox(language, code):
    if not execution_service.toolchain_available(language):
        pytest.skip(f"{language} not installed")
    result = practice_run.run(language, code, SPEC)
    lines = [line for line in result.stdout.splitlines() if line.startswith(("UI:", "API:", "DB:"))]
    assert not result.infra_error and result.exit_code == 0 and lines == EXPECTED, (result.stdout[-800:], result.stderr[-1500:])


@needs_tools
def test_every_run_starts_from_fresh_data():
    """Two Runs of the same borrow: both succeed - no 'already borrowed' left over from the first."""
    for _ in range(2):
        result = practice_run.run("python", PYTHON, SPEC)
        assert "UI: Book borrowed successfully. Due date: 24-Feb-2024" in result.stdout, result.stderr[-800:]


@needs_tools
def test_the_sandbox_still_blocks_everything_else():
    probe = '''
import os, socket, urllib.request
for label, fn in [("internet", lambda: socket.create_connection(("1.1.1.1", 53), timeout=3)),
                  ("the real server", lambda: urllib.request.urlopen("http://127.0.0.1:8000/", timeout=3)),
                  ("secrets", lambda: open("/Users/aaron/Projects/QA_eval_tool/.env").read(1)),
                  ("the tools folder listing of the home folder", lambda: os.listdir(os.path.expanduser("~aaron")))]:
    try:
        fn()
        print("ALLOWED", label)
    except Exception:
        print("blocked", label)
'''
    result = practice_run.run("python", probe, SPEC, browser=False)
    assert "ALLOWED" not in result.stdout and result.stdout.count("blocked") == 4, (result.stdout, result.stderr[-500:])


def _open_browser_sessions() -> int:
    import urllib.request
    nodes = json.loads(urllib.request.urlopen(practice_run.grid_url() + "/status").read())["value"]["nodes"]
    return sum(1 for n in nodes for s in n["slots"] if s.get("session"))


@needs_tools
def test_runs_at_the_same_time_never_disturb_each_other():
    """Candidates in different branches run at the same moment: each Run closes
    only its own browsers (a cleanup that guessed by time closed others' - 10 of 30)."""
    from concurrent.futures import ThreadPoolExecutor
    jobs = [("java", JAVA), ("python", PYTHON), ("javascript", JAVASCRIPT)] * 2
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda job: practice_run.run(job[0], job[1], SPEC), jobs))
    for (language, _), result in zip(jobs, results):
        lines = [line for line in result.stdout.splitlines() if line.startswith(("UI:", "API:", "DB:"))]
        assert lines == EXPECTED, (language, result.stderr[-800:])
    assert _open_browser_sessions() == 0


@needs_tools
def test_a_test_that_forgets_to_close_its_browser_leaves_nothing_open():
    forgetful = PYTHON.replace("finally:\n    driver.quit()", "finally:\n    pass")
    assert forgetful != PYTHON
    result = practice_run.run("python", forgetful, SPEC)
    assert "UI: Book borrowed successfully" in result.stdout, result.stderr[-800:]
    assert _open_browser_sessions() == 0
