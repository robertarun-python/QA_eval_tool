"""
Time running out, in a real browser, for every round - the paths where the
page submits on its own. Two wrong-round bugs lived here (Round 2's and
Round 4's fallback expired the wrong round). Rather than wait 20 minutes a
round, the attempt's start time is moved back so a few seconds remain and the
page reloaded; the page's own timer then runs out and must close the round
itself (a reload after the deadline would let the server close it instead,
hiding a broken page - see the mutation check in the commit history). Uses candidate2,
separate from test_candidate_flow's candidate.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PASSWORDS, login

pytestmark = pytest.mark.e2e
CANDIDATE2 = ("candidate2@example.com", PASSWORDS["CANDIDATE2_PASSWORD"])


def _age_attempt(e2e_server, round_number, seconds_left=4):
    """Moves the attempt's start back so only `seconds_left` remain: the page
    loads with the round still open (the server doesn't close it first) and
    the page's OWN timer then runs out while the candidate watches."""
    db = sqlite3.connect(e2e_server["log"].parent / "e2e.db")
    sid, limit = db.execute("""select s.id, coalesce(s.time_limit_minutes_at_start, sc.time_limit_minutes)
                               from submissions s join scenarios sc on sc.id = s.scenario_id
                               where s.round_number = ? and s.status = 'in_progress'
                               and s.user_id = (select id from users where email = ?)""", (round_number, CANDIDATE2[0])).fetchone()
    started = datetime.utcnow() - timedelta(minutes=limit) + timedelta(seconds=seconds_left)
    db.execute("update submissions set started_at = ? where id = ?", (started.isoformat(sep=" "), sid))
    db.commit()
    db.close()


def _status(e2e_server, round_number):
    db = sqlite3.connect(e2e_server["log"].parent / "e2e.db")
    row = db.execute("""select status from submissions where round_number = ? and archived = 0
                        and user_id = (select id from users where email = ?)""", (round_number, CANDIDATE2[0])).fetchone()
    db.close()
    return row[0] if row else None


def _wait_closed(page, e2e_server, n):
    for _ in range(80):
        if _status(e2e_server, n) not in (None, "in_progress"):
            return _status(e2e_server, n)
        page.wait_for_timeout(250)
    return _status(e2e_server, n)


def test_every_round_closes_itself_when_time_runs_out(e2e_server, app_page):
    page = app_page
    login(page, CANDIDATE2)

    # Round 1: a half-written test case is submitted as it stands.
    page.click("text=Got it - Start Round 1")
    page.locator("#tc-rows tr .tc-title").first.fill("Half-written case")
    page.wait_for_timeout(2500)  # autosave
    _age_attempt(e2e_server, 1)
    page.reload()
    assert _wait_closed(page, e2e_server, 1) in ("submitted", "scored", "scoring_failed")
    assert _status(e2e_server, 2) is None  # ...and never a different round

    # Round 2: nothing selected, so the submit is refused and the fallback must expire ROUND 2.
    page.goto(e2e_server["base_url"])
    page.select_option("#r4a-intro-language-select", "python")
    page.click("#r4a-intro-start-btn")
    page.wait_for_timeout(800)
    _age_attempt(e2e_server, 2)
    page.reload()
    assert _wait_closed(page, e2e_server, 2) in ("submitted", "scored", "scoring_failed")

    # Round 3.
    page.goto(e2e_server["base_url"])
    page.click("text=Got it")
    page.wait_for_timeout(800)
    _age_attempt(e2e_server, 3)
    page.reload()
    assert _wait_closed(page, e2e_server, 3) in ("submitted", "scored", "scoring_failed")

    # Round 4: empty write-up, so the submit is refused and the fallback must expire ROUND 4.
    page.goto(e2e_server["base_url"])
    page.click("text=Got it - Start Round 4")
    page.wait_for_timeout(800)
    _age_attempt(e2e_server, 4)
    page.reload()
    assert _wait_closed(page, e2e_server, 4) in ("submitted", "scored", "scoring_failed")
    expect(page.locator('button[onclick="loadRound(4)"]')).to_be_visible()
