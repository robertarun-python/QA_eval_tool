"""The lasting AI cost record: every Claude call is appended to a file that
survives server restarts (settings.ai_call_log_path), with its model, tokens,
cost at that model's own price, time taken, the prompt file and version, and
what it was for (round, scenario, submission, candidate) - so HR can see what
a candidate, a day or a round really cost. Metadata only, never prompts or
replies; a problem writing the record never breaks the AI call itself."""
import json
from types import SimpleNamespace

import pytest

from app.services import llm_service
from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, HR_EMAIL, HR_PASSWORD, _auth, _login


@pytest.fixture
def record(tmp_path, monkeypatch):
    path = tmp_path / "ai_calls.jsonl"
    monkeypatch.setattr(llm_service.settings, "ai_call_log_path", str(path))
    monkeypatch.setattr(llm_service.settings, "claude_model", "claude-sonnet-4-5")
    llm_service._CALL_LOG.clear()

    def lines():
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    return lines


def _client_replying(usage=None, model_seen=None):
    usage = usage or SimpleNamespace(input_tokens=1000, output_tokens=500, cache_read_input_tokens=2000, cache_creation_input_tokens=400)

    def create(**kwargs):
        if model_seen is not None:
            model_seen.append(kwargs["model"])
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn", usage=usage)
    return lambda: SimpleNamespace(messages=SimpleNamespace(create=create))


def test_each_call_is_saved_with_its_tokens_and_cost(record, monkeypatch):
    monkeypatch.setattr(llm_service, "_get_client", _client_replying())
    assert llm_service._call_claude("p") == "ok"
    [entry] = record()
    assert entry["model"] == "claude-sonnet-4-5" and entry["outcome"] == "ok"
    assert (entry["input_tokens"], entry["output_tokens"], entry["cache_read_tokens"], entry["cache_write_tokens"]) == (1000, 500, 2000, 400)
    # $3 in, $15 out, cache read 0.1x input, 5-minute cache write 1.25x input - per million tokens
    assert entry["cost_usd"] == pytest.approx((1000 * 3 + 500 * 15 + 2000 * 0.3 + 400 * 3.75) / 1e6)
    assert entry["ms"] is not None and entry["at"]
    assert "prompt" not in entry and "reply" not in entry


def test_the_record_survives_a_restart(record, monkeypatch, client):
    monkeypatch.setattr(llm_service, "_get_client", _client_replying())
    llm_service._call_claude("p")
    llm_service._CALL_LOG.clear()  # what a restart does to the in-memory history
    health = client.get("/hr/ai-health", cookies=_auth(_login(client, HR_EMAIL, HR_PASSWORD))).json()
    assert health["total"] == 0
    assert health["lasting"]["calls"] == 1
    assert health["lasting"]["all_time_usd"] == pytest.approx(round((1000 * 3 + 500 * 15 + 2000 * 0.3 + 400 * 3.75) / 1e6, 4))


def test_calls_are_labelled_with_what_they_were_for(record, monkeypatch):
    monkeypatch.setattr(llm_service, "_get_client", _client_replying())
    with llm_service.call_context(round_number=2, user_id=7):
        with llm_service.call_context(submission_id=31, scenario_id=4):
            llm_service._call_claude("p")
        llm_service._call_claude("p")
    llm_service._call_claude("p")
    inner, outer, outside = record()
    assert (inner["round_number"], inner["user_id"], inner["submission_id"], inner["scenario_id"]) == (2, 7, 31, 4)
    assert (outer["round_number"], outer["user_id"]) == (2, 7) and "submission_id" not in outer
    assert "round_number" not in outside and "user_id" not in outside


def test_the_prompt_file_and_its_version_are_recorded(record, monkeypatch):
    monkeypatch.setattr(llm_service, "_get_client", _client_replying())
    text = llm_service._load_prompt("round1_scoring.txt")
    llm_service._call_claude(text)
    [entry] = record()
    assert entry["prompt_file"] == "round1_scoring.txt"
    assert entry["prompt_hash"] == llm_service._prompt_hash(text)


