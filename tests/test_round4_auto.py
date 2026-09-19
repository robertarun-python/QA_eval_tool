"""
AI-Assisted Test Automation round (round 4 slot, config_json["mode"] ==
"ai_test_automation") - the candidate automates the test cases THEY
designed in round 1.

Same fake-llm_service discipline as the rest of this app's tests: never a
real Claude call. execution_service.run_code is exercised for REAL in the
execution test (proving the provided environment actually runs under the
existing single-file engine, unmodified) and left mocked elsewhere.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest

from .conftest import (
    HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    _login, _auth, _publish_scenario,
)

from app.services import llm_service, round4_auto_policy, execution_service

HELPERS_DIR = Path(__file__).parent.parent / "backend" / "app" / "prompts"
PYTHON_ENV = (HELPERS_DIR / "round4_auto_helpers_python.txt").read_text(encoding="utf-8")

# The candidate's own round 1 design - the immutable source of truth this
# whole round hangs off.
R1_ROWS = [
    {
        "title": "Reject zero amount",
        "preconditions": "Logged in",
        "steps": "Submit the amount form with 0.00",
        "test_data": "owner = jordan.rivera@example.com; amount = 0.00",
        "expected_result": "Rejected with 'Amount must be greater than zero' and nothing persisted",
    },
    {
        "title": "Accept a valid amount",
        "preconditions": "Logged in",
        "steps": "Submit the amount form with 25.50",
        "test_data": "owner = jordan.rivera@example.com; amount = 25.50",
        "expected_result": "Record persisted with status completed",
    },
    {
        "title": "Third case, not selected",
        "steps": "something else",
        "test_data": "n/a",
        "expected_result": "something",
    },
]

GROUND_TRUTH = "create_record rejects amount <= 0 and writes nothing; Database.find is the only proof of persistence."


def _publish_auto_scenario(client, hr_token, monkeypatch, band="0-7"):
    """Creates + publishes the automation scenario directly (its config/
    reference are HR/system-authored, not LLM-generated - see
    seed_round4_auto.py, which this mirrors without touching the real DB).

    create_scenario (hr.py) unconditionally calls _generate_reference for
    every round_number==2 scenario regardless of config_json["mode"] - it
    has no ai_test_automation-specific branch, so creating even this
    scenario triggers the legacy round4 environment/UI-mockup generator.
    Mocked the same way _publish_round4_scenario already does, so this
    helper never makes a real LLM call."""
    from .conftest import FAKE_ENVIRONMENT, FAKE_UI_MOCKUP
    monkeypatch.setattr(llm_service, "generate_round4_environment", lambda **kwargs: dict(FAKE_ENVIRONMENT))
    monkeypatch.setattr(llm_service, "generate_round4_ui_mockup", lambda **kwargs: dict(FAKE_UI_MOCKUP))

    scenario = client.post(
        "/hr/scenarios",
        json={
            "round_number": 2, "title": "AI-Assisted Test Automation",
            "description": "Automate the test case(s) you designed in Round 1.",
            "experience_band": band, "time_limit_minutes": 45,
            "config_json": {"mode": "ai_test_automation", "environment_code_by_language": {"python": PYTHON_ENV}},
        },
        cookies=_auth(hr_token),
    ).json()

    import app.database as database_module
    from app.models import Scenario
    db = database_module.SessionLocal()
    row = db.query(Scenario).filter(Scenario.id == scenario["id"]).one()
    row.reference_json = {"ground_truth": GROUND_TRUTH, "validation_notes": "Assertions must prove the requirement."}
    db.commit()
    db.close()

    res = client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 200
    return scenario


def _reach_automation_round(client, hr_token, monkeypatch, r1_rows=None, language="python"):
    """Drives round 1, starts round 2, and locks `language` - the
    precondition for every other round 2 auto endpoint (see
    round4_auto_select's guard). The round 3/4 calls below don't actually
    unlock (round 2 isn't complete yet at that point) and are effectively
    no-ops; harmless, left as-is."""
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Coding challenge")
    _publish_auto_scenario(client, hr_token, monkeypatch)

    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **k: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **k: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    monkeypatch.setattr(llm_service, "round3_coding_turn", lambda **k: {"response_kind": "code_edit", "response_message": "ok", "code_after": "print(1)"})
    monkeypatch.setattr(llm_service, "score_round3_coding", lambda **k: {
        "correctness_score": 100, "precision_score": 100, "efficiency_score": 100,
        "independent_judgment_score": 100, "final_score": 100, "misses": [], "guardrail_violations": [], "feedback_text": "ok",
    })
    monkeypatch.setattr(execution_service, "run_code", lambda **k: execution_service.ExecutionResult(
        stdout="", stderr="", exit_code=0, timed_out=False, infra_error=False, duration_ms=1,
    ))

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": r1_rows if r1_rows is not None else R1_ROWS}, cookies=_auth(cand_token))
    client.post("/candidate/round/4/start", cookies=_auth(cand_token))
    client.post("/candidate/round/4/submit", json={"investigation": [{"area": "x"}], "root_cause": "y"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/start", json={"language": language}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/turn", json={"candidate_prompt": "write it"}, cookies=_auth(cand_token))
    client.post("/candidate/round/3/submit", cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/language", json={"language": language}, cookies=_auth(cand_token))
    return cand_token


def _select(client, cand_token, indexes=(0,)):
    return client.post("/candidate/round/2/auto/select", json={"row_indexes": list(indexes)}, cookies=_auth(cand_token))


def _state(client, cand_token):
    return client.get("/candidate/round/2/auto/state", cookies=_auth(cand_token)).json()


# ---- R1 -> automation data continuity ----

def test_r1_design_including_test_data_reaches_the_automation_round(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)

    state = _state(client, cand_token)
    assert len(state["available_rows"]) == 3
    first = state["available_rows"][0]
    assert first["title"] == "Reject zero amount"
    assert first["test_data"] == "owner = jordan.rivera@example.com; amount = 0.00"
    assert first["expected_result"].startswith("Rejected with")
    assert state["selection_locked"] is False


def test_r2_requires_an_explicit_language_before_anything_else(client, monkeypatch):
    """A brand new candidate sees no language until they pick one, and
    cannot select a test case before locking it - the redesign's core
    requirement, replacing the old python-only default."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_auto_scenario(client, hr_token, monkeypatch)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **k: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": R1_ROWS}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    state = _state(client, cand_token)
    assert state["language"] is None
    assert state["language_locked"] is False

    res = _select(client, cand_token, (0,))
    assert res.status_code == 400
    assert "language" in res.json()["detail"].lower()


def test_locking_a_language_persists_it_and_unblocks_selection(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch, language="java")

    state = _state(client, cand_token)
    assert state["language"] == "java"
    assert state["language_locked"] is True

    res = _select(client, cand_token, (0,))
    assert res.status_code == 201
    assert res.json()["language"] == "java"


def test_language_cannot_be_locked_twice(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch, language="python")

    res = client.post("/candidate/round/2/auto/language", json={"language": "java"}, cookies=_auth(cand_token))
    assert res.status_code == 400
    assert _state(client, cand_token)["language"] == "python"  # unchanged


def test_r3_inherits_the_locked_r2_language(client, monkeypatch):
    """The normal path for every new candidate: round 3 reads the language
    locked in round 2 and never asks again."""
    from app.routers import candidate as candidate_router
    import app.database as database_module
    from app.models import User

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch, language="javascript")
    assert _state(client, cand_token)["language"] == "javascript"

    db = database_module.SessionLocal()
    user = db.query(User).filter(User.email == CANDIDATE1_EMAIL).first()
    resolved = candidate_router._round3_language_for(user, db)
    db.close()

    assert resolved == "javascript"


