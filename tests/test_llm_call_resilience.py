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

from .test_round4_auto import _reach_automation_round, _select
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
    for fn in (llm_service.round4_auto_turn, llm_service._round3_coding_turn_once, llm_service.round4_pilot_turn,
               llm_service.round3_syntax_fix, llm_service._round4_force_flaw, llm_service.generate_round3_reference):
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

    monkeypatch.setattr(llm_service, "round4_auto_clarify", boom)
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode step 1", "row_index": 0}, cookies=_auth(cand_token))
    assert res.status_code == 502
    assert "had trouble responding" in res.json()["detail"]
    assert "LLMReplyTruncated" in capsys.readouterr().err  # the real cause reaches the server log


def test_round2_shows_ai_errors_beside_the_test_case():
    js = (Path(__file__).parent.parent / "backend" / "app" / "static" / "app.js").read_text()
    assert 'id="r4a-tc-status-${row.index}"' in js
    assert re.search(r'auto/turn".*?\),\s*rowIndex, "Asking the assistant', js, re.S)
