"""
The per-candidate Round 2 allowance is checked when a candidate's message STARTS, never in the middle of
one (owner, 2026-10-05): a message whose first AI call was allowed finishes its whole redraft / compile-fix /
JSON / cut-off retry chain, even if those internal calls take the attempt past its dollar or call allowance;
the NEXT message is the one refused (429 "contact HR", no AI call). A per-attempt message cap (default 40) is
the everyday limit and the dollars ($2.50) a runaway backstop. Round 2 scoring is a system call: never
counted in, or stopped by, the candidate's allowance - the daily and monthly limits still apply to it.

The AI is a fake that charges by output tokens (Sonnet 4.5 list price); no real API call is ever made.
"""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services import llm_service, round2_typist
from .conftest import _auth
from .test_round2_automation import _SUFFICIENT, _reach_automation_round, _select, _sequential_call_claude, _submit_payload
from .test_round2_typist_endpoints import _environment_round
from .conftest import HR_EMAIL, HR_PASSWORD, _login

REAL_CALL_CLAUDE = llm_service._call_claude
CAND = dict(round_number=2, scenario_id=1, submission_id=5, user_id=9)
KEY = (9, 2, 5)
PER_TOKEN = 15 / 1e6  # Sonnet 4.5 output, US$ per token


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A call record and a fake model: each request takes the next scripted reply (default: $0.30 of output)."""
    path = tmp_path / "ai_calls.jsonl"
    for name, value in dict(ai_call_log_path=str(path), claude_model="claude-sonnet-4-5", llm_fake_mode=False,
                            ai_monthly_limit_usd=100.0, ai_daily_limit_usd=100.0, ai_candidate_round_limit_usd=0.75,
                            ai_candidate_round_calls=60, ai_candidate_round_messages=40, ai_reuse_replies=False,
                            ai_batch_jobs=False, llm_tool_output=False).items():
        monkeypatch.setattr(llm_service.settings, name, value)
    monkeypatch.setattr(llm_service, "_MONTH_SPEND", {"month": None, "usd": 0.0})
    monkeypatch.setattr(llm_service, "_SPEND", {"day": None, "day_usd": 0.0, "candidates": {}, "messages": {}})
    sent, queue = [], []

    def create(**kwargs):
        sent.append(kwargs)
        step = queue.pop(0) if queue else {}
        if step.get("error"):
            raise RuntimeError(step["error"])
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=step.get("text", '{"ok": 1}'))],
                               stop_reason=step.get("stop", "end_turn"),
                               usage=SimpleNamespace(input_tokens=0, output_tokens=step.get("tokens", 20_000),
                                                     cache_read_input_tokens=0, cache_creation_input_tokens=0))
    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(llm_service, "_get_client", lambda: SimpleNamespace(messages=client.messages, with_options=lambda **k: client))

    def spent_before(usd, *, calls=1, day=None, **ctx):
        at = (day or datetime.utcnow().strftime("%Y-%m-%d")) + "T00:00:01Z"
        with path.open("a") as f:
            for _ in range(calls):
                f.write(json.dumps({"at": at, "outcome": "ok", "cost_usd": usd / calls, **ctx}) + "\n")
        llm_service._SPEND["day"] = None          # written behind the app's back: make it read the record again
        llm_service._MONTH_SPEND["month"] = None

    def records():
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return SimpleNamespace(sent=sent, queue=queue, spent_before=spent_before, records=records, path=path)


def _message(request_id, chain=("first_draft",), **extra_ctx):
    """One candidate message: its calls, in order, each under its call_type - as round2_typist makes them."""
    with llm_service.call_context(**CAND, request_id=request_id, **extra_ctx):
        for call_type in chain:
            with llm_service.call_type(call_type):
                llm_service._call_claude("p")


# ---- the new defaults ----

def test_the_new_defaults():
    fields = Settings.model_fields
    assert fields["ai_candidate_round_limit_usd"].default == 2.50
    assert fields["ai_candidate_round_calls"].default == 60
    assert fields["ai_candidate_round_messages"].default == 40
    assert fields["ai_daily_limit_usd"].default == 3.0 and fields["ai_monthly_limit_usd"].default == 10.0  # unchanged


# ---- A: a started message is never cut off ----

def test_a_started_message_finishes_its_whole_internal_chain_past_the_allowance(fake):
    fake.spent_before(0.60, **CAND, request_id="earlier")              # $0.60 of $0.75 used by an earlier message
    _message("m1", ("first_draft", "full_redraft", "reply_only", "compile_fix"))   # 0.60 -> 1.80: nothing stops it
    assert len(fake.sent) == 4
    # its JSON retry and cut-off retry are part of the same message too
    fake.queue[:] = [{"text": "not json at all"}, {"text": '{"ok": 2}'}]
    with llm_service.call_context(**CAND, request_id="m1"), llm_service.call_type("full_redraft"):
        assert llm_service._call_claude_json("p") == {"ok": 2}
    fake.queue[:] = [{"stop": "max_tokens", "tokens": 100}, {"text": '{"ok": 3}'}]
    with llm_service.call_context(**CAND, request_id="m1"), llm_service.call_type("compile_fix"):
        llm_service._call_claude("p")
    types = [r["call_type"] for r in fake.records() if r.get("request_id") == "m1" and r["outcome"] in ("ok", "cut_off_retrying")]
    assert types == ["first_draft", "full_redraft", "reply_only", "compile_fix", "full_redraft", "json_retry", "compile_fix", "cutoff_retry"]
    assert not [r for r in fake.records() if r["outcome"] == "limit_reached"]


def test_a_the_endpoint_completes_a_message_whose_redrafts_pass_the_allowance(client, monkeypatch, fake):
    cand = _environment_round(client, monkeypatch)
    monkeypatch.setattr(llm_service.settings, "ai_candidate_round_limit_usd", 0.50)

    def three_drafts(*a, **k):  # the typist's first draft, a redraft and a compile fix: $0.90 for one message
        for call_type in ("first_draft", "full_redraft", "compile_fix"):
            with llm_service.call_type(call_type):
                llm_service._call_claude("p")
        return {"response_kind": "clarify", "response_message": "Noted: log in as jordan.", "code_after": None, "steps": []}
    monkeypatch.setattr(round2_typist, "turn", three_drafts)
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "Log in as jordan", "row_index": 0}, cookies=_auth(cand))
    assert res.status_code == 201, res.text                      # no 429 mid-message
    assert len(fake.sent) == 3
    # B: the NEXT message is refused before any AI call
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "Click Borrow", "row_index": 0}, cookies=_auth(cand))
    assert res.status_code == 429 and "HR" in res.json()["detail"] and "$" not in res.json()["detail"]
    assert len(fake.sent) == 3


# ---- B: a new message is refused once an allowance is used ----

def test_b_a_new_message_is_refused_once_the_dollar_allowance_is_used(fake):
    fake.spent_before(0.75, **CAND, request_id="earlier")
    with pytest.raises(llm_service.AILimitReached) as stop:
        _message("m2")
    assert stop.value.limit == "candidate_round_usd" and fake.sent == []


def test_b_the_message_cap_refuses_the_41st_message_but_finishes_the_40th(fake):
    for i in range(40):
        fake.spent_before(0.001, **CAND, request_id=f"m{i}")
    _message("m39", ("full_redraft", "compile_fix"))                   # an already-counted message keeps going
    with pytest.raises(llm_service.AILimitReached) as stop:
        _message("m40")
    assert stop.value.limit == "candidate_round_messages" and len(fake.sent) == 2
    [record] = [r for r in fake.records() if r["outcome"] == "limit_reached"]
    assert record["limit"] == "candidate_round_messages" and record["cost_usd"] == 0.0 and "40 of 40" in record["detail"]
    assert len(llm_service._spend_now()["messages"][KEY]) == 40


def test_b_the_endpoint_refuses_a_message_over_the_cap_with_no_ai_call(client, monkeypatch, fake):
    cand = _environment_round(client, monkeypatch)
    monkeypatch.setattr(llm_service.settings, "ai_candidate_round_messages", 1)
    monkeypatch.setattr(round2_typist, "turn", lambda *a, **k: (llm_service._call_claude("p"), {
        "response_kind": "clarify", "response_message": "Noted.", "code_after": None, "steps": []})[1])
    assert client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "Log in as jordan", "row_index": 0},
                       cookies=_auth(cand)).status_code == 201
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "Click Borrow", "row_index": 0}, cookies=_auth(cand))
    assert res.status_code == 429 and len(fake.sent) == 1


# ---- C + D: scoring and the global limits ----

def test_c_scoring_runs_past_the_candidates_allowance_and_is_recorded(fake):
    fake.spent_before(5.0, calls=60, **CAND, request_id="earlier")      # dollars, calls - everything used
    with llm_service.call_context(**CAND, system_call="r2_scoring"):
        llm_service._call_claude("score")
    [score] = [r for r in fake.records() if r.get("system_call") == "r2_scoring"]
    assert score["outcome"] == "ok" and score["cost_usd"] == pytest.approx(0.30)
    spent = llm_service._spend_now()
    assert spent["candidates"][KEY] == [pytest.approx(5.0), 60]           # not the candidate's
    assert spent["day_usd"] == pytest.approx(5.30)                       # but today's, like every call


@pytest.mark.parametrize("limit", ["daily", "monthly"])
def test_c_d_scoring_still_obeys_the_daily_and_monthly_limits(fake, monkeypatch, limit):
    if limit == "daily":
        monkeypatch.setattr(llm_service.settings, "ai_daily_limit_usd", 3.0)
    else:
        monkeypatch.setattr(llm_service.settings, "ai_monthly_limit_usd", 3.0)
    fake.spent_before(3.31)                                               # past the limit plus a candidate's 10% grace
    with llm_service.call_context(**CAND, system_call="r2_scoring"), pytest.raises(llm_service.AIBudgetReached) as stop:
        llm_service._call_claude("score")
    assert stop.value.limit == limit and fake.sent == []


def test_d_the_global_limits_stop_even_a_started_message(fake, monkeypatch):
    _message("m1")
    monkeypatch.setattr(llm_service.settings, "ai_daily_limit_usd", 0.25)
    with pytest.raises(llm_service.AILimitReached) as stop:
        with llm_service.call_context(**CAND, request_id="m1"), llm_service.call_type("full_redraft"):
            llm_service._call_claude("p")
    assert stop.value.limit == "daily" and len(fake.sent) == 1


def _r2_submission_ready(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand, (0,))
    _sequential_call_claude(monkeypatch, _SUFFICIENT, json.dumps({"response_kind": "code_edit", "response_message": "ok", "code_after": "x = 1\n"}))
    client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode step 1"}, cookies=_auth(cand))
    client.post("/candidate/round/2/auto/run", json={"code": "x = 1\n"}, cookies=_auth(cand))
    monkeypatch.setattr(llm_service, "_call_claude", REAL_CALL_CLAUDE)    # from here on, the fake paid model
    import app.database as database_module
    from app.models import Submission
    db = database_module.SessionLocal()
    sub = db.query(Submission).filter(Submission.round_number == 2).one()
    ids = dict(round_number=2, submission_id=sub.id, user_id=sub.user_id)
    db.close()
    return cand, ids


def _scorer_that_calls_the_model(**kwargs):
    llm_service._call_claude("score")
    return {"scores": {"automation_design": 15, "test_data_and_assertions": 15, "ai_usage": 15, "ai_output_review": 15,
                       "execution_and_validation": 15}, "final_score": 75, "findings": [], "feedback_text": "ok"}


@pytest.mark.parametrize("used_up", ["candidate", "daily"])
def test_c_r2_scoring_end_to_end(client, monkeypatch, fake, used_up):
    cand, ids = _r2_submission_ready(client, monkeypatch)
    if used_up == "candidate":
        fake.spent_before(5.0, calls=60, request_id="earlier", **ids)
    else:
        monkeypatch.setattr(llm_service.settings, "ai_daily_limit_usd", 3.0)
        fake.spent_before(3.31)
    monkeypatch.setattr(llm_service, "score_round2_automation_conversation", _scorer_that_calls_the_model)
    assert client.post("/candidate/round/2/auto/submit", json=_submit_payload(), cookies=_auth(cand)).status_code == 201
    import app.database as database_module
    from app.models import RoundStatus, Score, Submission
    db = database_module.SessionLocal()
    sub = db.get(Submission, ids["submission_id"])
    score = db.query(Score).filter(Score.submission_id == sub.id).one_or_none()
    status, error, final = sub.status, sub.scoring_error, score.final_score if score else None
    db.close()
    scoring = [r for r in fake.records() if r.get("system_call") == "r2_scoring"]
    if used_up == "candidate":                               # the candidate's allowance never stops their scoring
        assert status == RoundStatus.scored and final == 75
        assert [r["outcome"] for r in scoring] == ["ok"] and scoring[0]["cost_usd"] == pytest.approx(0.30)
    else:                                                    # the day's limit still does
        assert status == RoundStatus.scoring_failed and "today's ai spending limit" in (error or "").lower() and fake.sent == []


# ---- E: the 60-call cap at message start ----

def test_e_the_call_cap_is_checked_at_message_start_only(fake):
    fake.queue[:] = [{"tokens": 1}] * 10
    fake.spent_before(0.01, calls=59, **CAND, request_id="earlier")
    _message("m1", ("first_draft", "full_redraft", "compile_fix"))     # call 60 starts it; 61 and 62 finish it
    with pytest.raises(llm_service.AILimitReached) as stop:
        _message("m2")
    assert stop.value.limit == "candidate_round_calls" and len(fake.sent) == 3


def test_e_calls_without_a_request_id_are_still_checked_one_by_one(fake):
    """R1/R3/R4 and anything else untagged: as before - every call checked, none counted as a message."""
    fake.spent_before(0.60, **CAND)
    with llm_service.call_context(**CAND):
        llm_service._call_claude("p")                                   # 0.60 -> 0.90
        with pytest.raises(llm_service.AILimitReached) as stop:
            llm_service._call_claude("p")
    assert stop.value.limit == "candidate_round_usd" and len(fake.sent) == 1
    assert llm_service._spend_now()["messages"].get(KEY, set()) == set()


# ---- F: a free repeat ----

def test_f_an_identical_repeat_is_free_and_not_a_message(client, monkeypatch, fake):
    cand = _environment_round(client, monkeypatch)
    monkeypatch.setattr(round2_typist, "turn", lambda *a, **k: (llm_service._call_claude("p"), {
        "response_kind": "clarify", "response_message": "Noted: log in as jordan.", "code_after": None, "steps": []})[1])
    for _ in range(2):
        res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "Log in as jordan", "row_index": 0}, cookies=_auth(cand))
        assert res.status_code == 201, res.text
    assert len(fake.sent) == 1
    [messages] = llm_service._spend_now()["messages"].values()
    assert len(messages) == 1


# ---- G: a fresh attempt ----

def test_g_a_fresh_attempt_after_an_hr_reset_gets_a_fresh_message_allowance(fake):
    old = {**CAND, "submission_id": 208}
    for i in range(40):
        fake.spent_before(0.001, **old, request_id=f"old{i}")
    with llm_service.call_context(**old, request_id="old-next"), pytest.raises(llm_service.AILimitReached):
        llm_service._call_claude("p")
    _message("new-1")                                                    # CAND is the new attempt (submission 5)
    assert len(fake.sent) == 1


# ---- H: candidate C's real Round 2, replayed ----

# Pilot candidate C (submission 10, 2026-10-04): 10 messages, 19 counted calls - 15 paid drafts, 3 redrafts the
# API failed (counted, $0), and scoring. Costs as recorded in ai_calls_pilot.jsonl.
C_SEQUENCE = [
    [("first_draft", 0.030118)],
    [("first_draft", 0.072966), ("full_redraft", 0.081393), ("full_redraft", None)],
    [("first_draft", 0.080181), ("full_redraft", 0.078477), ("full_redraft", None)],
    [("first_draft", 0.068664), ("full_redraft", 0.068886), ("full_redraft", None)],
    [("first_draft", 0.078732), ("full_redraft", 0.078498)],
    [("first_draft", 0.032454)],
    [("first_draft", 0.097404)],
    [("first_draft", 0.075537), ("full_redraft", 0.069819)],
    [("first_draft", 0.030306)],
    [("first_draft", 0.099822)],
]
C_SCORING = 0.146022


def _replay_c(fake):
    finished = 0
    for i, calls in enumerate(C_SEQUENCE):
        with llm_service.call_context(**CAND, request_id=f"c{i}"):
            for call_type, cost in calls:
                fake.queue.append({"error": "overloaded"} if cost is None else {"tokens": round(cost / PER_TOKEN)})
                with llm_service.call_type(call_type):
                    try:
                        llm_service._call_claude("p")
                    except llm_service.AIBudgetReached:
                        raise
                    except RuntimeError as e:
                        assert "overloaded" in str(e)           # the recorded API failure, not a limit
        finished += 1
    fake.queue.append({"tokens": round(C_SCORING / PER_TOKEN)})
    with llm_service.call_context(**CAND, system_call="r2_scoring"):
        llm_service._call_claude("score")
    return finished


def test_h_candidate_c_completes_all_ten_messages_and_scoring_under_the_new_defaults(fake, monkeypatch):
    for name in ("ai_candidate_round_limit_usd", "ai_candidate_round_calls", "ai_candidate_round_messages",
                 "ai_daily_limit_usd", "ai_monthly_limit_usd"):
        monkeypatch.setattr(llm_service.settings, name, Settings.model_fields[name].default)
    assert _replay_c(fake) == 10
    records = fake.records()
    assert not [r for r in records if r["outcome"] in ("limit_reached", "budget_reached")]
    counted = [r for r in records if llm_service._is_call(r)]
    assert len(counted) == 19 and sum(r["outcome"] == "api_error" for r in counted) == 3
    assert sum(r["cost_usd"] or 0 for r in counted) == pytest.approx(1.189, abs=0.002)
    assert len(llm_service._spend_now()["messages"][KEY]) == 10


def test_h_candidate_c_under_the_old_75_cents_now_finishes_every_started_message(fake):
    """At the old $0.75 the 8th message is refused at its start - but no message is cut off part-way."""
    with pytest.raises(llm_service.AILimitReached) as stop:
        _replay_c(fake)
    assert stop.value.limit == "candidate_round_usd"
    started = {r["request_id"] for r in fake.records() if llm_service._is_call(r) and r.get("request_id")}
    for i in range(len(started)):                                        # every message that started has all its calls
        assert sum(1 for r in fake.records() if r.get("request_id") == f"c{i}" and llm_service._is_call(r)) == len(C_SEQUENCE[i])
    assert started == {f"c{i}" for i in range(7)}