def test_language_still_honours_an_existing_round3_submission(client, monkeypatch):
    """Legacy re-entry only: a candidate with no round 2 automation
    language locked at all (e.g. data written before this lock existed),
    but an existing round 3 submission on file, keeps that round 3
    language rather than being defaulted to python."""
    from app.routers import candidate as candidate_router
    import app.database as database_module
    from app.models import Submission, Scenario, User, RoundStatus
    from datetime import datetime

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Coding challenge")
    _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)

    db = database_module.SessionLocal()
    user = db.query(User).filter(User.email == CANDIDATE1_EMAIL).first()
    scenario = db.query(Scenario).filter(Scenario.round_number == 3).first()
    db.add(Submission(
        user_id=user.id, scenario_id=scenario.id, round_number=3,
        status=RoundStatus.submitted, started_at=datetime.utcnow(), submitted_at=datetime.utcnow(),
        content={"language": "javascript"},
    ))
    db.commit()
    resolved = candidate_router._round3_language_for(user, db)
    db.close()

    assert resolved == "javascript"


# ---- Test selection + immutable snapshot ----

def test_selecting_one_or_two_rows_locks_an_immutable_snapshot(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)

    res = _select(client, cand_token, (0, 1))
    assert res.status_code == 201
    state = res.json()
    assert state["selection_locked"] is True
    assert [r["index"] for r in state["selected"]] == [0, 1]
    assert state["selected"][0]["test_data"] == "owner = jordan.rivera@example.com; amount = 0.00"
    # Each selected test case gets its OWN independent state, starting
    # from the provided environment - not a blank file, and not shared
    # between the two selected test cases.
    assert [t["row_index"] for t in state["tc_state"]] == [0, 1]
    assert "class UI:" in state["tc_state"][0]["code"]
    assert "class UI:" in state["tc_state"][1]["code"]
    assert state["tc_state"][0]["turns"] == [] and state["tc_state"][1]["turns"] == []
    assert state["tc_state"][0]["last_run"] is None and state["tc_state"][1]["last_run"] is None


