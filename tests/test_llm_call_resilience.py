"""
Resilience of the one place every Claude call goes through
(llm_service._call_claude / _parse_json_response), and of what happens when
a call still fails. Found live: candidate4's Round 2 "Ask AI" returned a
generic 502 twice - the first code-writing reply (a ~130-line file inside
JSON) was cut off at its 4096-token limit or hit the fixed 60s timeout, the
handler logged nothing, and the page showed the error below Submit where
nobody saw it. These pin the fix for that whole class of failure, not just
that one call. No API calls: the client is faked.
"""
import inspect
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest

from app.services import llm_service

from .test_round2_automation import _reach_automation_round, _select
from .conftest import HR_EMAIL, HR_PASSWORD, _login, _auth


class _FakeClient:
    """Stands in for anthropic.Anthropic: replies are (text, stop_reason)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        text, stop = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)] if text is not None else [], stop_reason=stop)


def _use(monkeypatch, *replies):
    client = _FakeClient(*replies)
    monkeypatch.setattr(llm_service, "_get_client", lambda: client)
    return client


# ---- cut-off replies ----

def test_complete_reply_is_returned_once(monkeypatch):
    client = _use(monkeypatch, ('{"ok": true}', "end_turn"))
    assert llm_service._call_claude("p", max_tokens=4096) == '{"ok": true}'
    assert len(client.calls) == 1 and client.calls[0]["max_tokens"] == 4096


def test_cut_off_reply_is_retried_once_with_double_the_limit(monkeypatch):
    client = _use(monkeypatch, ('{"code_after": "def test_', "max_tokens"), ('{"code_after": "ok"}', "end_turn"))
    assert llm_service._call_claude("p", max_tokens=4096) == '{"code_after": "ok"}'
    assert [c["max_tokens"] for c in client.calls] == [4096, 8192]


def test_still_cut_off_after_the_retry_raises_a_clear_error(monkeypatch):
    client = _use(monkeypatch, ('{"code_after": "def', "max_tokens"))
    with pytest.raises(llm_service.LLMReplyTruncated, match="cut off"):
        llm_service._call_claude("p", max_tokens=4096)
    assert len(client.calls) == 2  # one retry, never a loop


def test_no_retry_beyond_the_ceiling(monkeypatch):
    client = _use(monkeypatch, ("{", "max_tokens"))
    with pytest.raises(llm_service.LLMReplyTruncated):
        llm_service._call_claude("p", max_tokens=llm_service._MAX_OUTPUT_TOKENS)
    assert len(client.calls) == 1


def test_empty_reply_is_an_error_not_an_empty_string(monkeypatch):
    _use(monkeypatch, (None, "end_turn"))
    with pytest.raises(RuntimeError, match="empty reply"):
        llm_service._call_claude("p")


# ---- timeouts scale with the reply ----

def test_each_call_gets_a_timeout_sized_to_its_reply(monkeypatch):
    client = _use(monkeypatch, ("x", "end_turn"))
    llm_service._call_claude("p", max_tokens=1024)
    llm_service._call_claude("p", max_tokens=8192)
    assert client.calls[0]["timeout"] == 60.0              # short replies keep the old 60s
    assert client.calls[1]["timeout"] > 60.0               # long code-writing replies get more
    assert llm_service._timeout_for(10**6) == 240.0        # never unbounded


def test_code_writing_calls_ask_for_the_larger_limit():
    for fn in (llm_service.round2_automation_turn, llm_service._round3_coding_turn_once,
               llm_service.round3_syntax_fix, llm_service.generate_round3_reference):
        assert "max_tokens=_CODE_REPLY_TOKENS" in inspect.getsource(fn), fn.__name__
    assert llm_service._CODE_REPLY_TOKENS >= 8192


# ---- replies with stray text around the JSON ----

@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Looking at this instruction, it is a normal step.\n\n{"a": 1}', {"a": 1}),
    ('Here you go: [{"a": 1}, {"b": 2}] - done.', [{"a": 1}, {"b": 2}]),
    ('Sure. {"code_after": "if x:\n    y()"} Hope that helps.', {"code_after": "if x:\n    y()"}),
])
def test_json_is_read_even_with_text_around_it(raw, expected):
    assert llm_service._parse_json_response(raw) == expected


@pytest.mark.parametrize("raw", ["no json here", '{"a": "unterminated', ""])
def test_a_reply_with_no_whole_json_still_fails(raw):
    with pytest.raises(json.JSONDecodeError):
        llm_service._parse_json_response(raw)


# ---- failures are logged, and shown where the candidate is looking ----

def test_every_502_handler_logs_the_real_error():
    """Guard for the whole class: a new endpoint that turns an exception into
    a generic 502 without logging it fails here."""
    routers = Path(__file__).parent.parent / "backend" / "app" / "routers"
    missing = []
    for path in routers.glob("*.py"):
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if "raise HTTPException(502" in line and not any("traceback.print_exc()" in l for l in lines[max(0, i - 3):i]):
                missing.append(f"{path.name}:{i + 1}")
    assert not missing, f"502 without logging the cause: {missing}"


def test_background_scoring_failure_is_logged():
    from app.services import scoring_service
    assert "traceback.print_exc()" in inspect.getsource(scoring_service)


def test_round2_ask_ai_failure_is_logged_and_reported(client, monkeypatch, capsys):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token)

    def boom(**kwargs):
        raise llm_service.LLMReplyTruncated("The model's reply was cut off at its 8192-token limit.")

    monkeypatch.setattr(llm_service, "round2_automation_clarify", boom)
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode step 1", "row_index": 0}, cookies=_auth(cand_token))
    assert res.status_code == 502
    assert "had trouble responding" in res.json()["detail"]
    assert "LLMReplyTruncated" in capsys.readouterr().err  # the real cause reaches the server log


def test_round2_shows_ai_errors_beside_the_test_case():
    from .page_js import page_js
    js = page_js()
    assert 'id="r4a-tc-status-${row.index}"' in js
    assert re.search(r'auto/turn".*?\),\s*rowIndex, "Asking the assistant', js, re.S)


# ---- replies that aren't valid JSON: one corrective retry ----

def test_valid_json_is_one_call(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: (calls.append(prompt), '{"a": 1}')[1])
    assert llm_service._call_claude_json("p") == {"a": 1}
    assert len(calls) == 1


def test_invalid_json_gets_one_retry_with_a_correction_note(monkeypatch, capsys):
    replies = ['{"code_after": "def t():\n    """doc"""\n"}', '{"code_after": "ok"}']
    calls = []
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: (calls.append((prompt, max_tokens)), replies[len(calls) - 1])[1])
    assert llm_service._call_claude_json("THE PROMPT", max_tokens=8192) == {"code_after": "ok"}
    assert len(calls) == 2
    assert calls[1][0].startswith("THE PROMPT") and "could not be read as JSON" in calls[1][0]
    assert "each double quote as" in calls[1][0] and calls[1][1] == 8192  # same limit on the retry
    assert "[llm] unparseable reply" in capsys.readouterr().err  # what the model sent is logged


def test_invalid_json_twice_still_fails_after_one_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: (calls.append(1), '{"a": "x" "b": 1}')[1])
    with pytest.raises(json.JSONDecodeError):
        llm_service._call_claude_json("p")
    assert len(calls) == 2


def test_every_json_call_site_goes_through_the_retry():
    """Guard: a new call that parses a reply directly would skip the retry."""
    services = Path(__file__).parent.parent / "backend" / "app" / "services"
    offenders = []
    for path in services.glob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if "_parse_json_response(" in line and "def _parse_json_response" not in line:
                offenders.append(f"{path.name}:{i}")
    # the only direct uses are inside _call_claude_json itself
    assert all(o.startswith("llm_service.py:") for o in offenders) and len(offenders) == 2, offenders


def test_round2_ask_ai_recovers_from_a_broken_reply(client, monkeypatch):
    """Today's failure end to end: the first code-writing reply is broken
    JSON (as candidate4 got twice); the retry's reply is fine - Ask AI works."""
    from .test_round2_automation import _sequential_call_claude, _SUFFICIENT
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token)
    broken = '{\n  "response_kind": "code_edit",\n  "response_message": "Wrote it",\n  "code_after": "def test():\n    """x"""\n"\n  "planted_flaw": "y"\n}'
    good = json.dumps({"response_kind": "code_edit", "response_message": "Wrote it", "code_after": "def test():\n    pass\n"})
    calls = _sequential_call_claude(monkeypatch, _SUFFICIENT, broken, good)
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode step 1", "row_index": 0}, cookies=_auth(cand_token))
    assert res.status_code == 201, res.text
    assert res.json()["response_kind"] == "code_edit"
    assert calls["n"] >= 3  # gate, broken reply, corrected reply


