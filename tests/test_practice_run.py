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
        java.util.function.Function<String, WebElement> el = id -> wait.until(ExpectedConditions.visibilityOfElementLocated(By.id(id)));
        try {
            driver.get(app);
            el.apply("email").sendKeys("testuser@library.test");
            el.apply("password").sendKeys("Test@123");
            el.apply("login").click();
            wait.until(ExpectedConditions.titleContains("Home"));
            el.apply("nav-search").click();
            wait.until(ExpectedConditions.titleContains("Search"));
            el.apply("book_id").sendKeys("BK-001");
            el.apply("open-book").click();
            wait.until(ExpectedConditions.titleContains("Book Details"));
            el.apply("book_id").sendKeys("BK-001");
            el.apply("borrow-book").click();
            wait.until(ExpectedConditions.titleContains("Borrow Confirmation"));
            System.out.println("UI: " + el.apply("message").getText());
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


def el(element_id):
    return wait.until(EC.visibility_of_element_located((By.ID, element_id)))


try:
    driver.get(os.environ["PRACTICE_APP_URL"])
    el("email").send_keys("testuser@library.test")
    el("password").send_keys("Test@123")
    el("login").click()
    wait.until(EC.title_contains("Home"))
    el("nav-search").click()
    wait.until(EC.title_contains("Search"))
    el("book_id").send_keys("BK-001")
    el("open-book").click()
    wait.until(EC.title_contains("Book Details"))
    el("book_id").send_keys("BK-001")
    el("borrow-book").click()
    wait.until(EC.title_contains("Borrow Confirmation"))
    print("UI:", el("message").text)
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
  const el = (id) => driver.wait(until.elementIsVisible(driver.wait(until.elementLocated(By.id(id)), 10000)), 10000);
  try {
    await driver.get(process.env.PRACTICE_APP_URL);
    await el("email").sendKeys("testuser@library.test");
    await el("password").sendKeys("Test@123");
    await el("login").click();
    await driver.wait(until.titleContains("Home"), 10000);
    await el("nav-search").click();
    await driver.wait(until.titleContains("Search"), 10000);
    await el("book_id").sendKeys("BK-001");
    await el("open-book").click();
    await driver.wait(until.titleContains("Book Details"), 10000);
    await el("book_id").sendKeys("BK-001");
    await el("borrow-book").click();
    await driver.wait(until.titleContains("Borrow Confirmation"), 10000);
    console.log("UI: " + await el("message").getText());
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



def test_a_grid_whose_browser_node_is_stuck_down_is_restarted_not_duplicated(monkeypatch):
    """Seen after the machine slept: the Grid answered but its node was 'down'; the
    old check started a second Grid, which couldn't get the port, and Runs failed."""
    if not (practice_run.vendor() / "selenium-server.jar").exists():
        pytest.skip("browser tools not installed")
    events = []
    clock = iter(range(0, 10_000, 5))  # every look at the clock moves 5 s on

    def state():
        if "started" in events:
            return True, True  # the fresh Grid is ready
        if "stopped" in events:
            return False, False
        return True, False  # answering, node stuck down

    class FakeProcess:
        def __init__(self, cmd, **kwargs):
            events.append("started")

        def poll(self):
            return None
    monkeypatch.setattr(practice_run, "_grid_state", state)
    monkeypatch.setattr(practice_run, "_stop_grid", lambda: events.append("stopped"))
    monkeypatch.setattr(practice_run, "_no_app_nap", lambda: None)
    monkeypatch.setattr(practice_run.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(practice_run.time, "sleep", lambda s: None)
    monkeypatch.setattr(practice_run.time, "monotonic", lambda: next(clock))
    assert practice_run.ensure_grid() == practice_run.grid_url()
    assert events == ["stopped", "started"]


ESCAPE = r'''
import json, os, urllib.request, urllib.error
from selenium import webdriver
from selenium.webdriver.common.by import By

secret = os.environ["DECOY_URL"]
options = webdriver.ChromeOptions()
for a in ("--headless=new", "--no-sandbox", "--proxy-server=direct://", "--remote-debugging-port=9333", "--user-data-dir=/tmp/x"):
    options.add_argument(a)
driver = webdriver.Remote(os.environ["SELENIUM_GRID_URL"], options=options)
app = os.environ["PRACTICE_APP_URL"]

def attempt(label, fn):
    try:
        result = fn()
        print(label, "->", "LEAK" if "DECOY-SECRET" in str(result) or "PRACTICE-FILE" in str(result) else "no leak")
    except Exception as e:
        print(label, "-> refused", type(e).__name__)

try:
    attempt("file url", lambda: (driver.get("file://" + os.environ["DECOY_FILE"]), driver.page_source)[1])
    attempt("other local server", lambda: (driver.get(secret), driver.page_source)[1])
    attempt("internet", lambda: (driver.get("https://example.com/"), driver.page_source)[1])
    driver.get(app)
    attempt("js navigation", lambda: (driver.execute_script("window.location = arguments[0]", secret), __import__("time").sleep(1.5), driver.page_source)[2])
    driver.get(app)
    attempt("js fetch", lambda: driver.execute_async_script(
        "var cb = arguments[arguments.length-1]; fetch(arguments[0]).then(r => r.text()).then(cb, e => cb('failed ' + e));", secret))
    attempt("js file", lambda: (driver.execute_script("window.location = 'file://' + arguments[0]", os.environ["DECOY_FILE"]), __import__("time").sleep(1), driver.page_source)[2])
    sid = driver.session_id
    base = os.environ["SELENIUM_GRID_URL"]
    def cdp():
        req = urllib.request.Request(f"{base}/session/{sid}/goog/cdp/execute", data=json.dumps({"cmd": "Page.navigate", "params": {"url": secret}}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        return urllib.request.urlopen(req).read()
    attempt("raw cdp command", cdp)
    attempt("grid admin", lambda: urllib.request.urlopen(base + "/se/grid/distributor/status").read())
    driver.get(app)
    print("practice app still works:", "Login" in driver.title)
finally:
    driver.quit()
'''


@needs_tools
def test_a_candidates_browser_cannot_escape_the_practice_environment(tmp_path):
    """Chrome runs outside the sandbox: through the practice server it may only
    open the practice app - no local files, no other local server, no internet,
    no raw browser (CDP) or Grid admin commands, no unsafe browser options."""
    import http.server
    import threading

    class Decoy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><body>DECOY-SECRET</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    decoy = http.server.HTTPServer(("127.0.0.1", 0), Decoy)
    threading.Thread(target=decoy.serve_forever, daemon=True).start()
    secret_file = tmp_path / "secret.html"
    secret_file.write_text("<p>PRACTICE-FILE</p>")
    code = ESCAPE.replace('os.environ["DECOY_URL"]', repr(f"http://127.0.0.1:{decoy.server_address[1]}/")) \
                 .replace('os.environ["DECOY_FILE"]', repr(str(secret_file)))
    try:
        result = practice_run.run("python", code, SPEC)
    finally:
        decoy.shutdown()
    out = result.stdout
    assert "LEAK" not in out, out
    for label in ("file url", "other local server", "internet", "js navigation", "js fetch", "js file", "raw cdp command", "grid admin"):
        assert f"{label} ->" in out, (label, out, result.stderr[-800:])
    assert "raw cdp command -> refused" in out and "grid admin -> refused" in out
    assert "practice app still works: True" in out, (out, result.stderr[-800:])


def test_the_api_paths_join_either_way():
    """Simulated API tester (2026-09-28): PRACTICE_API_URL ends in "api/" and the Reference lists
    "/api/login" - the assistant joined them into ".../api//api/login", got a 404 and the candidate was
    stuck for 8 turns. Every way of joining the two reaches the same API."""
    code = '''import json, os, urllib.request
app, api = os.environ["PRACTICE_APP_URL"], os.environ["PRACTICE_API_URL"]
body = json.dumps({"email": "testuser@library.test", "password": "Test@123"}).encode()
for label, url in (("app+path", app + "api/login"), ("app+/path", app + "/api/login"), ("api+name", api + "login"),
                   ("api+/api/path", api + "/api/login"), ("api+api/path", api + "api/login")):
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    print(label, "token" in json.loads(urllib.request.urlopen(req).read()))
'''
    out = practice_run.run("python", code, SPEC, browser=False).stdout
    for label in ("app+path", "app+/path", "api+name", "api+/api/path", "api+api/path"):
        assert f"{label} True" in out, out