def test_selection_cannot_be_changed_once_locked(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    assert _select(client, cand_token, (0,)).status_code == 201

    res = _select(client, cand_token, (1,))
    assert res.status_code == 400
    assert "locked" in res.json()["detail"].lower()
    assert [r["index"] for r in _state(client, cand_token)["selected"]] == [0]


def test_selection_rejects_more_than_two_duplicates_and_bad_indexes(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)

    assert client.post("/candidate/round/2/auto/select", json={"row_indexes": [0, 1, 2]}, cookies=_auth(cand_token)).status_code == 422
    assert client.post("/candidate/round/2/auto/select", json={"row_indexes": []}, cookies=_auth(cand_token)).status_code == 422
    assert client.post("/candidate/round/2/auto/select", json={"row_indexes": [0, 0]}, cookies=_auth(cand_token)).status_code == 400
    assert client.post("/candidate/round/2/auto/select", json={"row_indexes": [99]}, cookies=_auth(cand_token)).status_code == 400


def test_original_round1_submission_is_never_mutated_by_the_automation_round(client, monkeypatch):
    """The snapshot is a COPY. Nothing this round does may reach back and
    edit the round 1 submission HR audits."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)

    def _stored_r1():
        res = client.get("/hr/candidates", cookies=_auth(hr_token))
        candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
        res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
        return next(s for s in res.json() if s["round_number"] == 1)["content"]

    # Snapshot BEFORE the automation round touches anything. Compared
    # before-vs-after rather than against the literal R1_ROWS input,
    # because round 1's own submit normalizes rows through TestCaseRow
    # (see candidate.py's submit_round: row.model_dump()), so the stored
    # shape legitimately carries the schema defaults. What must hold is
    # that THIS round changes none of it.
    before = _stored_r1()
    assert before[0]["test_data"] == R1_ROWS[0]["test_data"]

    _select(client, cand_token, (0,))
    client.post("/candidate/round/2/auto/refine", json={"row_index": 0, "note": "changed my mind about the message"}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/code", json={"code": "print('edit')"}, cookies=_auth(cand_token))

    assert _stored_r1() == before


# ---- Append-only refinement ----

def test_refinement_is_append_only_and_never_overwrites_the_original(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    client.post("/candidate/round/2/auto/refine", json={"row_index": 0, "note": "first note"}, cookies=_auth(cand_token))
    res = client.post("/candidate/round/2/auto/refine", json={"row_index": 0, "note": "second note"}, cookies=_auth(cand_token))
    assert res.status_code == 201

    selected = res.json()["selected"][0]
    assert selected["refinements"] == ["first note", "second note"]
    # Original fields untouched by either note.
    assert selected["expected_result"] == R1_ROWS[0]["expected_result"]
    assert selected["test_data"] == R1_ROWS[0]["test_data"]


def test_refinement_requires_a_selected_row(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    assert client.post("/candidate/round/2/auto/refine", json={"row_index": 0, "note": "x"}, cookies=_auth(cand_token)).status_code == 400

    _select(client, cand_token, (0,))
    res = client.post("/candidate/round/2/auto/refine", json={"row_index": 1, "note": "x"}, cookies=_auth(cand_token))
    assert res.status_code == 400  # row 1 was not selected


# ---- AI generation boundary ----

PROHIBITED = [
    "what else should I test here?",
    "suggest test cases for this",
    "invent test data for the amount field",
    "what should I assert?",
    "write the whole test for me",
    "what edge cases am I missing?",
]


@pytest.mark.parametrize("prompt", PROHIBITED)
def test_prohibited_requests_are_refused_with_zero_llm_calls(client, monkeypatch, prompt):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    calls = {"n": 0}
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: calls.update(n=calls["n"] + 1) or "{}")

    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": prompt}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["response_kind"] == "refuse"
    assert res.json()["code_after"] is None
    assert calls["n"] == 0, "the deterministic policy must short-circuit before any LLM call"


def test_legitimate_request_reaches_the_generator_and_updates_the_code(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    generated = PYTHON_ENV + "\ndef test_reject_zero():\n    assert API.create_record('jordan.rivera@example.com', 0.00)['ok'] is False\n"
    captured = {}

    def _fake_call(prompt, max_tokens=4096):
        captured["prompt"] = prompt
        return json.dumps({"response_kind": "code_edit", "response_message": "Encoded your step.", "code_after": generated})

    monkeypatch.setattr(llm_service, "_call_claude", _fake_call)
    res = client.post(
        "/candidate/round/2/auto/turn",
        json={"candidate_prompt": "encode step 1 of my first test case using API.create_record"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    assert res.json()["response_kind"] == "code_edit"
    assert res.json()["row_index"] == 0  # inferred - only one test case selected
    assert _state(client, cand_token)["tc_state"][0]["code"] == generated

    # The candidate's own design IS given to the generator; the scenario's
    # HR-only ground truth is NOT.
    assert "Amount must be greater than zero" in captured["prompt"]
    assert GROUND_TRUTH not in captured["prompt"]


# ---- Clarification flow (/round/2/auto/clarify) ----

def test_clarify_asks_a_neutral_question_and_writes_no_code(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))
    baseline_code = _state(client, cand_token)["tc_state"][0]["code"]

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"status": "insufficient", "question": "What exactly should happen when this step runs, and what should prove it worked?"}))
    res = client.post(
        "/candidate/round/2/auto/clarify",
        json={"candidate_prompt": "submit the amount and confirm it worked"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["response_kind"] == "clarify"
    assert body["code_after"] is None
    assert body["row_index"] == 0
    assert "worked" in body["response_message"]

    state = _state(client, cand_token)
    assert state["tc_state"][0]["code"] == baseline_code  # completely untouched
    assert len(state["tc_state"][0]["turns"]) == 1
    assert state["tc_state"][0]["turns"][0]["response_kind"] == "clarify"


def test_clarify_never_writes_code_even_when_the_instruction_is_complete(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))
    baseline_code = _state(client, cand_token)["tc_state"][0]["code"]

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps({"status": "sufficient"}))
    res = client.post(
        "/candidate/round/2/auto/clarify",
        json={"candidate_prompt": "log in as jordan.rivera and confirm login succeeds"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    assert res.json()["response_kind"] == "explain"
    assert res.json()["code_after"] is None
    assert _state(client, cand_token)["tc_state"][0]["code"] == baseline_code  # still never touched


@pytest.mark.parametrize("leaking_question", [
    "Should this go through the UI or call the API directly?",
    "Do you want to check this via the database?",
    "Which layer should this hit - the front end or the backend?",
])
def test_clarify_substitutes_the_fallback_when_the_drafted_question_leaks_the_layer(client, monkeypatch, leaking_question):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"status": "insufficient", "question": leaking_question}))
    res = client.post(
        "/candidate/round/2/auto/clarify",
        json={"candidate_prompt": "submit the amount"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["response_kind"] == "clarify"
    # The leaking draft never reaches the candidate - a fixed, pre-approved,
    # category-neutral question is substituted instead.
    from app.services.round4_auto_clarify_policy import FALLBACK_QUESTION
    assert body["response_message"] == FALLBACK_QUESTION
    for leaked_word in ("ui", "api", "database", "backend", "front end", "layer"):
        assert leaked_word not in body["response_message"].lower()


@pytest.mark.parametrize("prompt", PROHIBITED)
def test_clarify_reuses_the_existing_prohibited_request_policy(client, monkeypatch, prompt):
    """Preserved, not duplicated: the same is_prohibited check /turn uses,
    so a request to invent coverage/data/assertions can't be routed
    around the guardrail just by calling /clarify instead of /turn."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    calls = {"n": 0}
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: calls.update(n=calls["n"] + 1) or "{}")
    res = client.post("/candidate/round/2/auto/clarify", json={"candidate_prompt": prompt}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["response_kind"] == "refuse"
    assert res.json()["code_after"] is None
    assert calls["n"] == 0, "the deterministic policy must short-circuit before any LLM call"


def test_clarify_requires_row_index_once_two_test_cases_are_selected(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0, 1))

    res = client.post("/candidate/round/2/auto/clarify", json={"candidate_prompt": "submit the amount"}, cookies=_auth(cand_token))
    assert res.status_code == 400
    assert "row_index" in res.json()["detail"]

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps({"status": "sufficient"}))
    res = client.post("/candidate/round/2/auto/clarify", json={"candidate_prompt": "submit the amount", "row_index": 1}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["row_index"] == 1
    state = _state(client, cand_token)
    assert len(state["tc_state"][1]["turns"]) == 1
    assert state["tc_state"][0]["turns"] == []  # the other TC is untouched


def test_clarify_and_turn_share_one_conversation_log_per_tc(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"status": "insufficient", "question": "What exactly should happen when this step runs, and what should prove it worked?"}))
    client.post("/candidate/round/2/auto/clarify", json={"candidate_prompt": "submit it"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded it.", "code_after": PYTHON_ENV + "\nprint('ok')\n"}))
    client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "submit 0.00 and confirm it's rejected via API.create_record"}, cookies=_auth(cand_token))

    turns = _state(client, cand_token)["tc_state"][0]["turns"]
    assert [t["response_kind"] for t in turns] == ["clarify", "code_edit"]
    assert [t["turn_number"] for t in turns] == [1, 2]


def test_untraceable_literal_detection_flags_invented_values_only():
    """Deterministic post-generation control - see round4_auto_policy."""
    selected = [{"test_data": "amount = 0.00", "expected_result": "rejected", "steps": "submit", "title": "t"}]
    flagged = round4_auto_policy.untraceable_literals(
        "a = 0.00\nb = 77.77\nUI.login('nobody@example.com')", selected, environment_code="def login(): pass",
    )
    assert "77.77" in flagged                      # value the candidate never specified
    assert "nobody@example.com" in flagged
    assert "0.00" not in flagged                   # straight from their own test data


def test_clarify_policy_flags_layer_vocabulary_but_not_ordinary_english():
    """Deterministic post-generation control - see round4_auto_clarify_policy.
    Isolated from round3_constructs on purpose (see that module's own
    docstring) - this is its own, much smaller vocabulary."""
    from app.services import round4_auto_clarify_policy as policy

    for leaking in [
        "Should this go through the UI or the API?",
        "Do you want to check the database directly?",
        "Which layer should this hit - the front end or the backend?",
        "Should the test call API.create_record directly?",
    ]:
        assert policy.contains_forbidden_vocab(leaking), leaking

    for safe in [
        "What exactly should happen when this step runs, and what should prove it worked?",
        "What value should be used for the owner field?",
        "How should the result be confirmed?",
    ]:
        assert not policy.contains_forbidden_vocab(safe), safe


def test_placeholder_prefilter_catches_empty_instructions_but_not_short_real_ones():
    """Deterministic pre-filter - see
    round4_auto_clarify_policy.is_placeholder_instruction. Conservative on
    purpose: a short instruction that still says something real ("confirm
    login fails") must NOT be caught here - only genuinely empty ones,
    the false-positive risk this design explicitly called out."""
    from app.services import round4_auto_clarify_policy as policy

    for placeholder in ["test it", "go", "do it", "please automate this", "check it now", "   "]:
        assert policy.is_placeholder_instruction(placeholder), placeholder

    for real in ["confirm login fails", "submit 0.00", "log in as jordan.rivera", "check the message"]:
        assert not policy.is_placeholder_instruction(real), real


