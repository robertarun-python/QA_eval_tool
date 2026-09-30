"""
The AI spending guardrails beside the monthly limit (owner, 2026-09-28): a day's spend, and one
candidate's spend and number of AI calls in one round. Checked before EVERY request to the model -
retries included - so no loop can get past them; a blocked call is logged and never sent or paid; the
candidate is told the assistant is paused and their work is saved; HR sees which limit and changes it on
the AI health card. The AI is a fake that would charge per call - no real API call is ever made.
"""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import llm_service
from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login
from .test_round2_typist_endpoints import _environment_round

CANDIDATE = dict(round_number=2, scenario_id=1, submission_id=5, user_id=9)


@pytest.fixture
def paid(tmp_path, monkeypatch):
    return make_paid(tmp_path, monkeypatch)


def make_paid(tmp_path, monkeypatch):
    """A call record and a fake model: $0.30 a call (Sonnet 4.5, 20,000 output tokens), or a cut-off reply."""
    path = tmp_path / "ai_calls.jsonl"
    monkeypatch.setattr(llm_service.settings, "ai_call_log_path", str(path))
    monkeypatch.setattr(llm_service.settings, "claude_model", "claude-sonnet-4-5")
    monkeypatch.setattr(llm_service.settings, "llm_fake_mode", False)
    monkeypatch.setattr(llm_service.settings, "ai_monthly_limit_usd", 12.0)
    monkeypatch.setattr(llm_service.settings, "ai_daily_limit_usd", 3.0)
    monkeypatch.setattr(llm_service.settings, "ai_candidate_round_limit_usd", 0.75)
    monkeypatch.setattr(llm_service.settings, "ai_candidate_round_calls", 60)
    monkeypatch.setattr(llm_service.settings, "ai_reuse_replies", False)
    monkeypatch.setattr(llm_service.settings, "ai_batch_jobs", False)
    monkeypatch.setattr(llm_service, "_MONTH_SPEND", {"month": None, "usd": 0.0})
    monkeypatch.setattr(llm_service, "_SPEND", {"day": None, "day_usd": 0.0, "candidates": {}})
    llm_service._CALL_LOG.clear()
    sent, script = [], {"stop": "end_turn", "tokens": 20_000}

    def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"ok": 1}')], stop_reason=script["stop"],
                               usage=SimpleNamespace(input_tokens=0, output_tokens=script["tokens"], cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))
    monkeypatch.setattr(llm_service, "_get_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create),
                                                                            with_options=lambda **k: SimpleNamespace(messages=SimpleNamespace(create=create))))

    def spent_before(usd, *, calls=1, day=None, **ctx):
        at = (day or datetime.utcnow().strftime("%Y-%m-%d")) + "T00:00:01Z"
        with path.open("a") as f:
            for _ in range(calls):
                f.write(json.dumps({"at": at, "outcome": "ok", "cost_usd": usd / calls, **ctx}) + "\n")
    return SimpleNamespace(sent=sent, script=script, spent_before=spent_before, path=path)


def _blocked_records(paid):
    return [json.loads(line) for line in paid.path.read_text().splitlines() if '"limit_reached"' in line or '"budget_reached"' in line]


# a) per-round dollar limit
def test_a_candidate_is_stopped_at_the_round_spend_limit(paid):
    paid.spent_before(0.60, **CANDIDATE)
    with llm_service.call_context(**CANDIDATE):
        llm_service._call_claude("p")                       # 0.60 -> 0.90
        with pytest.raises(llm_service.AILimitReached) as stop:
            llm_service._call_claude("p")
    assert stop.value.limit == "candidate_round_usd" and len(paid.sent) == 1
    with llm_service.call_context(**{**CANDIDATE, "round_number": 3}):   # another round has its own allowance
        llm_service._call_claude("p")
    with llm_service.call_context(**{**CANDIDATE, "user_id": 10, "submission_id": 6}):   # another candidate too
        llm_service._call_claude("p")
    assert len(paid.sent) == 3