@pytest.mark.parametrize("model, expected", [
    ("claude-sonnet-5", (1000 * 2 + 500 * 10 + 2000 * 0.2 + 400 * 2.5) / 1e6),
    ("claude-haiku-4-5", (1000 * 1 + 500 * 5 + 2000 * 0.1 + 400 * 1.25) / 1e6),
    ("claude-sonnet-4-5-20250929", (1000 * 3 + 500 * 15 + 2000 * 0.3 + 400 * 3.75) / 1e6),  # a dated id prices as its model
    ("claude-unknown-9", None),  # never a made-up price
])
def test_each_model_is_priced_at_its_own_rate(record, monkeypatch, model, expected):
    monkeypatch.setattr(llm_service.settings, "claude_model", model)
    monkeypatch.setattr(llm_service, "_get_client", _client_replying())
    llm_service._call_claude("p")
    [entry] = record()
    assert entry["cost_usd"] == (pytest.approx(expected) if expected is not None else None)


def test_a_record_that_cannot_be_written_never_breaks_the_call(record, monkeypatch, tmp_path):
    monkeypatch.setattr(llm_service.settings, "ai_call_log_path", str(tmp_path))  # a folder, not a file
    monkeypatch.setattr(llm_service, "_get_client", _client_replying())
    assert llm_service._call_claude("p") == "ok"


def test_failed_calls_are_recorded_too(record, monkeypatch):
    def create(**kwargs):
        raise RuntimeError("overloaded")
    monkeypatch.setattr(llm_service, "_get_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    with pytest.raises(RuntimeError):
        llm_service._call_claude("p")
    [entry] = record()
    assert entry["outcome"] == "api_error" and entry["cost_usd"] is None


def test_hr_sees_totals_by_day_round_and_candidate(record, client):
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    import app.database as database_module
    from app.models import User
    db = database_module.SessionLocal()
    cand_id = db.query(User).filter(User.email == CANDIDATE1_EMAIL).one().id
    db.close()
    for entry in (
        {"at": "2026-09-20T10:00:00Z", "caller": "score_round1_submission", "outcome": "ok", "cost_usd": 0.05, "round_number": 1, "user_id": cand_id},
        {"at": "2026-09-21T10:00:00Z", "caller": "round2_automation_turn", "outcome": "ok", "cost_usd": 0.25, "round_number": 2, "user_id": cand_id},
        {"at": "2026-09-21T11:00:00Z", "caller": "generate_round1_reference", "outcome": "ok", "cost_usd": 0.10},
        {"at": "2026-09-21T12:00:00Z", "caller": "round2_automation_turn", "outcome": "ok", "cost_usd": None},
    ):
        llm_service._append_call_log(entry)
    lasting = client.get("/hr/ai-health", cookies=_auth(hr)).json()["lasting"]
    assert lasting["calls"] == 4 and lasting["unpriced_calls"] == 1
    assert lasting["all_time_usd"] == pytest.approx(0.40)
    assert lasting["by_day"] == [{"day": "2026-09-21", "usd": pytest.approx(0.35), "calls": 3},
                                 {"day": "2026-09-20", "usd": pytest.approx(0.05), "calls": 1}]
    assert lasting["by_round"] == {"1": pytest.approx(0.05), "2": pytest.approx(0.25), "none": pytest.approx(0.10)}
    assert lasting["top_candidates"] == [{"user_id": cand_id, "email": CANDIDATE1_EMAIL, "usd": pytest.approx(0.30), "calls": 2}]
    assert client.get("/hr/ai-health", cookies=_auth(cand)).status_code == 403


def test_a_damaged_line_in_the_record_is_skipped(record, client, tmp_path):
    llm_service._append_call_log({"at": "2026-09-21T10:00:00Z", "caller": "x", "outcome": "ok", "cost_usd": 0.1})
    with open(llm_service.settings.ai_call_log_path, "a", encoding="utf-8") as f:
        f.write('{"at": "2026-09-21T1\n')  # a write cut off by a crash
    lasting = client.get("/hr/ai-health", cookies=_auth(_login(client, HR_EMAIL, HR_PASSWORD))).json()["lasting"]
    assert lasting["calls"] == 1


def test_scoring_calls_are_labelled_with_the_round_and_candidate(record, client, monkeypatch):
    from .test_scoring_runs_once import _submitted_round1
    from app.models import RoundStatus
    from app.services import scoring_service
    sid = _submitted_round1(client, monkeypatch)
    seen = {}

    def scorer(db, submission):
        seen.update(llm_service._CALL_CONTEXT.get())
        seen["expected"] = (submission.id, submission.user_id, submission.scenario_id)
        submission.status = RoundStatus.scored
        db.commit()
    monkeypatch.setitem(scoring_service._SCORERS, 1, scorer)
    scoring_service.score_submission_in_background(sid)
    assert (seen["submission_id"], seen["user_id"], seen["scenario_id"]) == seen["expected"]
    assert seen["round_number"] == 1


def test_practice_app_builds_are_labelled_with_their_scenario(record, monkeypatch):
    from app.services.practice_app import service
    seen = {}
    monkeypatch.setattr(service, "_run_build", lambda scenario_id: seen.update(llm_service._CALL_CONTEXT.get()))
    service.run_build(42)
    assert seen == {"round_number": 2, "scenario_id": 42}


def test_hr_reference_generation_is_labelled_with_its_scenario(record, client, monkeypatch):
    from .conftest import _publish_scenario
    seen = []
    from .conftest import _FAKE_REFERENCE_BY_ROUND
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr, monkeypatch, round_number=1)
    monkeypatch.setattr(llm_service, "generate_round1_reference",
                        lambda **k: seen.append(dict(llm_service._CALL_CONTEXT.get())) or _FAKE_REFERENCE_BY_ROUND[1]())
    created = client.post("/hr/scenarios", json={"round_number": 1, "title": "t", "description": "d", "experience_band": "0-7",
                                                 "time_limit_minutes": 30}, cookies=_auth(hr)).json()
    assert seen == [{"round_number": 1, "scenario_id": created["id"]}]


