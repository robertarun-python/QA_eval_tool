"""
Browser tests: a real server and a real Chromium, driving the pages the way
a candidate and HR use them. The server runs in fake-AI mode (every AI call
returns a scripted reply - services/fake_llm.py) against a throwaway SQLite
database, so these never spend API credit and never touch real data.

The unit tests exercise the backend with a faked model; these catch what
they can't - the page itself: timers, where errors appear, layout, buttons
that do nothing, request shapes the page sends.

Skipped automatically when Playwright or its Chromium isn't installed:
    pip install -r requirements-dev.txt && python -m playwright install chromium
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
PASSWORDS = {
    "HR_PASSWORD": "e2e-hr", "CANDIDATE1_PASSWORD": "e2e-c1", "CANDIDATE2_PASSWORD": "e2e-c2",
    "CANDIDATE3_PASSWORD": "e2e-c3", "CANDIDATE5_PASSWORD": "e2e-c5",
}
HR = ("hr@example.com", PASSWORDS["HR_PASSWORD"])
CANDIDATE = ("candidate1@example.com", PASSWORDS["CANDIDATE1_PASSWORD"])


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: browser test against a fake-AI server (tests/e2e)")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _publish(client: httpx.Client, round_number: int, title: str) -> None:
    s = client.post("/hr/scenarios", json={"round_number": round_number, "title": title, "description": f"{title} - browser test scenario.",
                                           "experience_band": "0-7", "time_limit_minutes": 20})
    s.raise_for_status()
    client.post(f"/hr/scenarios/{s.json()['id']}/publish").raise_for_status()


@pytest.fixture(scope="session")
def e2e_server(tmp_path_factory):
    work = tmp_path_factory.mktemp("e2e")
    env = {
        **os.environ, **PASSWORDS,
        # Explicit overrides - these win over the repo's .env, so the test
        # server can never reach the real database or the real API.
        "DATABASE_URL": f"sqlite:///{work / 'e2e.db'}", "ANTHROPIC_API_KEY": "", "LLM_FAKE_MODE": "true",
        "LLM_TOOL_OUTPUT": "false", "PYTHONUNBUFFERED": "1",
    }
    subprocess.run([sys.executable, "-m", "app.seed"], cwd=BACKEND, env=env, check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "app.seed_round4_auto"], cwd=BACKEND, env=env, check=True, capture_output=True)
    port = _free_port()
    log = open(work / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError(f"e2e server didn't start - see {work / 'server.log'}")

    with httpx.Client(base_url=base, timeout=30) as hr:
        hr.post("/auth/login", json={"identifier": HR[0], "password": HR[1]}).raise_for_status()
        _publish(hr, 1, "Login page test design")
        _publish(hr, 3, "Sum of two numbers")
        _publish(hr, 4, "Payment timeout investigation")
        auto = next(s for s in hr.get("/hr/scenarios").json() if s["round_number"] == 2)
        hr.post(f"/hr/scenarios/{auto['id']}/publish").raise_for_status()

    yield {"base_url": base, "log": work / "server.log"}
    proc.terminate()
    proc.wait(timeout=10)
    log.close()


@pytest.fixture()
def app_page(e2e_server, page):
    """A browser page on the test server that accepts every confirm() -
    Playwright dismisses dialogs by default, which would cancel submits."""
    page.on("dialog", lambda d: d.accept())
    page.set_default_timeout(15000)
    page.goto(e2e_server["base_url"])
    return page


def login(page, who):
    page.fill("#email", who[0])
    page.fill("#password", who[1])
    page.click("text=Log in")