# b) per-round call-count limit
def test_a_candidate_is_stopped_at_the_round_call_limit(paid):
    paid.script["tokens"] = 1                              # nearly free calls - the count stops them, not the dollars
    paid.spent_before(0.01, calls=59, **CANDIDATE)
    with llm_service.call_context(**CANDIDATE):
        llm_service._call_claude("p")                       # call 60
        with pytest.raises(llm_service.AILimitReached) as stop:
            llm_service._call_claude("p")                   # call 61
    assert stop.value.limit == "candidate_round_calls" and len(paid.sent) == 1


# c) daily limit
def test_calls_stop_at_the_daily_limit_and_yesterday_does_not_count(paid):
    paid.spent_before(50.0, day="2000-01-01")               # another day (and month) entirely
    paid.spent_before(2.80)
    llm_service._call_claude("p")                           # 2.80 -> 3.10
    with pytest.raises(llm_service.AILimitReached) as stop:
        llm_service._call_claude("p")
    assert stop.value.limit == "daily" and len(paid.sent) == 1


def test_a_candidate_in_a_test_keeps_the_ten_percent_grace_on_the_daily_limit(paid):
    paid.spent_before(3.0)
    with pytest.raises(llm_service.AILimitReached):
        llm_service._call_claude("p")                       # new work (HR, a build) stops at $3.00
    with llm_service.call_context(**CANDIDATE):
        llm_service._call_claude("p")                       # a candidate mid-test: 3.00 < 3.30
    assert len(paid.sent) == 1


# d) the monthly limit is unchanged
def test_the_monthly_limit_and_its_grace_are_unchanged(paid, monkeypatch):
    monkeypatch.setattr(llm_service.settings, "ai_daily_limit_usd", 1000.0)
    paid.spent_before(11.9, day=datetime.utcnow().strftime("%Y-%m") + "-01")
    llm_service._call_claude("p")                           # 11.90 -> 12.20
    with pytest.raises(llm_service.AIBudgetReached, match=r"\$12\.00") as stop:
        llm_service._call_claude("p")
    assert not isinstance(stop.value, llm_service.AILimitReached) and stop.value.limit == "monthly"
    with llm_service.call_context(**{**CANDIDATE, "user_id": 11}):
        llm_service._call_claude("p")                       # 12.20 < 13.20: the 10% grace for a candidate in a test
    assert llm_service.recent_calls()[1]["outcome"] == "budget_reached"


# e) + f) checked before every retry; a retry chain stops at the limit
def test_the_cut_off_retry_is_checked_too(paid):
    paid.spent_before(0.60, **CANDIDATE)
    paid.script["stop"] = "max_tokens"                      # the reply is cut off -> _call_claude retries it
    with llm_service.call_context(**CANDIDATE), pytest.raises(llm_service.AILimitReached):
        llm_service._call_claude("p")                       # first request 0.60 -> 0.90; the retry is blocked
    assert len(paid.sent) == 1


def test_a_chain_of_redrafts_stops_at_the_limit(paid):
    """The Round 2 assistant redrafts up to three times, then may fix a compile error: every one checks first."""
    from app.services import round2_typist
    paid.spent_before(0.10, **CANDIDATE)
    leak = {"reply": "Should the booking be BK-009?", "code": None}   # blocked by the guard -> redrafted each time
    before = len(paid.sent)
    with llm_service.call_context(**CANDIDATE):
        orig = llm_service._call_claude

        def claude(prompt, max_tokens=4096):
            orig(prompt, max_tokens)                        # the real path: limit check, the fake paid request, the record
            return json.dumps(leak)
        llm_service._call_claude, restore = claude, orig
        try:
            with pytest.raises(llm_service.AILimitReached):
                for _ in range(10):                          # the candidate keeps sending
                    round2_typist.turn("python", {"title": "x"}, [], "", "Borrow a book")
        finally:
            llm_service._call_claude = restore
    assert (len(paid.sent) - before) * 0.30 + 0.10 < 0.75 + 0.30, "no request after the limit was reached"


def test_the_json_retry_and_the_tool_path_are_checked(paid):
    paid.spent_before(0.75, **CANDIDATE)
    with llm_service.call_context(**CANDIDATE):
        with pytest.raises(llm_service.AILimitReached):
            llm_service._call_claude_json("p")
        with pytest.raises(llm_service.AILimitReached):
            llm_service._call_claude_tool("p", {"type": "object", "properties": {}})
    assert paid.sent == []