# ---- every call is logged (AI health) ----

class _Usage:
    input_tokens = 1200
    output_tokens = 340


def _reply(text=None, stop="end_turn", tool_input=None):
    content = [SimpleNamespace(type="tool_use", name="submit_reply", input=tool_input)] if tool_input is not None else \
              ([SimpleNamespace(type="text", text=text)] if text is not None else [])
    return SimpleNamespace(content=content, stop_reason=stop, usage=_Usage())


class _ScriptedClient:
    """Returns scripted replies in order; each may be an exception to raise."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_each_call_is_logged_with_its_caller_and_outcome(monkeypatch, capsys):
    monkeypatch.setattr(llm_service, "_get_client", lambda: _ScriptedClient(_reply('{"a"', stop="max_tokens"), _reply('{"a": 1}')))
    llm_service._CALL_LOG.clear()
    llm_service._call_claude_json("p")
    log = llm_service.recent_calls()
    assert [c["outcome"] for c in log] == ["ok", "cut_off_retrying"]  # newest first
    assert log[0]["caller"] == "test_each_call_is_logged_with_its_caller_and_outcome"
    assert log[0]["input_tokens"] == 1200 and log[0]["output_tokens"] == 340 and log[0]["ms"] is not None
    assert "[llm] " in capsys.readouterr().err
    assert "prompt" not in log[0] and "reply" not in log[0]  # metadata only


def test_invalid_json_and_invalid_replies_are_logged(monkeypatch):
    llm_service._CALL_LOG.clear()
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: "{}")
    with pytest.raises(ValueError):
        llm_service.score_round3_coding(scenario_description="d", expected_approach="e", conversation_so_far=[], test_results=[])
    assert llm_service.recent_calls()[0]["outcome"] == "invalid_reply"


def test_hr_ai_health_reports_problems(client, monkeypatch):
    llm_service._CALL_LOG.clear()
    monkeypatch.setattr(llm_service, "_get_client", lambda: _ScriptedClient(_reply(None)))
    with pytest.raises(RuntimeError):
        llm_service._call_claude("p")
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    health = client.get("/hr/ai-health", cookies=_auth(hr_token)).json()
    assert health["total"] == 1 and health["by_outcome"] == {"empty_reply": 1}
    assert health["recent_problems"][0]["outcome"] == "empty_reply"


def test_ai_health_is_hr_only(client):
    from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert client.get("/hr/ai-health", cookies=_auth(cand_token)).status_code == 403


# ---- tool-use output for code-writing calls (settings.llm_tool_output) ----

CODE_WITH_QUOTES = 'def test_login():\n    """Checks "Welcome, Jordan" shows."""\n    assert page["title"] == "Home"\n'


def test_tool_output_returns_code_without_json_escaping(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "llm_tool_output", True)
    monkeypatch.setattr(llm_service, "_tool_output_disabled_reason", None)
    client = _ScriptedClient(_reply(stop="tool_use", tool_input={"response_kind": "code_edit", "response_message": "Wrote it",
                                                                  "code_after": CODE_WITH_QUOTES, "planted_flaw": "x"}))
    monkeypatch.setattr(llm_service, "_get_client", lambda: client)
    result = llm_service._call_claude_json("p", max_tokens=8192, schema=llm_service.SCHEMA_CODE_TURN)
    assert result["code_after"] == CODE_WITH_QUOTES and result["planted_flaw"] == "x"
    assert client.calls[0]["tool_choice"] == {"type": "tool", "name": "submit_reply"}
    assert client.calls[0]["tools"][0]["input_schema"] is llm_service.SCHEMA_CODE_TURN
    # AI health names the function that asked, not the tool-call helper (found in the live run).
    assert llm_service.recent_calls()[0]["caller"] == "test_tool_output_returns_code_without_json_escaping"


def test_tool_output_is_off_by_default(monkeypatch):
    from app.config import settings
    assert settings.llm_tool_output is False
    client = _ScriptedClient(_reply('{"response_kind": "explain", "response_message": "ok"}'))
    monkeypatch.setattr(llm_service, "_get_client", lambda: client)
    llm_service._call_claude_json("p", schema=llm_service.SCHEMA_CODE_TURN)
    assert "tools" not in client.calls[0]


def test_tool_output_falls_back_to_json_text_if_the_api_rejects_it(monkeypatch):
    import anthropic
    import httpx
    from app.config import settings
    monkeypatch.setattr(settings, "llm_tool_output", True)
    monkeypatch.setattr(llm_service, "_tool_output_disabled_reason", None)
    rejected = anthropic.BadRequestError("tools not supported", response=httpx.Response(400, request=httpx.Request("POST", "http://api")), body=None)
    client = _ScriptedClient(rejected, _reply('{"response_kind": "explain", "response_message": "ok"}'),
                             _reply('{"response_kind": "explain", "response_message": "again"}'))
    monkeypatch.setattr(llm_service, "_get_client", lambda: client)
    assert llm_service._call_claude_json("p", schema=llm_service.SCHEMA_CODE_TURN)["response_message"] == "ok"
    assert llm_service._call_claude_json("p", schema=llm_service.SCHEMA_CODE_TURN)["response_message"] == "again"
    assert "tools" in client.calls[0] and "tools" not in client.calls[1] and "tools" not in client.calls[2]  # tried once, then off


def test_code_writing_calls_pass_their_schema():
    for fn, schema in ((llm_service.round2_automation_turn, "SCHEMA_CODE_TURN"), (llm_service._round3_coding_turn_once, "SCHEMA_CODE_TURN"),
                       (llm_service.round3_syntax_fix, "SCHEMA_SYNTAX_FIX"),
                       (llm_service.generate_round3_reference, "SCHEMA_R3_REFERENCE")):
        assert f"schema={schema}" in inspect.getsource(fn), fn.__name__