def test_build_clarify_response_decision_rule():
    """Pure function, no LLM/HTTP - the deterministic rule that turns the
    LLM's bounded classification into what the candidate sees."""
    from app.services import round4_auto_clarify_policy as policy

    sufficient = policy.build_clarify_response(status="sufficient")
    assert sufficient["response_kind"] == "explain"

    safe = policy.build_clarify_response(status="insufficient", question="What value should be used here?")
    assert safe["response_kind"] == "clarify"
    assert safe["response_message"] == "What value should be used here?"

    leaking = policy.build_clarify_response(status="insufficient", question="Should this use the API?")
    assert leaking["response_kind"] == "clarify"
    assert leaking["response_message"] == policy.FALLBACK_QUESTION  # substituted, not passed through

    contradiction = policy.build_clarify_response(status="contradicts_prior", prior_value="0.00", current_value="50")
    assert contradiction["response_kind"] == "clarify"
    assert "0.00" in contradiction["response_message"]
    assert "50" in contradiction["response_message"]
    assert "which one" in contradiction["response_message"].lower()


def test_clarify_flags_an_instruction_that_contradicts_the_candidates_own_design(client, monkeypatch):
    """contradicts_prior is a distinct outcome from insufficient - the
    candidate said two different things, so the follow-up asks which one
    is current rather than asking a fresh neutral question."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"status": "contradicts_prior", "prior_value": "0.00", "current_value": "50"}))
    res = client.post(
        "/candidate/round/2/auto/clarify",
        json={"candidate_prompt": "actually submit 50 for this one"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["response_kind"] == "clarify"
    assert body["code_after"] is None
    assert "0.00" in body["response_message"]
    assert "50" in body["response_message"]
    # Quoting the candidate's own two statements back is not a category
    # leak - neither value contains layer vocabulary here, and the
    # template itself never mentions UI/API/DB either way.
    for leaked_word in ("ui", "api", "database"):
        assert leaked_word not in body["response_message"].lower()


# ---- Candidate code edit ----

def test_candidate_code_edit_is_recorded_separately_from_ai_output(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    edited = PYTHON_ENV + "\n# my own edit\n"
    res = client.post("/candidate/round/2/auto/code", json={"code": edited}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["tc_state"][0]["code"] == edited
    assert res.json()["tc_state"][0]["code_edits_count"] == 1

    client.post("/candidate/round/2/auto/code", json={"code": edited + "# again\n"}, cookies=_auth(cand_token))
    assert _state(client, cand_token)["tc_state"][0]["code_edits_count"] == 2


# ---- Execution ----

def test_run_executes_the_real_provided_environment_via_the_existing_engine(client, monkeypatch):
    """No execution mocking here - proves the provided Python environment
    genuinely runs under the SAME execution_service.run_code everything
    else uses, unmodified, and that Run uses the editor's current text."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))
    monkeypatch.undo()  # restore the real run_code

    code = PYTHON_ENV + """
def test_reject_zero():
    setup()
    result = API.create_record("jordan.rivera@example.com", 0.00)
    assert result["ok"] is False
    assert Database.find(result["id"]) is None
    print("PASS: rejected zero amount")

test_reject_zero()
"""
    res = client.post("/candidate/round/2/auto/run", json={"code": code}, cookies=_auth(cand_token))
    assert res.status_code == 201
    run = res.json()
    assert run["infra_error"] is False
    assert run["exit_code"] == 0
    assert "PASS: rejected zero amount" in run["stdout"]
    # execution_service.run_code already computes duration_ms - this only
    # confirms it's no longer discarded before reaching the candidate.
    assert isinstance(run["duration_ms"], int) and run["duration_ms"] >= 0
    assert run["ran_at"] is not None


def test_run_result_presentation_fields_are_independent_per_tc(client, monkeypatch):
    """TC ID, duration and timestamp - the per-TC presentation fields -
    for one test case's run must not appear on, or be overwritten by,
    another's."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0, 1))

    res0 = client.post("/candidate/round/2/auto/run", json={"code": "print('tc0')", "row_index": 0}, cookies=_auth(cand_token))
    assert res0.json()["duration_ms"] is not None
    ran_at_0 = res0.json()["ran_at"]

    res1 = client.post("/candidate/round/2/auto/run", json={"code": "print('tc1')", "row_index": 1}, cookies=_auth(cand_token))
    assert res1.json()["duration_ms"] is not None

    state = _state(client, cand_token)
    tc0 = next(t for t in state["tc_state"] if t["row_index"] == 0)
    tc1 = next(t for t in state["tc_state"] if t["row_index"] == 1)
    assert tc0["last_run"]["ran_at"] == ran_at_0
    assert tc1["last_run"]["ran_at"] == res1.json()["ran_at"]
    assert tc0["last_run"]["duration_ms"] is not None
    assert tc1["last_run"]["duration_ms"] is not None


def test_state_tolerates_a_run_recorded_before_duration_and_timestamp_existed(client, monkeypatch):
    """"Where available" - an older last_run dict with neither field must
    still read back cleanly, not 500."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    import app.database as database_module
    from app.models import User, Submission
    db = database_module.SessionLocal()
    user = db.query(User).filter(User.email == CANDIDATE1_EMAIL).first()
    submission = db.query(Submission).filter(Submission.user_id == user.id, Submission.round_number == 2).first()
    content = dict(submission.content)
    content["selected"] = [
        {**row, "last_run": {"stdout": "old", "stderr": "", "exit_code": 0, "timed_out": False, "infra_error": False}}
        for row in content["selected"]
    ]
    submission.content = content
    db.commit()
    db.close()

    state = _state(client, cand_token)
    assert state["tc_state"][0]["last_run"]["duration_ms"] is None
    assert state["tc_state"][0]["last_run"]["ran_at"] is None
    assert state["tc_state"][0]["last_run"]["stdout"] == "old"


def test_run_requires_a_selection_first(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    assert client.post("/candidate/round/2/auto/run", cookies=_auth(cand_token)).status_code == 400


# ---- Independent per-TC state ----

def test_row_index_is_required_once_two_test_cases_are_selected(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0, 1))

    for path, body in [
        ("/candidate/round/2/auto/turn", {"candidate_prompt": "encode it"}),
        ("/candidate/round/2/auto/code", {"code": "x = 1"}),
        ("/candidate/round/2/auto/run", {}),
    ]:
        res = client.post(path, json=body, cookies=_auth(cand_token))
        assert res.status_code == 400, path
        assert "row_index" in res.json()["detail"]

    # An invalid row_index (not one of the selected ones) is also rejected.
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode it", "row_index": 2}, cookies=_auth(cand_token))
    assert res.status_code == 400
    assert "2" in res.json()["detail"]


