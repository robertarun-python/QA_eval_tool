"""
Round 2 Submit with two selected test cases, only one of them on screen.

The bug this guards (synthetic-candidate pilot, 2026-10-04): only the test
case on screen has a code box, so Submit built the other test case's entry
from a box that wasn't there - code "" - and the server's min_length=1 turned
every Submit into a 422 the candidate barely saw. Round 2 could never be
handed in once two test cases were selected.

The page's real submit function runs in a headless browser on the real round
state; the request it builds goes to the real endpoint. Offline: no model call.
"""
import re

import pytest

from app.services import llm_service

from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login
from .page_js import page_js
from .test_round2_automation import _reach_automation_round, _select, _state, _unlock

sync_api = pytest.importorskip("playwright.sync_api")

SUBMIT_FUNCTIONS = ("round2AutomationCode", "round2AutomationTcState", "round2AutomationTcIsUnlocked",
                    "round2AutomationSubmitClicked")

# Everything else the submit function touches, stubbed: `api` records the request instead of sending it.
STUBS = """
let round2AutomationState = null;
let round2AutomationBusy = false;
const r4aFixedPrefix = {};
let sentRequest = null;
function round2AutomationSetBusy() {}
function round2AutomationSwitchTestCase() {}
function stopTimer() {}
function disarmTabGuard() {}
function refreshCandidateNav() {}
async function api(path, opts) { sentRequest = { path, body: JSON.parse(opts.body) }; return {}; }
"""


def _function(js: str, name: str) -> str:
    start = re.search(rf"(async )?function {name}\(", js).start()
    nxt = re.search(r"\n(async )?function ", js[start + 1:])
    return js[start:start + 1 + nxt.start()] if nxt else js[start:]


def _request_the_page_sends(state, on_screen, editor_text):
    """Clicks Submit in a page showing only `on_screen`'s code box, holding `editor_text`."""
    js = page_js()
    script = STUBS + "\n".join(_function(js, name) for name in SUBMIT_FUNCTIONS)
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(f'<p id="r4a-status"></p><textarea id="r4a-code-{on_screen}"></textarea>')
            page.add_script_tag(content=script)
            page.evaluate("([state, id, text]) => { round2AutomationState = state; document.getElementById(id).value = text; }",
                          [state, f"r4a-code-{on_screen}", editor_text])
            page.evaluate("() => round2AutomationSubmitClicked()")
            return page.evaluate("() => ({ request: sentRequest, status: document.getElementById('r4a-status').textContent })")
        finally:
            browser.close()


def _two_test_cases_generated_and_run(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0, 1))
    for i in (0, 1):
        _unlock(client, monkeypatch, cand_token, row_index=i, code=f"print('tc{i}')")
        res = client.post("/candidate/round/2/auto/run", json={"code": f"print('tc{i}')", "row_index": i}, cookies=_auth(cand_token))
        assert res.status_code == 201, res.text
    monkeypatch.setattr(llm_service, "score_round2_automation_conversation", lambda **k: {
        "scores": {"automation_design": 20, "test_data_and_assertions": 20, "ai_usage": 20,
                   "ai_output_review": 20, "execution_and_validation": 20},
        "final_score": 100, "findings": [], "feedback_text": "ok",
    })
    return cand_token


@pytest.mark.parametrize("on_screen", [0, 1])
def test_submit_sends_every_selected_test_cases_code_and_the_server_accepts_it(client, monkeypatch, on_screen):
    cand_token = _two_test_cases_generated_and_run(client, monkeypatch)
    state = _state(client, cand_token)
    hidden = 1 - on_screen
    saved = {tc["row_index"]: tc["code"] for tc in state["tc_state"]}
    assert saved[hidden] == f"print('tc{hidden}')"

    edited = f"print('tc{on_screen} edited on screen')"
    sent = _request_the_page_sends(state, on_screen, edited)

    assert sent["status"] == ""  # no "generate code first" / "run it first" message
    assert sent["request"]["path"] == "/candidate/round/2/auto/submit"
    entries = {e["row_index"]: e["code"] for e in sent["request"]["body"]["entries"]}
    assert entries == {on_screen: edited, hidden: saved[hidden]}  # the hidden one's saved code, not ""

    res = client.post(sent["request"]["path"], json=sent["request"]["body"], cookies=_auth(cand_token))
    assert res.status_code == 201, res.text
    stored = {row["index"]: row["code"] for row in res.json()["content"]["selected"]}
    assert stored == {on_screen: edited, hidden: saved[hidden]}


def test_the_empty_code_the_old_page_sent_is_still_refused(client, monkeypatch):
    """The server side is unchanged: an empty code value is still a 422 - the page must not send one."""
    cand_token = _two_test_cases_generated_and_run(client, monkeypatch)
    res = client.post("/candidate/round/2/auto/submit", json={"entries": [
        {"row_index": 0, "code": "print('tc0')"}, {"row_index": 1, "code": ""},
    ]}, cookies=_auth(cand_token))
    assert res.status_code == 422
