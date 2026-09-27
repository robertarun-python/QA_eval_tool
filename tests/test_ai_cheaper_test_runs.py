"""Cheaper test and measurement runs (llm_service._send), off for the live
tool: a request identical to an earlier one is answered from its saved reply
for free (ai_reuse_replies), and calls can go through the Message Batches API
at half price (ai_batch_jobs). The owner's test budget went on repeat builds
whose requests had mostly not changed (2026-09-27)."""
import json
from types import SimpleNamespace

import anthropic
import pytest

from app.services import llm_service


def _message(text="ok"):
    return anthropic.types.Message.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-4-5", "stop_reason": "end_turn",
        "stop_sequence": None, "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    log = tmp_path / "ai_calls.jsonl"
    monkeypatch.setattr(llm_service.settings, "ai_call_log_path", str(log))
    monkeypatch.setattr(llm_service.settings, "claude_model", "claude-sonnet-4-5")
    monkeypatch.setattr(llm_service, "_MONTH_SPEND", {"month": None, "usd": 0.0})
    monkeypatch.setattr(llm_service.time, "sleep", lambda s: None)
    sent, batches = [], []

    class Batches:
        def __init__(self):
            self.polls = 0

        def create(self, requests):
            batches.append(requests)
            return SimpleNamespace(id="b1", processing_status="in_progress")

        def retrieve(self, batch_id):
            self.polls += 1
            return SimpleNamespace(id=batch_id, processing_status="ended" if self.polls >= 2 else "in_progress")

        def results(self, batch_id):
            return [SimpleNamespace(custom_id="call", result=SimpleNamespace(type="succeeded", message=_message("from batch")))]

        def cancel(self, batch_id):
            batches.append("cancelled")

    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: sent.append(kw) or _message(), batches=Batches()))
    monkeypatch.setattr(llm_service, "_get_client", lambda: client)

    def costs():
        return [json.loads(line)["cost_usd"] for line in log.read_text().splitlines()]
    return SimpleNamespace(sent=sent, batches=batches, costs=costs, client=client)


def test_both_are_off_for_the_live_tool():
    assert llm_service.settings.ai_reuse_replies is False and llm_service.settings.ai_batch_jobs is False


def test_an_identical_request_is_answered_free_from_its_saved_reply(setup, monkeypatch):
    monkeypatch.setattr(llm_service.settings, "ai_reuse_replies", True)
    assert llm_service._call_claude("same") == "ok"
    assert llm_service._call_claude("same") == "ok"
    assert llm_service._call_claude("changed") == "ok"
    assert len(setup.sent) == 2, "the repeat must not reach the AI"
    assert setup.costs() == [3.0, 0.0, 3.0]


def test_without_reuse_every_request_is_sent(setup):
    llm_service._call_claude("same")
    llm_service._call_claude("same")
    assert len(setup.sent) == 2


def test_a_batch_call_costs_half_and_returns_the_reply(setup, monkeypatch):
    monkeypatch.setattr(llm_service.settings, "ai_batch_jobs", True)
    assert llm_service._call_claude("p") == "from batch"
    assert setup.sent == [] and setup.batches[0][0]["params"]["messages"][0]["role"] == "user"
    assert "timeout" not in setup.batches[0][0]["params"]
    assert setup.costs() == [1.5]


def test_a_batch_not_answered_in_time_is_cancelled(setup, monkeypatch):
    monkeypatch.setattr(llm_service.settings, "ai_batch_jobs", True)
    monkeypatch.setattr(llm_service.settings, "ai_batch_max_wait_seconds", 0)
    with pytest.raises(TimeoutError):
        llm_service._call_claude("p")
    assert setup.batches[-1] == "cancelled"
