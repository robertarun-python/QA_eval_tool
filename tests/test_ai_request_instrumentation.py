"""
Request- and call-level cost records (owner, 2026-09-30): every Round 2 candidate message gets a request_id,
and each request it makes to the model is recorded with its own call_id, call_type, tokens, cost and success,
from the figures the call log already had. Labels only - the same calls, prompts and answers. A scripted fake
client stands in for Claude; no API call.
"""
import json
from types import SimpleNamespace

import pytest

from app.services import llm_service, round2_typist
from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login
from .test_round2_typist_endpoints import _environment_round


@pytest.fixture
def claude(tmp_path, monkeypatch):
    """A fake Claude answering from a script (text, stop reason); every request goes through the real gateway."""
    path = tmp_path / "calls.jsonl"
    for name, value in (("ai_call_log_path", str(path)), ("claude_model", "claude-sonnet-4-5"), ("llm_fake_mode", False),
                        ("llm_tool_output", False), ("ai_reuse_replies", False), ("ai_batch_jobs", False),
                        ("ai_daily_limit_usd", 1000.0), ("ai_monthly_limit_usd", 1000.0), ("ai_candidate_round_limit_usd", 1000.0)):
        monkeypatch.setattr(llm_service.settings, name, value)
    monkeypatch.setattr(llm_service, "_MONTH_SPEND", {"month": None, "usd": 0.0})
    monkeypatch.setattr(llm_service, "_SPEND", {"day": None, "day_usd": 0.0, "candidates": {}})
    script, sent = [], []

    def create(**kwargs):
        sent.append(kwargs)
        text, stop = script.pop(0) if len(script) > 1 else script[0]
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop,
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=300,
                                                     cache_creation_input_tokens=50))
    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    client.with_options = lambda **k: client
    monkeypatch.setattr(llm_service, "_get_client", lambda: client)

    def say(*replies, stop="end_turn"):
        script.extend((r if isinstance(r, str) else json.dumps(r), stop) for r in replies)
    # the requests to the model (the log also keeps notes, e.g. "invalid JSON, retrying", and blocked calls)
    records = lambda: [r for r in (json.loads(line) for line in path.read_text().splitlines()) if r.get("input_tokens") is not None] \
        if path.exists() else []
    everything = lambda: [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return SimpleNamespace(say=say, script=script, sent=sent, records=records, everything=everything)


STEPS = [{"step": "Log in as jordan", "missing": ""}]


def _turn(prompt="Log in as jordan. Generate the code.", request_id="r1"):
    with llm_service.call_context(round_number=2, submission_id=5, user_id=9, request_id=request_id):
        return round2_typist.turn("python", {"title": "Sign in"}, [], "", prompt)


def test_one_request_shares_its_request_id_and_each_call_has_its_own_id_and_type(claude):
    code = "print('Log in as jordan')\n"
    claude.say({"reply": "Should the booking be BK-009?", "steps": STEPS, "code": code},      # reply rejected, program kept
               {"reply": "Here is the program.", "steps": STEPS, "code": None})
    out = _turn()
    assert out["code_after"] == code
    rows = claude.records()
    assert [r["call_type"] for r in rows] == ["first_draft", "reply_only"]
    assert {r["request_id"] for r in rows} == {"r1"} and len({r["call_id"] for r in rows}) == 2
    for r in rows:
        assert (r["input_tokens"], r["output_tokens"], r["cache_read_tokens"], r["cache_write_tokens"]) == (1000, 200, 300, 50)
        assert r["total_tokens"] == 1550 and r["success"] is True and r["outcome"] == "ok" and r["model"] == "claude-sonnet-4-5"
        assert r["cost_usd"] == pytest.approx(llm_service.call_cost_usd("claude-sonnet-4-5", r)) and r["at"].endswith("Z")


def test_full_redraft_and_compile_fix_are_labelled(claude):
    claude.say({"reply": "Noted.", "steps": STEPS, "code": None},                                  # asked for code, none -> redraft
               {"reply": "Here it is.", "steps": STEPS, "code": "def f(:\n  print('Log in as jordan')\n"},   # doesn't compile
               {"reply": "Here it is.", "steps": STEPS, "code": "print('Log in as jordan')\n"})
    _turn()
    assert [r["call_type"] for r in claude.records()] == ["first_draft", "full_redraft", "compile_fix"]


def test_json_and_cutoff_retries_are_labelled_and_the_label_comes_back(claude):
    claude.say("not json at all", json.dumps({"reply": "Noted.", "steps": STEPS, "code": None}))
    _turn("Log in as jordan")
    assert [r["call_type"] for r in claude.records()] == ["first_draft", "json_retry"]
    claude.script.clear()
    claude.script.extend([(json.dumps({"reply": "part"}), "max_tokens"), (json.dumps({"reply": "Noted: jordan.", "steps": STEPS}), "end_turn")])
    _turn("Log in as jordan now", request_id="r2")
    r2 = [r for r in claude.records() if r["request_id"] == "r2"]
    assert [(r["call_type"], r["outcome"]) for r in r2] == [("first_draft", "cut_off_retrying"), ("cutoff_retry", "ok")]
    assert llm_service._CALL_TYPE.get() is None                                   # nothing leaks into the next call


def test_calls_outside_a_labelled_request_keep_their_old_record(claude):
    claude.say("hello")
    llm_service._call_claude("p")
    [r] = claude.records()
    assert r["call_type"] is None and "request_id" not in r and r["call_id"] and r["success"] is True


def test_a_blocked_call_carries_the_request_id_and_is_not_counted(claude, monkeypatch):
    monkeypatch.setattr(llm_service.settings, "ai_candidate_round_calls", 0)
    with pytest.raises(llm_service.AILimitReached):
        _turn(request_id="r3")
    [r] = claude.everything()
    assert (r["request_id"], r["outcome"], r["success"], r["call_type"]) == ("r3", "limit_reached", False, "first_draft") and claude.sent == []
    assert llm_service.request_totals("r3")["calls"] == 0


def test_request_totals_add_up_the_recorded_calls(claude):
    claude.say({"reply": "Should the booking be BK-009?", "steps": STEPS, "code": "print('Log in as jordan')\n"},
               {"reply": "Here is the program.", "steps": STEPS, "code": None})
    _turn()
    t = llm_service.request_totals("r1")
    rows = claude.records()
    assert (t["calls"], t["input_tokens"], t["output_tokens"], t["cache_read_tokens"], t["cache_write_tokens"]) == (2, 2000, 400, 600, 100)
    assert t["cost_usd"] == pytest.approx(sum(r["cost_usd"] for r in rows)) and t["call_types"] == ["first_draft", "reply_only"]


def test_each_candidate_message_gets_its_own_request_id_end_to_end(client, monkeypatch, claude):
    """Through the real Round 2 endpoint: two messages, two request ids - kept on the turns, on the records,
    and summed per request on HR's AI health card."""
    cand = _environment_round(client, monkeypatch)
    claude.say({"reply": "Noted: log in as jordan.", "steps": STEPS, "code": None},
               {"reply": "Noted: then open Loans.", "steps": STEPS + [{"step": "Then open Loans", "missing": ""}], "code": None})
    for text in ("Log in as jordan", "Then open Loans"):
        res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": text, "row_index": 0}, cookies=_auth(cand))
        assert res.status_code == 201, res.text
    turns = client.get("/candidate/round/2/auto/state", cookies=_auth(cand)).json()["tc_state"][0]["turns"]
    ids = [r["request_id"] for r in claude.records()]
    assert len(ids) == 2 and ids[0] != ids[1] and all(len(i) == 32 for i in ids)
    from app import database
    from app.models import Submission
    db = database.SessionLocal()
    try:
        sub = db.query(Submission).filter(Submission.round_number == 2, Submission.archived.is_(False)).order_by(Submission.id.desc()).first()
        saved = [t.get("request_id") for t in sub.content["selected"][0]["turns"]]
    finally:
        db.close()
    assert saved == ids and len(turns) == 2
    hr = _auth(_login(client, HR_EMAIL, HR_PASSWORD))
    recent = client.get("/hr/ai-health", cookies=hr).json()["lasting"]["recent_requests"]
    assert [r["request_id"] for r in recent] == ids[::-1] and all(r["calls"] == 1 and r["cost_usd"] > 0 for r in recent)


def test_the_caller_is_still_the_real_caller(claude, monkeypatch):
    """The call-type wrapper must not show up as the caller in the log (HR's per-caller totals)."""
    claude.say({"reply": "Noted: log in as jordan.", "steps": STEPS, "code": None})
    _turn("Log in as jordan")
    assert [r["caller"] for r in claude.records()] == ["turn"]
    monkeypatch.setattr(llm_service.settings, "ai_candidate_round_calls", 0)
    with pytest.raises(llm_service.AILimitReached):
        _turn("Again", request_id="r9")
    assert [r["caller"] for r in claude.everything() if r.get("request_id") == "r9"] == ["turn"]
