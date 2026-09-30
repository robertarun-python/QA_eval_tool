"""
A round whose scoring failed, in a real browser (live, 2026-09-30: candidate4's Round 2 scoring was blocked by
a spend limit; the page treated the round as still open, reopened it with its time long gone, auto-submitted,
was refused and reopened it - enter/leave fullscreen each time, a flickering screen whose Log out couldn't be
reached, even after closing the tab). The page must treat it as handed in: next round offered, no request
loop, Log out works. The candidate is created through HR's upload - every seeded account is taken by
another browser test.
"""
import sqlite3
from datetime import datetime, timedelta

import httpx
import pytest
from playwright.sync_api import expect

from .conftest import HR, login

pytestmark = pytest.mark.e2e
EMAIL = "e2e-scoring-failed@example.com"


def _db(e2e_server, sql, *args):
    db = sqlite3.connect(e2e_server["log"].parent / "e2e.db")
    db.execute(sql, args)
    db.commit()
    db.close()


def _scoring_failed(e2e_server, round_number, *, expired):
    """What a failed background scoring leaves behind - optionally long past the round's time, as live."""
    started = datetime.utcnow() - timedelta(hours=3) if expired else datetime.utcnow()
    _db(e2e_server, """update submissions set status = 'scoring_failed', submitted_at = ?, started_at = ?,
                       scoring_error = 'This candidate has reached the AI spend limit'
                       where round_number = ? and archived = 0 and user_id = (select id from users where email = ?)""",
        datetime.utcnow().isoformat(sep=" "), started.isoformat(sep=" "), round_number, EMAIL)


def test_a_round_whose_scoring_failed_is_handed_in_and_never_loops(e2e_server, app_page):
    with httpx.Client(base_url=e2e_server["base_url"], timeout=30) as hr:
        hr.post("/auth/login", json={"identifier": HR[0], "password": HR[1]}).raise_for_status()
        up = hr.post("/hr/candidates/upload", files={"file": ("c.txt", f"email,exam_date\n{EMAIL},{datetime.utcnow():%Y-%m-%d}\n", "text/plain")})
        up.raise_for_status()
        row = up.json()["rows"][0]
    page = app_page
    login(page, (row["username"], row["password"]))

    # Round 1 handed in, its scoring failed: Round 2 is next (was: Round 1 reopened, Round 2 locked)
    page.click("text=Got it - Start Round 1")
    page.wait_for_timeout(500)
    _scoring_failed(e2e_server, 1, expired=False)
    page.reload()
    expect(page.locator("#r4a-intro-language-select")).to_be_visible()

    # Round 2 handed in, its scoring failed hours ago - the live case
    page.select_option("#r4a-intro-language-select", "python")
    page.click("#r4a-intro-start-btn")
    page.wait_for_timeout(800)
    _scoring_failed(e2e_server, 2, expired=True)
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    page.reload()
    page.wait_for_timeout(4000)                                     # the live loop went round several times a second
    loop = [u for u in requests if "/expire" in u or "/auto/submit" in u or "/tab-switch" in u]
    assert loop == [], loop
    assert sum("/candidate/submissions" in u for u in requests) <= 2, requests
    assert page.evaluate("document.fullscreenElement") is None
    expect(page.locator('button[onclick="loadRound(2)"] .tick-num')).to_have_text("✓")   # shown as done

    expect(page.locator("#round3-coding-intro-overlay")).to_be_visible()   # moved on: Round 3's own briefing, as for anyone

    page.evaluate("logout()")        # what the Log out button runs (Round 3's briefing sits over it, as for every candidate)
    expect(page.locator("#auth-screen")).to_be_visible()
    page.reload()
    expect(page.locator("#auth-screen")).to_be_visible()             # stays logged out - no loop comes back on reopening