def _user_id(email):
    import app.database as database_module
    from app.models import User
    db = database_module.SessionLocal()
    try:
        return db.query(User).filter(User.email == email).one().id
    finally:
        db.close()


def test_round3_messages_are_labelled_with_the_candidate(record, client, monkeypatch):
    from .conftest import _publish_scenario
    from .conftest import _seed_completed_rounds
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr, monkeypatch, round_number=1, title="R1")
    _publish_scenario(client, hr, monkeypatch, round_number=4, title="R2")
    _publish_scenario(client, hr, monkeypatch, round_number=3, title="Add two numbers")
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **k: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand))
    _seed_completed_rounds(CANDIDATE1_EMAIL, 2)
    started = client.post("/candidate/round/3/start", json={}, cookies=_auth(cand)).json()
    seen = []
    monkeypatch.setattr(llm_service, "round3_coding_turn", lambda **k: seen.append(dict(llm_service._CALL_CONTEXT.get())) or {
        "response_kind": "code_edit", "response_message": "ok", "code_after": "print(1)"})
    assert client.post("/candidate/round/3/turn", json={"candidate_prompt": "print one"}, cookies=_auth(cand)).status_code == 201
    [context] = seen
    assert context["round_number"] == 3 and context["user_id"] == _user_id(CANDIDATE1_EMAIL)
    assert context["submission_id"] == started["id"] and context["scenario_id"]


def test_round2_messages_are_labelled_with_the_candidate(record, client, monkeypatch):
    from .test_round2_automation import _reach_automation_round, _select, _SUFFICIENT
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    cand = _reach_automation_round(client, hr, monkeypatch)
    _select(client, cand)
    seen = []
    replies = [_SUFFICIENT, json.dumps({"response_kind": "code_edit", "response_message": "ok", "code_after": "print(1)"})]
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: seen.append(dict(llm_service._CALL_CONTEXT.get())) or replies[len(seen) - 1])
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode step 1"}, cookies=_auth(cand))
    assert res.status_code == 201, res.text
    assert len(seen) == 2  # the clarify check and the code
    for context in seen:
        assert context["round_number"] == 2 and context["user_id"] == _user_id(CANDIDATE1_EMAIL)
        assert context["submission_id"] and context["scenario_id"]