def test_ai_turns_on_one_tc_never_touch_the_others_code_or_turns(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0, 1))

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded TC0.", "code_after": "print('tc0 code')"}))
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode my first case", "row_index": 0}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert res.json()["row_index"] == 0

    state = _state(client, cand_token)
    tc0 = next(t for t in state["tc_state"] if t["row_index"] == 0)
    tc1 = next(t for t in state["tc_state"] if t["row_index"] == 1)
    assert tc0["code"] == "print('tc0 code')"
    assert len(tc0["turns"]) == 1
    # TC1 is completely untouched - still its own environment-seeded code, no turns.
    assert tc1["code"] != tc0["code"]
    assert "class UI:" in tc1["code"]
    assert tc1["turns"] == []

    # A direct edit to TC1 leaves TC0 alone too.
    client.post("/candidate/round/2/auto/code", json={"code": "x = 'tc1 edit'", "row_index": 1}, cookies=_auth(cand_token))
    state = _state(client, cand_token)
    tc0 = next(t for t in state["tc_state"] if t["row_index"] == 0)
    tc1 = next(t for t in state["tc_state"] if t["row_index"] == 1)
    assert tc1["code"] == "x = 'tc1 edit'"
    assert tc1["code_edits_count"] == 1
    assert tc0["code"] == "print('tc0 code')"      # unchanged
    assert tc0["code_edits_count"] == 0             # unchanged


def test_each_tc_runs_independently_starting_from_its_own_code(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0, 1))

    # execution_service.run_code is mocked (see _reach_automation_round) -
    # this test is about which TC a run attaches to, not real output.
    res0 = client.post("/candidate/round/2/auto/run", json={"code": "print('run tc0')", "row_index": 0}, cookies=_auth(cand_token))
    assert res0.status_code == 201
    assert res0.json()["exit_code"] == 0

    state = _state(client, cand_token)
    tc0 = next(t for t in state["tc_state"] if t["row_index"] == 0)
    tc1 = next(t for t in state["tc_state"] if t["row_index"] == 1)
    assert tc0["code"] == "print('run tc0')"
    assert tc0["last_run"] is not None
    # TC1 was never run and never edited - its own last_run and code are
    # untouched by TC0's run.
    assert tc1["last_run"] is None
    assert tc1["code"] != "print('run tc0')"
    assert "class UI:" in tc1["code"]


def test_submit_requires_entries_once_two_test_cases_are_selected(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0, 1))
    client.post("/candidate/round/2/auto/run", json={"code": "x = 1", "row_index": 0}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/run", json={"code": "x = 1", "row_index": 1}, cookies=_auth(cand_token))

    # The old flat single-TC shape is ambiguous once two are selected.
    res = client.post("/candidate/round/2/auto/submit", json={"validation": "proves it"}, cookies=_auth(cand_token))
    assert res.status_code == 400
    assert "entries" in res.json()["detail"].lower()


def test_submit_validates_each_selected_tc_independently(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0, 1))
    client.post("/candidate/round/2/auto/run", json={"code": "x = 1", "row_index": 0}, cookies=_auth(cand_token))
    # TC1 deliberately never run.

    res = client.post("/candidate/round/2/auto/submit", json={"entries": [
        {"row_index": 0, "validation": "TC0 proves rejection."},
        {"row_index": 1, "validation": "TC1 proves persistence."},
    ]}, cookies=_auth(cand_token))
    assert res.status_code == 400
    assert "1" in res.json()["detail"]  # names the un-run test case

    client.post("/candidate/round/2/auto/run", json={"code": "y = 2", "row_index": 1}, cookies=_auth(cand_token))
    monkeypatch.setattr(llm_service, "score_round4_auto_conversation", lambda **k: {
        "scores": {"automation_design": 20, "test_data_and_assertions": 20, "ai_usage": 20,
                   "ai_output_review": 20, "execution_and_validation": 20},
        "final_score": 100, "findings": [], "feedback_text": "ok",
    })
    res = client.post("/candidate/round/2/auto/submit", json={"entries": [
        {"row_index": 0, "validation": "TC0 proves rejection."},
        {"row_index": 1, "validation": "TC1 proves persistence."},
    ]}, cookies=_auth(cand_token))
    assert res.status_code == 201


# ---- Submission + scoring/audit ----

def _submit_payload(code="x = 1\n", validation="The run proves the zero amount was rejected and nothing persisted."):
    return {"code": code, "validation": validation}