# g) + j) a blocked call is logged, and never reaches the AI
def test_a_blocked_call_is_logged_with_who_which_round_which_limit_and_when(paid):
    paid.spent_before(0.75, **CANDIDATE)
    with llm_service.call_context(**CANDIDATE), pytest.raises(llm_service.AILimitReached):
        llm_service._call_claude("p")
    assert paid.sent == []
    [record] = _blocked_records(paid)
    assert record["outcome"] == "limit_reached" and record["limit"] == "candidate_round_usd"
    assert (record["user_id"], record["round_number"]) == (9, 2) and record["at"].endswith("Z") and "$0.75" in record["detail"]
    assert record["cost_usd"] == 0.0
    # blocked records are not calls: they neither cost nor count toward any limit
    assert llm_service._spend_now()["candidates"][(9, 2, 5)] == [pytest.approx(0.75), 1]


# h) + i) the candidate's work is saved and the messages are clear
def test_the_candidate_is_told_the_assistant_is_paused_and_their_work_stays(client, monkeypatch, paid):
    cand = _environment_round(client, monkeypatch)
    monkeypatch.setattr(llm_service, "_call_claude_json",
                        lambda prompt, max_tokens=None, schema=None: {"reply": "Noted: log in as jordan.", "code": None})
    first = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "Log in as jordan", "row_index": 0}, cookies=_auth(cand))
    assert first.status_code == 201, first.text

    def limit_reached(*a, **k):
        llm_service._blocked("candidate_round_usd", "test")
        raise llm_service.AILimitReached("candidate_round_usd", "This candidate has reached the AI spend limit for round 2.")
    monkeypatch.setattr(llm_service, "_call_claude_json", limit_reached)
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "Click Borrow", "row_index": 0}, cookies=_auth(cand))
    assert res.status_code == 429
    message = res.json()["detail"]
    assert "paused" in message and "saved" in message and "HR" in message and "$" not in message
    state = client.get("/candidate/round/2/auto/state", cookies=_auth(cand)).json()
    turns = state["tc_state"][0]["turns"]
    assert [t["candidate_prompt"] for t in turns] == ["Log in as jordan"]   # the earlier work is all there, nothing half-written


def test_hr_sees_which_limit_was_reached_and_changes_the_limits(client, paid):
    hr = _auth(_login(client, HR_EMAIL, HR_PASSWORD))
    paid.spent_before(0.75, **CANDIDATE)
    with llm_service.call_context(**CANDIDATE), pytest.raises(llm_service.AILimitReached):
        llm_service._call_claude("p")
    lasting = client.get("/hr/ai-health", cookies=hr).json()["lasting"]
    assert lasting["limits"] == {"monthly_usd": 12.0, "daily_usd": 3.0, "candidate_round_usd": 0.75, "candidate_round_calls": 60}
    [reached] = lasting["limits_reached"]
    assert reached["limit"] == "candidate_round_usd" and reached["round_number"] == 2
    assert lasting["limit_labels"]["candidate_round_usd"] == "AI spend per candidate per round (US$)"
    assert lasting["calls"] == 1                           # the blocked call isn't counted as a call
    res = client.put("/hr/ai-budget", json={"candidate_round_usd": 1.5, "candidate_round_calls": 80, "daily_usd": 5}, cookies=hr)
    assert res.status_code == 200 and res.json()["limits"]["candidate_round_usd"] == 1.5
    assert res.json()["limits"]["monthly_usd"] == 12.0     # the others keep their values
    with llm_service.call_context(**CANDIDATE):
        llm_service._call_claude("p")                       # raised: the candidate may continue
    for bad in ({"daily_usd": -1}, {"candidate_round_calls": "5"}, {"nope": 1}, {}, {"daily_usd": True}):
        assert client.put("/hr/ai-budget", json=bad, cookies=hr).status_code == 400, bad


def test_the_hr_screen_shows_the_limits_and_what_was_reached():
    from .page_js import page_js
    js = page_js()
    assert "limits_reached" in js and "ai-limit-input" in js and "Limits reached recently" in js
