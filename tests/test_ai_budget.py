"""The monthly AI spending limit (llm_service.check_budget): the owner pays for
every call from a limited income and once spent a whole day's budget in a
morning without noticing (2026-09-27). Once this month's recorded spend
reaches the limit no new AI call is made or paid; a candidate already in a
test may finish (10% grace); HR sees and sets the limit on the AI health card."""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import llm_service
from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login


@pytest.fixture
def paid(tmp_path, monkeypatch):
    """A recorded month and an AI that would charge $1.50 per call."""
    path = tmp_path / "ai_calls.jsonl"
    monkeypatch.setattr(llm_service.settings, "ai_call_log_path", str(path))
    monkeypatch.setattr(llm_service.settings, "claude_model", "claude-sonnet-4-5")
    monkeypatch.setattr(llm_service.settings, "ai_monthly_limit_usd", 10.0)
    monkeypatch.setattr(llm_service, "_MONTH_SPEND", {"month": None, "usd": 0.0})
    monkeypatch.setattr(llm_service, "_SPEND", {"day": None, "day_usd": 0.0, "candidates": {}})
    llm_service._CALL_LOG.clear()
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=0, output_tokens=100_000, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    monkeypatch.setattr(llm_service, "_get_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))

    def spent_before(usd, month=None):
        at = (month or datetime.utcnow().strftime("%Y-%m")) + "-01T00:00:00Z"
        with path.open("a") as f:
            f.write(json.dumps({"at": at, "outcome": "ok", "cost_usd": usd}) + "\n")
    return SimpleNamespace(calls=calls, spent_before=spent_before, path=path)


def test_calls_stop_once_the_month_limit_is_reached(paid):
    paid.spent_before(7.0)
    llm_service._call_claude("p")          # 7.00 -> 8.50
    llm_service._call_claude("p")          # 8.50 -> 10.00
    with pytest.raises(llm_service.AIBudgetReached, match=r"\$10\.00"):
        llm_service._call_claude("p")
    assert len(paid.calls) == 2, "the refused call must never reach the AI (never paid)"
    assert llm_service.recent_calls()[0]["outcome"] == "budget_reached"


def test_last_month_does_not_count(paid):
    paid.spent_before(50.0, month="2000-01")
    assert llm_service._call_claude("p") == "ok"


def test_a_candidate_in_a_test_may_finish_within_the_grace(paid):
    paid.spent_before(10.5)
    with pytest.raises(llm_service.AIBudgetReached):
        llm_service._call_claude("p")      # new work (a build, HR generation): refused
    with llm_service.call_context(round_number=2, submission_id=5, user_id=9):
        assert llm_service._call_claude("p") == "ok"   # 10.50 < 11.00
        with pytest.raises(llm_service.AIBudgetReached):
            llm_service._call_claude("p")  # 12.00 - past the grace too


def test_the_tool_call_path_is_limited_too(paid):
    paid.spent_before(10.0)
    with pytest.raises(llm_service.AIBudgetReached):
        llm_service._call_claude_tool("p", {"type": "object", "properties": {}})
    assert paid.calls == []


def test_hr_sees_and_sets_the_limit(paid, client):
    hr = _auth(_login(client, HR_EMAIL, HR_PASSWORD))
    paid.spent_before(2.25)
    lasting = client.get("/hr/ai-health", cookies=hr).json()["lasting"]
    assert (lasting["month_usd"], lasting["monthly_limit_usd"]) == (2.25, 10.0)
    assert client.put("/hr/ai-budget", json={"monthly_usd": 2}, cookies=hr).json()["monthly_limit_usd"] == 2.0
    with pytest.raises(llm_service.AIBudgetReached):
        llm_service._call_claude("p")      # 2.25 >= 2.00
    for bad in (-1, "5", True, None, 20000):
        assert client.put("/hr/ai-budget", json={"monthly_usd": bad}, cookies=hr).status_code == 400
    client.cookies.clear()
    assert client.put("/hr/ai-budget", json={"monthly_usd": 5}).status_code in (401, 403)  # not signed in as HR


def test_a_damaged_limit_file_falls_back_to_the_default(paid):
    llm_service._budget_path().write_text("{not json")
    assert llm_service.monthly_limit_usd() == 10.0


def test_a_candidate_cannot_change_the_limit(paid, client):
    from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD
    cand = _auth(_login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD))
    client.cookies.clear()
    assert client.put("/hr/ai-budget", json={"monthly_usd": 500}, cookies=cand).status_code == 403
    assert llm_service.monthly_limit_usd() == 10.0


def test_a_build_stopped_by_the_limit_tells_hr_plainly(paid):
    from app.services.practice_app import engine_build
    paid.spent_before(10.0)
    result = engine_build.generate("Library", "A library app", [{"id": "TC-1", "title": "Borrow a book", "steps": "borrow", "expected_result": "ok"}])
    assert result.error.startswith("This month's AI spending limit ($10.00) has been reached")
    assert paid.calls == []