def test_submit_requires_a_run_and_an_interpretation(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    # No interpretation -> business rejection. (validation moved from a
    # schema-required field to an endpoint-level check when submit grew
    # the entries= shape for multiple independently-validated test cases -
    # pydantic can't express "required unless entries is given" cleanly.)
    res = client.post("/candidate/round/2/auto/submit", json={"code": "x = 1"}, cookies=_auth(cand_token))
    assert res.status_code == 400
    assert "explain" in res.json()["detail"].lower()
    # Never run -> business rejection.
    res = client.post("/candidate/round/2/auto/submit", json=_submit_payload(), cookies=_auth(cand_token))
    assert res.status_code == 400
    assert "run" in res.json()["detail"].lower()


# ---- Pilot challenges: a weak AI-generated assertion, or controlled app
# behavior contradicting R1's expected result. Both are authored into the
# scenario's existing HR-only ground_truth/validation_notes - no new
# field or mechanism - so "do not reveal which TC or what caused it"
# reduces to an existing guarantee (ScenarioPublicOut/SubmissionOut
# already exclude reference_json - see their own docstrings) that this
# test exercises directly against a ground_truth actually describing a
# challenge, across every candidate-facing R2 response. ----

def test_challenge_ground_truth_never_reaches_any_candidate_facing_response(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    challenge_text = "CHALLENGE-WEAK-ASSERTION-ROW-0-TRAP"
    validation_notes_text = "CHALLENGE-CONTRADICTS-EXPECTED-RESULT-TRAP"
    import app.database as database_module
    from app.models import Scenario
    db = database_module.SessionLocal()
    scenario = db.query(Scenario).filter(Scenario.round_number == 2).one()
    scenario.reference_json = {"ground_truth": challenge_text, "validation_notes": validation_notes_text}
    db.commit()
    db.close()

    bodies = []

    bodies.append(_state(client, cand_token))

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded it.", "code_after": "print('x')"}))
    bodies.append(client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode step 1"}, cookies=_auth(cand_token)).json())

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps({"status": "sufficient"}))
    bodies.append(client.post("/candidate/round/2/auto/clarify", json={"candidate_prompt": "submit the amount"}, cookies=_auth(cand_token)).json())

    bodies.append(client.post("/candidate/round/2/auto/run", json={"code": "print('x')"}, cookies=_auth(cand_token)).json())

    monkeypatch.setattr(llm_service, "score_round4_auto_conversation", lambda **k: {
        "scores": {"automation_design": 20, "test_data_and_assertions": 20, "ai_usage": 20,
                   "ai_output_review": 20, "execution_and_validation": 20},
        "final_score": 100, "findings": [], "feedback_text": "ok",
    })
    bodies.append(client.post("/candidate/round/2/auto/submit", json=_submit_payload(), cookies=_auth(cand_token)).json())
    bodies.append(client.get("/candidate/submissions", cookies=_auth(cand_token)).json())
    bodies.append(client.get("/candidate/round/2", cookies=_auth(cand_token)).json())

    for body in bodies:
        blob = json.dumps(body)
        assert challenge_text not in blob
        assert validation_notes_text not in blob
        assert "ground_truth" not in blob
        assert "validation_notes" not in blob


def test_full_submission_scores_and_audits_via_the_existing_patterns(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))
    client.post("/candidate/round/2/auto/refine", json={"row_index": 0, "note": "exact-match message"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded it.", "code_after": "print('x')"}))
    client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode my step 1"}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/code", json={"code": "print('my edit')"}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/run", json={"code": "print('my edit')"}, cookies=_auth(cand_token))

    captured = {}

    def _fake_scoring(**kwargs):
        captured.update(kwargs)
        return {
            "scores": {"automation_design": 16, "test_data_and_assertions": 14, "ai_usage": 15,
                       "ai_output_review": 13, "execution_and_validation": 12},
            "final_score": 70,
            "findings": [{"claim": "Asked the assistant to encode without stating the assertion",
                          "severity": "low",
                          "evidence": [{"turn": 1, "quote": "encode my step 1"}]}],
            "feedback_text": "Solid, but verify what the assertion proves.",
        }

    monkeypatch.setattr(llm_service, "score_round4_auto_conversation", _fake_scoring)

    res = client.post("/candidate/round/2/auto/submit", json=_submit_payload(validation="Proves rejection; does not prove persistence."), cookies=_auth(cand_token))
    assert res.status_code == 201

    # The scorer got the full primary-evidence set, as one self-contained
    # per-TC block, plus REFERENCE-ONLY ground truth.
    tc0 = captured["tc_evidence"][0]
    assert tc0["design"]["title"] == "Reject zero amount"
    assert tc0["design"]["refinements"] == ["exact-match message"]
    assert len(tc0["turns"]) == 1
    assert len(tc0["code_edits"]) == 1
    assert tc0["validation"].startswith("Proves rejection")
    assert captured["ground_truth"] == GROUND_TRUTH
    assert captured["language"] == "python"

    # HR sees the score, the surviving finding, and the evidence audit.
    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    r4 = next(r for r in candidate_row["rounds"] if r["round_number"] == 2)
    assert r4["final_score"] == 70

    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_r4 = next(s for s in res.json() if s["round_number"] == 2)
    assert report_r4["score"]["misses_json"] == ["Asked the assistant to encode without stating the assertion"]
    assert report_r4["score"]["evidence_audit"]["total_findings"] == 1

    # Candidate still never sees any score.
    res = client.get("/candidate/submissions", cookies=_auth(cand_token))
    assert "score" not in next(s for s in res.json() if s["round_number"] == 2)


def test_auto_tc_audit_payload_builds_one_real_entry_per_selected_tc():
    """Pure function, no LLM/HTTP - see scoring_service._auto_tc_audit_payload.
    Replaces the old single synthetic "Automation session" entry: titles
    are index-prefixed (unique even if two test cases share a
    candidate-authored title), and each test case's own turns keep their
    own turn_number (restarting at 1 per test case is fine here - only
    the AUDIT's flattened position, computed from this list's order,
    is what a citation's "turn" integer actually means)."""
    from app.services.scoring_service import _auto_tc_audit_payload

    selected = [
        {"index": 0, "title": "Reject zero amount", "turns": [
            {"turn_number": 1, "candidate_prompt": "encode TC0 step", "response_message": "Encoded TC0."},
        ]},
        {"index": 1, "title": "Accept valid amount", "turns": [
            {"turn_number": 1, "candidate_prompt": "encode TC1 step", "response_message": "Encoded TC1 distinctively."},
        ]},
    ]
    payload = _auto_tc_audit_payload(selected)
    assert len(payload) == 2
    assert payload[0]["title"] == "Test case 0: Reject zero amount"
    assert payload[1]["title"] == "Test case 1: Accept valid amount"
    # response_message remapped to model_response - what round4_evidence_audit's
    # _turn_text actually reads (see the remapping this mirrors).
    assert payload[0]["turns"][0]["model_response"] == "Encoded TC0."
    assert payload[1]["turns"][0]["model_response"] == "Encoded TC1 distinctively."

    # Single-TC behavior: exactly one entry, unchanged shape otherwise.
    single = _auto_tc_audit_payload(selected[:1])
    assert len(single) == 1
    assert single[0]["title"] == "Test case 0: Reject zero amount"


def test_evidence_audit_never_attributes_one_tcs_turn_to_the_other(client, monkeypatch):
    """Reproduces the exact collision shape: two selected test cases each
    with their OWN turn 1 (turn_number restarts per test case - see
    _auto_tc_audit_payload). A finding correctly citing the flattened
    position of test case 1's own turn must be SUPPORTED; a finding
    citing test case 0's position while quoting test case 1's text must
    NOT be - proving test case 1's evidence can never be validated
    against test case 0's turn, or vice versa."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0, 1))

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded TC0.", "code_after": "print('tc0')"}))
    client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode tc0 step", "row_index": 0}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded TC1.", "code_after": "print('tc1')"}))
    client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode tc1 step", "row_index": 1}, cookies=_auth(cand_token))

    client.post("/candidate/round/2/auto/run", json={"code": "print('tc0')", "row_index": 0}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/run", json={"code": "print('tc1')", "row_index": 1}, cookies=_auth(cand_token))

    # Flattened order is TC0's turn (position 1) then TC1's turn (position 2).
    monkeypatch.setattr(llm_service, "score_round4_auto_conversation", lambda **k: {
        "scores": {"automation_design": 18, "test_data_and_assertions": 18, "ai_usage": 18,
                   "ai_output_review": 18, "execution_and_validation": 18},
        "final_score": 90,
        "findings": [
            {"claim": "Test case 1: correctly cited at the real flattened position",
             "severity": "low", "evidence": [{"turn": 2, "quote": "encode tc1 step"}]},
            {"claim": "Test case 1: wrongly cited at test case 0's position",
             "severity": "low", "evidence": [{"turn": 1, "quote": "encode tc1 step"}]},
        ],
        "feedback_text": "ok",
    })
    entries = [
        {"row_index": 0, "validation": "TC0 run proves rejection."},
        {"row_index": 1, "validation": "TC1 run proves persistence."},
    ]
    res = client.post("/candidate/round/2/auto/submit", json={"entries": entries}, cookies=_auth(cand_token))
    assert res.status_code == 201

    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_r2 = next(s for s in res.json() if s["round_number"] == 2)

    # Only the correctly-attributed finding survives - the misattributed
    # one is dropped and its deduction refunded, not silently credited.
    assert report_r2["score"]["misses_json"] == ["Test case 1: correctly cited at the real flattened position"]
    ea = report_r2["score"]["evidence_audit"]
    assert ea["total_findings"] == 2
    assert ea["supported"] == 1
    assert ea["not_established"] == 1
    assert report_r2["score"]["final_score"] == 93  # 90 + one low-severity refund (3), the other stands


def test_unsupported_finding_is_dropped_by_the_shared_evidence_audit(client, monkeypatch):
    """The automation round reuses round4_evidence_audit unmodified - a
    finding citing a quote that was never said must not survive."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))
    client.post("/candidate/round/2/auto/run", json={"code": "print(1)"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "score_round4_auto_conversation", lambda **k: {
        "scores": {"automation_design": 10, "test_data_and_assertions": 10, "ai_usage": 10,
                   "ai_output_review": 10, "execution_and_validation": 10},
        "final_score": 50,
        "findings": [{"claim": "Said something they never said", "severity": "high",
                      "evidence": [{"turn": 1, "quote": "this quote does not exist anywhere"}]}],
        "feedback_text": "x",
    })

    client.post("/candidate/round/2/auto/submit", json=_submit_payload(), cookies=_auth(cand_token))

    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_r4 = next(s for s in res.json() if s["round_number"] == 2)
    assert report_r4["score"]["misses_json"] == []          # dropped
    assert report_r4["score"]["final_score"] > 50           # its severity points were restored


# ---- Legacy scenario compatibility ----

def test_legacy_round1_rows_without_test_data_still_work(client, monkeypatch):
    """A candidate whose round 1 predates the test_data field must still
    be able to select and automate - the field reads back as ""."""
    legacy_rows = [{"title": "Old shape", "steps": "do it", "expected_result": "it happens"}]
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch, r1_rows=legacy_rows)

    state = _state(client, cand_token)
    assert state["available_rows"][0]["test_data"] == ""
    res = _select(client, cand_token, (0,))
    assert res.status_code == 201
    assert res.json()["selected"][0]["test_data"] == ""


def test_auto_endpoints_reject_a_non_automation_round4_scenario(client, monkeypatch):
    """The other two round 4 modes are untouched: their scenarios must not
    be drivable through the automation endpoints."""
    from .conftest import _publish_round4_scenario
    from .test_round4 import _complete_round1_and_2

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    _publish_round4_scenario(client, hr_token, monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    res = client.get("/candidate/round/2/auto/state", cookies=_auth(cand_token))
    assert res.status_code == 400
    assert "not an AI-assisted automation scenario" in res.json()["detail"]


def test_publish_gate_requires_ground_truth_and_environment(client, monkeypatch):
    from .conftest import FAKE_ENVIRONMENT, FAKE_UI_MOCKUP
    monkeypatch.setattr(llm_service, "generate_round4_environment", lambda **kwargs: dict(FAKE_ENVIRONMENT))
    monkeypatch.setattr(llm_service, "generate_round4_ui_mockup", lambda **kwargs: dict(FAKE_UI_MOCKUP))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = client.post(
        "/hr/scenarios",
        json={"round_number": 2, "title": "Incomplete automation", "description": "d",
              "experience_band": "0-7", "time_limit_minutes": 45,
              "config_json": {"mode": "ai_test_automation"}},
        cookies=_auth(hr_token),
    ).json()

    res = client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 400
    assert "ground truth" in res.json()["detail"].lower()


# ---- Escaped-code repair (see llm_service._repair_escaped_code) ----
# Observed live during end-to-end validation: the model sometimes escapes
# its own JSON escapes, so code_after arrives as a single line containing
# literal backslash-n pairs. json.loads returns that faithfully and the
# generated automation then fails with a SyntaxError on line 1 - unusable.

def test_escaped_code_from_the_model_is_repaired():
    """A whole file arriving escaped - the shape seen live, where 137
    escaped pairs sat behind a single trailing real newline. The fixture
    keeps several escaped lines on purpose: the guard ignores blocks with
    fewer than 3, so that a short snippet whose only escapes are genuine
    string literals is never rewritten (see _repair_escaped_code)."""
    from app.services.llm_service import _repair_escaped_code
    bs = chr(92)
    escaped = (
        "def t():" + bs + "n"
        "    x = 1" + bs + "n"
        "    y = 2" + bs + "n"
        "    print(" + bs + '"ok' + bs + '")' + bs + "n"
        "    return x + y" + "\n"   # the stray real newline that defeated the first guard
    )
    repaired = _repair_escaped_code(escaped)
    assert repaired.count("\n") == 5
    assert bs + "n" not in repaired
    compile(repaired, "<generated>", "exec")  # the whole point: it must be runnable


def test_well_formed_code_is_never_touched():
    """The guard is narrow on purpose - anything with real newlines (i.e.
    every well-formed multi-line file) must pass through untouched, so a
    deliberate literal backslash-n inside normal code can't be mangled."""
    from app.services.llm_service import _repair_escaped_code
    good = 'def t():\n    print("a\nb")\n'
    assert _repair_escaped_code(good) == good
    assert _repair_escaped_code("x = 1") == "x = 1"
    assert _repair_escaped_code(None) is None
    assert _repair_escaped_code("") == ""


def test_auto_turn_repairs_escaped_code_end_to_end(monkeypatch):
    """Through the real round4_auto_turn, not just the helper."""
    import json as _json
    from app.services import llm_service
    bs = chr(92)
    payload = {"response_kind": "code_edit", "response_message": "done",
               "code_after": "def t():" + bs + "n    x = 1" + bs + "n    y = 2" + bs + "n    return x + y"}
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: _json.dumps(payload))
    out = llm_service.round4_auto_turn(
        language="python", selected_design=[{"title": "t"}], environment_code="",
        current_code="", conversation_so_far=[], candidate_prompt="encode my step 1",
    )
    assert out["code_after"] == "def t():\n    x = 1\n    y = 2\n    return x + y"


# ---- Scoring fidelity: a supported finding must survive to the Score ----
# End-to-end counterpart to test_round4_evidence_audit.py's unit coverage:
# proves the persisted evidence actually reaches the auditor through
# score_round4_auto_submission, not just that the auditor can use it.

def _score_with_finding(client, monkeypatch, hr_token, cand_token, finding, final_score=98):
    from app.services import llm_service as _llm
    _select(client, cand_token, (0,))
    client.post("/candidate/round/2/auto/code", json={"code": "print('x')\nassert 1 == 1\n"}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/run", json={"code": "print('x')\nassert 1 == 1\n"}, cookies=_auth(cand_token))
    monkeypatch.setattr(_llm, "score_round4_auto_conversation", lambda **k: {
        "scores": {"automation_design": 20, "test_data_and_assertions": 20, "ai_usage": 20,
                   "ai_output_review": 20, "execution_and_validation": 18},
        "final_score": final_score, "findings": [finding], "feedback_text": "ok",
    })
    res = client.post(
        "/candidate/round/2/auto/submit",
        json={"code": "print('x')\nassert 1 == 1\n",
              "validation": "The run proves the assertion held, but it does not prove persistence."},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    res = client.get(f"/hr/candidates/{row['id']}/report", cookies=_auth(hr_token))
    return next(s for s in res.json() if s["round_number"] == 2)["score"]


def test_finding_quoting_the_candidates_own_interpretation_is_not_refunded(client, monkeypatch):
    """The 98->100 defect, end to end: this quote is real text the
    candidate wrote, so the deduction must stand at 98."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    score = _score_with_finding(client, monkeypatch, hr_token, cand_token, {
        "claim": "Interpretation concedes persistence was not proven.",
        "severity": "low",
        "evidence": [{"quote": "it does not prove persistence"}],
    })
    assert score["final_score"] == 98, "a supported finding's deduction must be preserved"
    assert score["misses_json"] == ["Interpretation concedes persistence was not proven."]
    assert score["evidence_audit"]["supported"] == 1
    assert score["evidence_audit"]["not_established"] == 0


def test_fabricated_finding_is_still_dropped_and_refunded(client, monkeypatch):
    """The auditor's original job is unchanged by the fix."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    score = _score_with_finding(client, monkeypatch, hr_token, cand_token, {
        "claim": "The candidate said the run crashed.",
        "severity": "low",
        "evidence": [{"quote": "the run crashed catastrophically"}],
    })
    # 98 + 3 restored, capped at 100 by _round4_findings_to_misses - the
    # same arithmetic that produced the original 98->100 symptom. Here it
    # is CORRECT, because this finding genuinely cites nothing real.
    assert score["final_score"] == 100
    assert score["misses_json"] == []
    assert score["evidence_audit"]["not_established"] == 1


# ---- R2 final scoring: self-contained per-TC evidence blocks ----
# tc_evidence replaces the old flattened/concatenated multi-TC state (see
# scoring_service._auto_tc_evidence_blocks) - each selected test case's
# design, code, turns, code edits, execution result and validation must
# reach the scorer as its OWN block, never merged with another selected
# test case's.

def test_two_tc_evidence_blocks_are_fully_isolated(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0, 1))

    # Refinement attribution: only TC0 gets a refinement note.
    client.post("/candidate/round/2/auto/refine", json={"row_index": 0, "note": "tc0-only refinement"}, cookies=_auth(cand_token))

    # Clarification attribution: only TC0 gets a clarify turn.
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"status": "insufficient", "question": "What exactly should prove this step worked?"}))
    client.post("/candidate/round/2/auto/clarify", json={"candidate_prompt": "submit it", "row_index": 0}, cookies=_auth(cand_token))

    # A code_edit turn and a direct edit on TC1 only.
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded TC1.", "code_after": "print('tc1 ai code')"}))
    client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode tc1 step", "row_index": 1}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/code", json={"code": "print('tc1 edited')", "row_index": 1}, cookies=_auth(cand_token))

    # Execution isolation: distinct output per TC. _reach_automation_round
    # mocks execution_service.run_code to always return empty stdout, so
    # echo the code back as stdout here to tell the two runs apart.
    monkeypatch.setattr(execution_service, "run_code", lambda **k: execution_service.ExecutionResult(
        stdout=k["code"], stderr="", exit_code=0, timed_out=False, infra_error=False, duration_ms=1,
    ))
    client.post("/candidate/round/2/auto/run", json={"code": "print('tc0 output')", "row_index": 0}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/run", json={"code": "print('tc1 edited')", "row_index": 1}, cookies=_auth(cand_token))

    captured = {}

    def _fake_scoring(**kwargs):
        captured.update(kwargs)
        return {
            "scores": {"automation_design": 16, "test_data_and_assertions": 16, "ai_usage": 16,
                       "ai_output_review": 16, "execution_and_validation": 16},
            "final_score": 80, "findings": [], "feedback_text": "ok",
        }

    monkeypatch.setattr(llm_service, "score_round4_auto_conversation", _fake_scoring)

    entries = [
        {"row_index": 0, "validation": "TC0 run proves rejection."},
        {"row_index": 1, "validation": "TC1 run proves persistence."},
    ]
    res = client.post("/candidate/round/2/auto/submit", json={"entries": entries}, cookies=_auth(cand_token))
    assert res.status_code == 201

    blocks = captured["tc_evidence"]
    assert len(blocks) == 2
    tc0, tc1 = blocks
    assert tc0["label"] == "Test case 0: Reject zero amount"
    assert tc1["label"] == "Test case 1: Accept a valid amount"

    # Refinement attribution - TC0's own note never leaks into TC1.
    assert tc0["design"]["refinements"] == ["tc0-only refinement"]
    assert tc1["design"]["refinements"] == []

    # Clarification attribution - the clarify turn stays on TC0 alone.
    assert [t["response_kind"] for t in tc0["turns"]] == ["clarify"]
    assert [t["response_kind"] for t in tc1["turns"]] == ["code_edit"]

    # Code edit attribution - TC1's own direct edit never leaks into TC0.
    assert len(tc1["code_edits"]) == 1
    assert tc0["code_edits"] == []

    # Execution isolation - each TC's own run result, not the other's.
    assert "tc0 output" in tc0["execution_result"]["stdout"]
    assert "tc0 output" not in tc1["execution_result"]["stdout"]
    assert "tc1 edited" in tc1["execution_result"]["stdout"]
    assert "tc1 edited" not in tc0["execution_result"]["stdout"]

    # Validation attribution.
    assert tc0["validation"] == "TC0 run proves rejection."
    assert tc1["validation"] == "TC1 run proves persistence."


def test_quote_evidence_tagged_with_wrong_test_case_is_rejected_end_to_end(client, monkeypatch):
    """A finding quotes TC1's own validation text but tags the citation as
    TC0's evidence (evidence.test_case) - must be discarded and refunded,
    not silently matched because the quote is real text somewhere in the
    submission. See round4_evidence_audit._check_evidence's "wrong_test_case"
    branch."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0, 1))
    client.post("/candidate/round/2/auto/run", json={"code": "print('tc0')", "row_index": 0}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/run", json={"code": "print('tc1')", "row_index": 1}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "score_round4_auto_conversation", lambda **k: {
        "scores": {"automation_design": 18, "test_data_and_assertions": 18, "ai_usage": 18,
                   "ai_output_review": 18, "execution_and_validation": 18},
        "final_score": 90,
        "findings": [{
            "claim": "Test case 0: interpretation overstates what the run proves.",
            "severity": "low",
            "evidence": [{"quote": "TC1 run proves persistence, uniquely worded.",
                          "test_case": "Test case 0: Reject zero amount"}],
        }],
        "feedback_text": "ok",
    })
    entries = [
        {"row_index": 0, "validation": "TC0 run proves rejection."},
        {"row_index": 1, "validation": "TC1 run proves persistence, uniquely worded."},
    ]
    res = client.post("/candidate/round/2/auto/submit", json={"entries": entries}, cookies=_auth(cand_token))
    assert res.status_code == 201

    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_r2 = next(s for s in res.json() if s["round_number"] == 2)

    assert report_r2["score"]["misses_json"] == []
    assert report_r2["score"]["evidence_audit"]["not_established"] == 1
    assert report_r2["score"]["final_score"] == 93  # 90 + one low-severity refund (3)
