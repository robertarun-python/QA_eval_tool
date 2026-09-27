"""
A candidate's Round 2 Run against the real practice environment:

  1. a fresh practice server (practice_engine.server) with the description's
     starting data, its database in a folder of its own;
  2. the Selenium Grid (started on first use, kept running) for browser tests;
  3. the candidate's code in the sandbox (execution_service.run_code), which
     may reach ONLY those two ports on this machine, read the automation
     libraries in vendor/, and read/write the practice database - nothing else.

The candidate's code finds everything through environment variables, the way
a real test project is configured:
    PRACTICE_APP_URL   the application, e.g. http://127.0.0.1:53817/
    PRACTICE_API_URL   the same + "api/"
    PRACTICE_DB        path of the SQLite database file
    SELENIUM_GRID_URL  the Selenium Grid for RemoteWebDriver (through the practice
                       server, which closes exactly this Run's browsers at the end)
"""
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from ...config import settings
from .. import execution_service

_BACKEND = Path(__file__).resolve().parents[3]
_GRID_LOCK = threading.Lock()
_grid: subprocess.Popen | None = None


class EnvironmentUnavailable(RuntimeError):
    """The browser tools or the practice server couldn't be started - an
    infrastructure problem, never the candidate's."""


# The Java libraries a candidate's test may use (tools/setup_vendor.sh): Selenium, SQLite, and the
# JSON libraries Java API tests use - measured (2026-09-27): the assistant's Java API test used
# org.json's JSONObject and didn't compile without it.
JAVA_JARS = ("selenium-server.jar", "sqlite-jdbc.jar", "json.jar", "gson.jar",
             "jackson-databind.jar", "jackson-core.jar", "jackson-annotations.jar")


def vendor() -> Path:
    return Path(settings.vendor_dir)


def _chrome_binary() -> Path:
    return vendor() / "chrome-mac-arm64" / "Google Chrome for Testing.app" / "Contents" / "MacOS" / "Google Chrome for Testing"


def grid_url() -> str:
    return f"http://127.0.0.1:{settings.selenium_grid_port}"


def _grid_state() -> tuple[bool, bool]:
    """(the Grid answers, it has a browser node ready)."""
    try:
        with urllib.request.urlopen(grid_url() + "/status", timeout=3) as res:
            return True, bool(json.loads(res.read())["value"]["ready"])
    except (OSError, ValueError, KeyError):
        return False, False


def _grid_ready() -> bool:
    return _grid_state()[1]


def _stop_grid() -> None:
    """Stops the Grid on our port (ours: it runs from vendor/ with our config)."""
    global _grid
    if _grid is not None and _grid.poll() is None:
        _grid.terminate()
        try:
            _grid.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _grid.kill()
    subprocess.run(["pkill", "-f", f"{vendor() / 'selenium-server.jar'} standalone"], capture_output=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _grid_state()[0]:
        time.sleep(0.5)
    _grid = None


def ensure_grid(timeout_seconds: float = 60) -> str:
    """The Selenium Grid's address, starting it if it isn't running."""
    global _grid
    with _GRID_LOCK:
        answers, ready = _grid_state()
        if ready:
            return grid_url()
        if answers:
            # Running, but its browser node is marked down (seen after the machine slept): give it a
            # moment to recover, then restart it rather than fail the Run or start a second one.
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                time.sleep(1)
                if _grid_ready():
                    return grid_url()
            _stop_grid()
        jar, driver, chrome = vendor() / "selenium-server.jar", vendor() / "chromedriver-mac-arm64" / "chromedriver", _chrome_binary()
        missing = [str(p) for p in (jar, driver, chrome) if not p.exists()]
        if missing:
            raise EnvironmentUnavailable("browser tools are not installed (run tools/setup_vendor.sh): " + ", ".join(missing))
        _no_app_nap()
        config = vendor() / "grid.toml"
        # Background throttling off: with other browser windows active, Chrome treats a headless page as
        # in the background - timers slow down and clicks can be dropped (found when the practice Runs
        # followed the app's own browser tests). Standard for automation grids.
        stereotype = json.dumps({"browserName": "chrome", "goog:chromeOptions": {"binary": str(chrome), "args": [
            "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding"]}})
        config.write_text(
            f'[server]\nport = {settings.selenium_grid_port}\nhost = "127.0.0.1"\n\n'
            '[node]\nselenium-manager = false\noverride-max-sessions = true\nmax-sessions = 4\ndetect-drivers = false\n'
            'session-timeout = 120\n\n'
            f'[[node.driver-configuration]]\ndisplay-name = "chrome"\nwebdriver-executable = "{driver}"\nmax-sessions = 4\n'
            f"stereotype = '{stereotype}'\n", encoding="utf-8")
        log = open(vendor() / "grid.log", "ab")
        _grid = subprocess.Popen(["java", "-jar", str(jar), "standalone", "--config", str(config)], stdout=log, stderr=log,
                                 start_new_session=True)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if _grid_ready():
                return grid_url()
            if _grid.poll() is not None:
                break
            time.sleep(0.5)
        raise EnvironmentUnavailable("the Selenium Grid did not start (see vendor/grid.log)")


def _no_app_nap() -> None:
    """macOS App Nap slows an app with no visible window after a while - headless
    Chrome for Testing included - and its pages then drop clicks and typing:
    Runs failed in clusters until it was switched off (Chrome's own
    anti-throttling options can't override it). Harmless if already off."""
    if sys.platform == "darwin":
        subprocess.run(["defaults", "write", "com.google.chrome.for.testing", "NSAppSleepDisabled", "-bool", "YES"], capture_output=True)


class PracticeServer:
    """One practice server for one Run, in its own process."""

    def __init__(self, spec: dict, folder: Path, grid: str | None = None):
        self.folder = folder
        spec_file = folder / "spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        self.db = folder / "practice.db"
        self.process = subprocess.Popen([sys.executable, "-m", "app.services.practice_engine.server", "--spec", str(spec_file),
                                         "--db", str(self.db), "--port", "0", *(["--grid", grid] if grid else [])],
                                        cwd=_BACKEND, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = self.process.stdout.readline().strip()
        if not line.startswith("READY "):
            self.stop()
            raise EnvironmentUnavailable("the practice app did not start: " + (self.process.stderr.read() or line)[-400:])
        self.port = int(line.split()[1])
        self.url = f"http://127.0.0.1:{self.port}/"

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()  # the server closes this Run's browsers first
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


def run(language: str, code: str, spec: dict, browser: bool = True) -> execution_service.ExecutionResult:
    """Runs the candidate's code against a fresh practice environment."""
    grid = ensure_grid() if browser else None
    with tempfile.TemporaryDirectory(prefix="practice_env_") as tmp:
        server = PracticeServer(spec, Path(tmp), grid)
        try:
            v = vendor()
            access = execution_service.SandboxAccess(
                ports=(server.port,), read_dirs=(str(v),), rw_dirs=(tmp,),  # the practice server is the only door
                env={"PRACTICE_APP_URL": server.url, "PRACTICE_API_URL": server.url + "api/", "PRACTICE_DB": str(server.db),
                     **({"SELENIUM_GRID_URL": server.url + "wd/hub"} if grid else {})},
                java_classpath=tuple(str(v / jar) for jar in JAVA_JARS if (v / jar).exists()),
                python_path=(str(v / "python"),), node_path=str(v / "node" / "node_modules"),
                timeout_seconds=settings.practice_run_timeout_seconds)
            return execution_service.run_code(language, code, [], access=access)
        finally:
            server.stop()
