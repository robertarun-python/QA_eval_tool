"""
A round whose scoring failed is handed in, from the candidate's side (live, 2026-09-30: candidate4's Round 2
scoring was blocked by a spend limit; the page treated the round as still open, reopened it with its time
long gone, auto-submitted, was refused and reopened it - a loop the candidate couldn't log out of). The
candidate moves on; HR retries the scoring (hr.py retry_scoring), once, on the same submission. A fake
Claude answers through the real gateway - no API call.
"""
import json
from types import SimpleNamespace

import pytest

from app.services import llm_service
from .conftest import HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, _login, _auth, _publish_scenario

SCORE = {"coverage_score": 70, "misses": [], "final_score": 70, "feedback_text": "ok", "concept_coverage": []}


@pytest.fixture
def claude(tmp_path, monkeypatch):
    """Scores through the real gateway: "bad" answers fail the scoring, a score answers it."""
    path = tmp_path / "calls.jsonl"
    for name, value in (("ai_call_log_path", str(path)), ("claude_model", "claude-sonnet-4-5"), ("llm_fake_mode", False),
                        ("llm_tool_output", False), ("ai_reuse_replies", False), ("ai_batch_jobs", False),
                        ("ai_daily_limit_usd", 1000.0), ("ai_monthly_limit_usd", 1000.0), ("ai_candidate_round_limit_usd", 1000.0)):
        monkeypatch.setattr(llm_service.settings, name, value)
    monkeypatch.setattr(llm_service, "_MONTH_SPEND", {"month": None, "usd": 0.0})
    monkeypatch.setattr(llm_service, "_SPEND", {"day": None, "day_usd": 0.0, "candidates": {}})
    answer = {"text": '{"final_score": "high"}'}   # not a score: scoring fails (see _require_reply)
    sent = []

    def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=answer["text"])], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    client_ = SimpleNamespace(messages=SimpleNamespace(create=create))
    client_.with_options = lambda **k: client_
    monkeypatch.setattr(llm_service, "_get_client", lambda: client_)
    rows = lambda: [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return SimpleNamespace(answer=answer, sent=sent, rows=rows)


def _round1_scoring_fails(client, monkeypatch):
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr, monkeypatch, title="Checkout flow")
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand))
    res = client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
                      cookies=_auth(cand))
    assert res.status_code == 201, res.text
    [sub] = [s for s in client.get("/candidate/submissions", cookies=_auth(cand)).json() if s["round_number"] == 1]
    assert sub["status"] == "scoring_failed"
    return hr, cand, sub


def test_a_failed_scoring_does_not_keep_the_next_round_locked(client, monkeypatch, claude):
    _, cand, _ = _round1_scoring_fails(client, monkeypatch)
    res = client.get("/candidate/round/2", cookies=_auth(cand))
    assert res.status_code != 403, res.text                        # was 403 "Round 2 isn't unlocked yet"
    from app import database
    from app.models import User
    from app.routers.candidate import _max_completed_round
    db = database.SessionLocal()
    try:
        assert _max_completed_round(db, db.query(User).filter(User.email == CANDIDATE1_EMAIL).one()) == 1
    finally:
        db.close()


def test_the_failed_round_stays_one_handed_in_submission(client, monkeypatch, claude):
    _, cand, sub = _round1_scoring_fails(client, monkeypatch)
    again = client.post("/candidate/round/1/start", cookies=_auth(cand))
    assert again.json()["id"] == sub["id"]                          # the same attempt - no new submission
    res = client.post("/candidate/round/1/submit", json={"content": [{"title": "y", "steps": "y", "expected_result": "y"}]},
                      cookies=_auth(cand))
    assert res.status_code == 400                                   # already handed in: nothing to redo or rescore
    state = client.get("/candidate/round/1", cookies=_auth(cand)).json()
    assert state["submission"]["status"] == "scoring_failed"        # what the page reads as handed in
    assert [s["id"] for s in client.get("/candidate/submissions", cookies=_auth(cand)).json() if s["round_number"] == 1] == [sub["id"]]


def test_hr_retries_it_once_on_the_same_submission_and_the_costs_add_up(client, monkeypatch, claude):
    hr, cand, sub = _round1_scoring_fails(client, monkeypatch)
    failed_calls = len(claude.sent)
    claude.answer["text"] = json.dumps(SCORE)
    res = client.post(f"/hr/submissions/{sub['id']}/retry-scoring", cookies=_auth(hr))
    assert res.status_code == 200 and res.json()["status"] == "scored" and res.json()["score"]["final_score"] == 70
    assert len(claude.sent) == failed_calls + 1                     # one scoring call, not a second scoring run
    assert client.post(f"/hr/submissions/{sub['id']}/retry-scoring", cookies=_auth(hr)).status_code == 400
    assert len(claude.sent) == failed_calls + 1
    calls = [r for r in claude.rows() if llm_service._is_call(r)]
    assert len(calls) == len(claude.sent) and {r["submission_id"] for r in calls} == {sub["id"]}
    key = next(k for k in llm_service._SPEND["candidates"] if k[2] == sub["id"])
    usd, count = llm_service._SPEND["candidates"][key]
    assert count == len(calls) and usd == pytest.approx(sum(r["cost_usd"] for r in calls))
    assert [s["status"] for s in client.get("/candidate/submissions", cookies=_auth(cand)).json() if s["round_number"] == 1] == ["scored"]


def test_normal_scoring_is_unchanged(client, monkeypatch, claude):
    claude.answer["text"] = json.dumps(SCORE)
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr, monkeypatch, title="Checkout flow")
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand))
    [sub] = [s for s in client.get("/candidate/submissions", cookies=_auth(cand)).json() if s["round_number"] == 1]
    assert sub["status"] == "scored" and len(claude.sent) == 1
    assert client.get("/candidate/round/2", cookies=_auth(cand)).status_code != 403


def test_an_unfinished_round_still_keeps_the_next_one_locked(client, monkeypatch, claude):
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr, monkeypatch, title="Checkout flow")
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand))
    assert client.get("/candidate/round/2", cookies=_auth(cand)).status_code == 403
