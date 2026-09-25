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

from app.services import llm_service, round2_automation_policy, execution_service

HELPERS_DIR = Path(__file__).parent.parent / "backend" / "app" / "prompts"
PYTHON_ENV = (HELPERS_DIR / "round2_automation_helpers_python.txt").read_text(encoding="utf-8")

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

# The clarifying gate only runs for a design with something missing (a complete
# one is its own specification - round2_automation_clarify_policy "own design"),
# so the gate's own tests use designs without test data.
GATE_ROWS = [{**row, "test_data": ""} for row in R1_ROWS]

GROUND_TRUTH = "create_record rejects amount <= 0 and writes nothing; Database.find is the only proof of persistence."


def _publish_auto_scenario(client, hr_token, monkeypatch, band="0-7"):
    """Creates + publishes the automation scenario directly (its config/
    reference are HR/system-authored, not LLM-generated - see
    seed_round2_automation.py, which this mirrors without touching the real DB).

    create_scenario (hr.py) unconditionally calls _generate_reference for
    every round_number==2 scenario regardless of config_json["mode"] - it
    has no ai_test_automation-specific branch, so creating even this
    scenario triggers the legacy round4 environment/UI-mockup generator.
    Mocked the same way _publish_round4_scenario already does, so this
    helper never makes a real LLM call."""
    from .conftest import FAKE_ENVIRONMENT, FAKE_UI_MOCKUP
    monkeypatch.setattr(llm_service, "generate_round2_automation_environment", lambda **kwargs: dict(FAKE_ENVIRONMENT))
    monkeypatch.setattr(llm_service, "generate_round2_automation_ui_mockup", lambda **kwargs: dict(FAKE_UI_MOCKUP))

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
    round2_automation_select's guard). The round 3/4 calls below don't actually
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


def _sequential_call_claude(monkeypatch, *bodies):
    """A _call_claude replacement returning each JSON string in order,
    repeating the last one for any call beyond the given sequence. A test
    case's FIRST /turn call now goes through the clarify-then-generate
    gate (see routers/candidate.py's round2_automation_turn) - two underlying
    _call_claude invocations, not one - so most /turn mocks need two
    canned replies, not one."""
    calls = {"n": 0}

    def _call(*a, **k):
        i = min(calls["n"], len(bodies) - 1)
        calls["n"] += 1
        return bodies[i]

    monkeypatch.setattr(llm_service, "_call_claude", _call)
    return calls


_SUFFICIENT = json.dumps({"status": "sufficient"})


def _unlock(client, monkeypatch, cand_token, row_index=None, code="print('start')", prompt="encode step 1"):
    """Gets one selected test case past its first-generation gate with a
    throwaway code_edit, so /code and /run (locked until then - see
    _require_tc_unlocked) become usable. Leaves _call_claude mocked to
    whatever the caller sets up afterward."""
    _sequential_call_claude(monkeypatch, _SUFFICIENT, json.dumps(
        {"response_kind": "code_edit", "response_message": "ok", "code_after": code}))
    body = {"candidate_prompt": prompt}
    if row_index is not None:
        body["row_index"] = row_index
    res = client.post("/candidate/round/2/auto/turn", json=body, cookies=_auth(cand_token))
    assert res.status_code == 201 and res.json()["response_kind"] == "code_edit", res.text
    return res


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


def test_state_exposes_the_reused_environment_and_ui_mockup_reference(client, monkeypatch):
    """The candidate-facing state reuses whatever the scenario already
    generated at creation time (see _publish_auto_scenario's own
    docstring - create_scenario runs the legacy generator unconditionally
    for every round_number==2 scenario) - no new generation for this
    round, just surfacing what's already there."""
    from .conftest import FAKE_ENVIRONMENT, FAKE_UI_MOCKUP
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    state = _state(client, cand_token)
    assert state["environment"] == FAKE_ENVIRONMENT
    assert state["ui_mockup"] == FAKE_UI_MOCKUP


def test_candidate_can_correct_their_own_test_data_without_touching_round1(client, monkeypatch):
    """A candidate-authored correction to test data, for automation
    purposes only - never overwrites the immutable Round 1 record."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)

    def _stored_r1():
        res = client.get("/hr/candidates", cookies=_auth(hr_token))
        candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
        res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
        return next(s for s in res.json() if s["round_number"] == 1)["content"]

    before = _stored_r1()
    _select(client, cand_token, (0,))
    res = client.post(
        "/candidate/round/2/auto/test-data",
        json={"row_index": 0, "test_data": "owner = jordan.rivera@example.com; amount = 5.00"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    assert res.json()["selected"][0]["test_data"] == "owner = jordan.rivera@example.com; amount = 5.00"

    # The Round 1 record itself is completely untouched.
    assert _stored_r1() == before
    # So is the original test_data still readable from the R1 table.
    assert _state(client, cand_token)["available_rows"][0]["test_data"] == R1_ROWS[0]["test_data"]


def test_a_second_test_case_can_be_added_one_at_a_time_up_to_the_cap(client, monkeypatch):
    """The candidate automates one test case, then decides whether to add
    a second - selection is no longer a single upfront pick-both-then-
    lock action. A row already added is still immutable: re-selecting it
    is rejected, and so is a third once the two-test-case cap is hit."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    assert _select(client, cand_token, (0,)).status_code == 201

    # Re-selecting the one already-added row, before the cap is even
    # reached, is its own rejection - distinct from the cap check below.
    res = _select(client, cand_token, (0,))
    assert res.status_code == 400
    assert "already selected" in res.json()["detail"].lower()

    res = _select(client, cand_token, (1,))
    assert res.status_code == 201
    assert [r["index"] for r in res.json()["selected"]] == [0, 1]

    res = _select(client, cand_token, (2,))
    assert res.status_code == 400
    assert "two test cases" in res.json()["detail"].lower()
    assert [r["index"] for r in _state(client, cand_token)["selected"]] == [0, 1]


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
    _unlock(client, monkeypatch, cand_token)
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
    calls = {"n": 0}

    def _fake_call(prompt, max_tokens=4096):
        # First call is this test case's gating sufficiency check (see
        # round2_automation_turn's clarify-then-generate gate) - only the
        # second is the actual generation call whose prompt matters here.
        calls["n"] += 1
        if calls["n"] == 1:
            return _SUFFICIENT
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
    cand_token = _reach_automation_round(client, hr_token, monkeypatch, r1_rows=GATE_ROWS)
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
    cand_token = _reach_automation_round(client, hr_token, monkeypatch, r1_rows=GATE_ROWS)
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
    # Replaced by a question naming what the design is actually missing
    # (here its test data) - not the layer, and not one generic sentence.
    assert body["response_message"] == "Which test data should the test use?"
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
    cand_token = _reach_automation_round(client, hr_token, monkeypatch, r1_rows=GATE_ROWS)
    _select(client, cand_token, (0,))

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"status": "insufficient", "question": "What exactly should happen when this step runs, and what should prove it worked?"}))
    client.post("/candidate/round/2/auto/clarify", json={"candidate_prompt": "submit it"}, cookies=_auth(cand_token))

    # This TC is still locked (the clarify call above never records a
    # code_edit) - its own /turn call goes through the gate's own
    # sufficiency check first, so this needs two canned replies too.
    _sequential_call_claude(monkeypatch, _SUFFICIENT, json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded it.", "code_after": PYTHON_ENV + "\nprint('ok')\n"}))
    client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "submit 0.00 and confirm it's rejected via API.create_record"}, cookies=_auth(cand_token))

    turns = _state(client, cand_token)["tc_state"][0]["turns"]
    assert [t["response_kind"] for t in turns] == ["clarify", "code_edit"]
    assert [t["turn_number"] for t in turns] == [1, 2]


def test_untraceable_literal_detection_flags_invented_values_only():
    """Deterministic post-generation control - see round2_automation_policy."""
    selected = [{"test_data": "amount = 0.00", "expected_result": "rejected", "steps": "submit", "title": "t"}]
    flagged = round2_automation_policy.untraceable_literals(
        "a = 0.00\nb = 77.77\nUI.login('nobody@example.com')", selected, environment_code="def login(): pass",
    )
    assert "77.77" in flagged                      # value the candidate never specified
    assert "nobody@example.com" in flagged
    assert "0.00" not in flagged                   # straight from their own test data


def test_clarify_policy_flags_layer_vocabulary_but_not_ordinary_english():
    """Deterministic post-generation control - see round2_automation_clarify_policy.
    Isolated from round3_constructs on purpose (see that module's own
    docstring) - this is its own, much smaller vocabulary."""
    from app.services import round2_automation_clarify_policy as policy

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
    round2_automation_clarify_policy.is_placeholder_instruction. Conservative on
    purpose: a short instruction that still says something real ("confirm
    login fails") must NOT be caught here - only genuinely empty ones,
    the false-positive risk this design explicitly called out."""
    from app.services import round2_automation_clarify_policy as policy

    for placeholder in [
        "test it", "go", "do it", "please automate this", "check it now", "   ",
        # The real instruction observed live: padded with enough extra
        # filler words around "just do it" that a narrower word list let
        # it through to the LLM, which then filled the gap with the
        # candidate's own design specifics instead of asking a genuinely
        # open question - see round2_automation_clarify.txt.
        "I want to automate the given test case. please do",
    ]:
        assert policy.is_placeholder_instruction(placeholder), placeholder

    for real in ["confirm login fails", "submit 0.00", "log in as jordan.rivera", "check the message"]:
        assert not policy.is_placeholder_instruction(real), real


def test_build_clarify_response_decision_rule():
    """Pure function, no LLM/HTTP - the deterministic rule that turns the
    LLM's bounded classification into what the candidate sees."""
    from app.services import round2_automation_clarify_policy as policy

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


# ---- Deterministic backstops on the LLM's own contradicts_prior call -
# verified live against the real model that prompt wording alone did not
# reliably stop either failure mode: the model citing a value that only
# exists in the provided environment code as something "the candidate
# said earlier", and the model re-flagging the same already-settled
# scope choice as an unresolved contradiction indefinitely. ----

def test_value_traces_to_candidate_rejects_environment_only_values():
    """Pure function, no LLM/HTTP - see round2_automation_clarify_policy.
    value_traces_to_candidate. A value that only appears in the provided
    environment code (never in the candidate's own design or messages)
    must never be treated as something the candidate said."""
    from app.services import round2_automation_clarify_policy as policy

    design = [{"title": "t", "steps": "log in", "test_data": "amount = 0.00", "expected_result": "rejected"}]
    conversation = [{"candidate_prompt": "submit the amount and confirm it worked"}]

    # Genuinely stated by the candidate (in their design's own field).
    assert policy.value_traces_to_candidate("amount = 0.00", design, conversation) is True
    # Genuinely stated by the candidate (in an earlier message).
    assert policy.value_traces_to_candidate("confirm it worked", design, conversation) is True
    # Never stated by the candidate anywhere - e.g. only in the
    # environment's own base_url config, which is not passed here at all.
    assert policy.value_traces_to_candidate("https://demo.example.test", design, conversation) is False
    assert policy.value_traces_to_candidate("", design, conversation) is False
    assert policy.value_traces_to_candidate(None, design, conversation) is False


def test_contradicts_prior_count_reads_the_fixed_template_marker():
    """Pure function, no LLM/HTTP - see round2_automation_clarify_policy.
    contradicts_prior_count. Counts by matching build_clarify_response's
    own fixed template, the only place this exact wording is generated."""
    from app.services import round2_automation_clarify_policy as policy

    assert policy.contradicts_prior_count([]) == 0
    assert policy.contradicts_prior_count([
        {"candidate_prompt": "x", "response_message": "What should prove it worked?"},
    ]) == 0
    assert policy.contradicts_prior_count([
        {"candidate_prompt": "x", "response_message": 'You said "A" earlier and "B" now for the same thing - which one should the automation use?'},
        {"candidate_prompt": "y", "response_message": "What should prove it worked?"},
        {"candidate_prompt": "z", "response_message": 'You said "C" earlier and "D" now for the same thing - which one should the automation use?'},
    ]) == 2


def test_clarify_never_loops_on_a_repeated_scope_narrowing_contradiction(client, monkeypatch):
    """End to end, through the real (mocked) LLM classification path: a
    genuine first contradiction against the candidate's own design is
    still flagged once, but reaffirming it a second time must resolve to
    sufficient rather than asking the identical question again forever."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch, r1_rows=GATE_ROWS)
    _select(client, cand_token, (0,))

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"status": "contradicts_prior", "prior_value": "Amount must be greater than zero", "current_value": "a different message"}))
    res = client.post(
        "/candidate/round/2/auto/clarify",
        json={"candidate_prompt": "just check for a different message instead"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    assert res.json()["response_kind"] == "clarify"  # first contradiction still flagged

    # Same classification again (candidate reaffirmed) - must NOT loop.
    res = client.post(
        "/candidate/round/2/auto/clarify",
        json={"candidate_prompt": "yes, use the different message, not the original one"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    assert res.json()["response_kind"] == "explain"


def test_clarify_flags_an_instruction_that_contradicts_the_candidates_own_design(client, monkeypatch):
    """contradicts_prior is a distinct outcome from insufficient - the
    candidate said two different things, so the follow-up asks which one
    is current rather than asking a fresh neutral question."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch, r1_rows=GATE_ROWS)
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
    _unlock(client, monkeypatch, cand_token)

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
    _unlock(client, monkeypatch, cand_token)  # unlock while _call_claude is still mocked
    monkeypatch.undo()  # restore the real run_code (and _call_claude, unused from here on)

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
    _unlock(client, monkeypatch, cand_token, row_index=0)
    _unlock(client, monkeypatch, cand_token, row_index=1)

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

    _sequential_call_claude(monkeypatch, _SUFFICIENT, json.dumps(
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
    _unlock(client, monkeypatch, cand_token, row_index=1)
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
    _unlock(client, monkeypatch, cand_token, row_index=0)

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
    _unlock(client, monkeypatch, cand_token, row_index=0)
    _unlock(client, monkeypatch, cand_token, row_index=1)
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
    _unlock(client, monkeypatch, cand_token, row_index=0)
    client.post("/candidate/round/2/auto/run", json={"code": "x = 1", "row_index": 0}, cookies=_auth(cand_token))
    # TC1 deliberately never run (and never even unlocked).

    res = client.post("/candidate/round/2/auto/submit", json={"entries": [
        {"row_index": 0, "validation": "TC0 proves rejection."},
        {"row_index": 1, "validation": "TC1 proves persistence."},
    ]}, cookies=_auth(cand_token))
    assert res.status_code == 400
    assert "1" in res.json()["detail"]  # names the un-run test case

    _unlock(client, monkeypatch, cand_token, row_index=1)
    client.post("/candidate/round/2/auto/run", json={"code": "y = 2", "row_index": 1}, cookies=_auth(cand_token))
    monkeypatch.setattr(llm_service, "score_round2_automation_conversation", lambda **k: {
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

def _submit_payload(code="x = 1\n"):
    return {"code": code}


def test_submit_requires_a_run(client, monkeypatch):
    """No candidate-written interpretation is required to submit - only
    that the selected test case was actually run at least once."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

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

    _sequential_call_claude(monkeypatch, _SUFFICIENT, json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded it.", "code_after": "print('x')"}))
    bodies.append(client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode step 1"}, cookies=_auth(cand_token)).json())

    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps({"status": "sufficient"}))
    bodies.append(client.post("/candidate/round/2/auto/clarify", json={"candidate_prompt": "submit the amount"}, cookies=_auth(cand_token)).json())

    bodies.append(client.post("/candidate/round/2/auto/run", json={"code": "print('x')"}, cookies=_auth(cand_token)).json())

    monkeypatch.setattr(llm_service, "score_round2_automation_conversation", lambda **k: {
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

    _sequential_call_claude(monkeypatch, _SUFFICIENT, json.dumps(
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

    monkeypatch.setattr(llm_service, "score_round2_automation_conversation", _fake_scoring)

    res = client.post("/candidate/round/2/auto/submit", json=_submit_payload(), cookies=_auth(cand_token))
    assert res.status_code == 201

    # The scorer got the full primary-evidence set, as one self-contained
    # per-TC block, plus REFERENCE-ONLY ground truth. No candidate-written
    # validation field - scoring judges final_code/execution_result alone.
    tc0 = captured["tc_evidence"][0]
    assert tc0["design"]["title"] == "Reject zero amount"
    assert tc0["design"]["refinements"] == ["exact-match message"]
    assert len(tc0["turns"]) == 1
    assert len(tc0["code_edits"]) == 1
    assert "validation" not in tc0
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
    # response_message remapped to model_response - what round2_automation_evidence_audit's
    # _turn_text actually reads (see the remapping this mirrors).
    assert payload[0]["turns"][0]["model_response"] == "Encoded TC0."
    assert payload[1]["turns"][0]["model_response"] == "Encoded TC1 distinctively."

    # Single-TC behavior: exactly one entry, unchanged shape otherwise.
    single = _auto_tc_audit_payload(selected[:1])
    assert len(single) == 1
    assert single[0]["title"] == "Test case 0: Reject zero amount"


def test_design_only_uses_the_corrected_test_data_and_keeps_the_original():
    """Pure function, no LLM/HTTP - see scoring_service._auto_tc_design_only.
    Once a candidate corrects their own test data, the design the AI/
    scorer sees uses that correction (it's what the automation actually
    targets), with the original kept alongside only for transparency -
    never silently dropped, never used as the effective value."""
    from app.services.scoring_service import _auto_tc_design_only

    row_uncorrected = {"index": 0, "title": "t", "preconditions": "", "steps": "s",
                        "test_data": "amount = 0.00", "expected_result": "e", "refinements": []}
    design = _auto_tc_design_only(row_uncorrected)
    assert design["test_data"] == "amount = 0.00"
    assert "original_test_data" not in design

    row_corrected = {**row_uncorrected, "test_data_override": "amount = 5.00"}
    design = _auto_tc_design_only(row_corrected)
    assert design["test_data"] == "amount = 5.00"
    assert design["original_test_data"] == "amount = 0.00"


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

    _sequential_call_claude(monkeypatch, _SUFFICIENT, json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded TC0.", "code_after": "print('tc0')"}))
    client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode tc0 step", "row_index": 0}, cookies=_auth(cand_token))

    _sequential_call_claude(monkeypatch, _SUFFICIENT, json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded TC1.", "code_after": "print('tc1')"}))
    client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode tc1 step", "row_index": 1}, cookies=_auth(cand_token))

    client.post("/candidate/round/2/auto/run", json={"code": "print('tc0')", "row_index": 0}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/run", json={"code": "print('tc1')", "row_index": 1}, cookies=_auth(cand_token))

    # Flattened order is TC0's turn (position 1) then TC1's turn (position 2).
    monkeypatch.setattr(llm_service, "score_round2_automation_conversation", lambda **k: {
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
    """The automation round reuses round2_automation_evidence_audit unmodified - a
    finding citing a quote that was never said must not survive."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))
    _unlock(client, monkeypatch, cand_token)
    client.post("/candidate/round/2/auto/run", json={"code": "print(1)"}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "score_round2_automation_conversation", lambda **k: {
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


def test_publish_gate_requires_ground_truth_and_environment(client, monkeypatch):
    from .conftest import FAKE_ENVIRONMENT, FAKE_UI_MOCKUP
    monkeypatch.setattr(llm_service, "generate_round2_automation_environment", lambda **kwargs: dict(FAKE_ENVIRONMENT))
    monkeypatch.setattr(llm_service, "generate_round2_automation_ui_mockup", lambda **kwargs: dict(FAKE_UI_MOCKUP))

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
    """Through the real round2_automation_turn, not just the helper."""
    import json as _json
    from app.services import llm_service
    bs = chr(92)
    payload = {"response_kind": "code_edit", "response_message": "done",
               "code_after": "def t():" + bs + "n    x = 1" + bs + "n    y = 2" + bs + "n    return x + y"}
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: _json.dumps(payload))
    out = llm_service.round2_automation_turn(
        language="python", selected_design=[{"title": "t"}], environment_code="",
        current_code="", conversation_so_far=[], candidate_prompt="encode my step 1",
    )
    assert out["code_after"] == "def t():\n    x = 1\n    y = 2\n    return x + y"


# ---- Deliberately-imperfect first generation (see round2_automation_turn's
# inject_flaw and the router's clarify-then-generate gate) ----

def test_inject_flaw_adds_the_flaw_instruction_only_when_requested(monkeypatch):
    """Pure prompt-shaping check, no HTTP - inject_flaw is the only thing
    that should change what reaches the model."""
    from app.services import llm_service as _llm
    captured = {}

    def _capture(prompt, max_tokens=4096):
        captured["prompt"] = prompt
        return json.dumps({"response_kind": "code_edit", "response_message": "ok", "code_after": "x = 1"})

    monkeypatch.setattr(_llm, "_call_claude", _capture)

    _llm.round2_automation_turn(
        language="python", selected_design=[{"title": "t"}], environment_code="",
        current_code="", conversation_so_far=[], candidate_prompt="encode step 1", inject_flaw=True,
    )
    assert "FOR THIS RESPONSE ONLY" in captured["prompt"]
    assert "does not actually prove the candidate's stated expected result" in captured["prompt"]

    _llm.round2_automation_turn(
        language="python", selected_design=[{"title": "t"}], environment_code="",
        current_code="", conversation_so_far=[], candidate_prompt="encode step 1",
    )
    assert "FOR THIS RESPONSE ONLY" not in captured["prompt"]


def test_only_the_first_generation_for_a_tc_gets_the_flaw_instruction(client, monkeypatch):
    """End to end through the router's gate: the first /turn call for a
    fresh test case must ask the generator to inject a flaw; a follow-up
    /turn call on the now-unlocked test case must not."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0,))

    prompts = []

    def _capture(prompt, max_tokens=4096):
        prompts.append(prompt)
        if len(prompts) == 1:
            return _SUFFICIENT
        return json.dumps({"response_kind": "code_edit", "response_message": "ok", "code_after": f"x = {len(prompts)}"})

    monkeypatch.setattr(llm_service, "_call_claude", _capture)
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode step 1"}, cookies=_auth(cand_token))
    assert res.status_code == 201 and res.json()["response_kind"] == "code_edit"
    assert "FOR THIS RESPONSE ONLY" in prompts[-1]

    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "now encode step 2"}, cookies=_auth(cand_token))
    assert res.status_code == 201 and res.json()["response_kind"] == "code_edit"
    assert "FOR THIS RESPONSE ONLY" not in prompts[-1]


# ---- Scoring fidelity: a supported finding must survive to the Score ----
# End-to-end counterpart to test_round2_automation_evidence_audit.py's unit coverage:
# proves the persisted evidence actually reaches the auditor through
# score_round2_automation_submission, not just that the auditor can use it.

def _score_with_finding(client, monkeypatch, hr_token, cand_token, finding, final_score=98):
    from app.services import llm_service as _llm
    _select(client, cand_token, (0,))
    _unlock(client, monkeypatch, cand_token)
    client.post("/candidate/round/2/auto/code", json={"code": "print('x')\nassert 1 == 1\n"}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/run", json={"code": "print('x')\nassert 1 == 1\n"}, cookies=_auth(cand_token))
    monkeypatch.setattr(_llm, "score_round2_automation_conversation", lambda **k: {
        "scores": {"automation_design": 20, "test_data_and_assertions": 20, "ai_usage": 20,
                   "ai_output_review": 20, "execution_and_validation": 18},
        "final_score": final_score, "findings": [finding], "feedback_text": "ok",
    })
    res = client.post(
        "/candidate/round/2/auto/submit",
        json={"code": "print('x')\nassert 1 == 1\n"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    res = client.get(f"/hr/candidates/{row['id']}/report", cookies=_auth(hr_token))
    return next(s for s in res.json() if s["round_number"] == 2)["score"]


def test_finding_quoting_the_final_code_is_not_refunded(client, monkeypatch):
    """The 98->100 defect, end to end: this quote is real text from the
    candidate's own final code, so the deduction must stand at 98."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    score = _score_with_finding(client, monkeypatch, hr_token, cand_token, {
        "claim": "Assertion is trivially true and proves nothing.",
        "severity": "low",
        "evidence": [{"quote": "assert 1 == 1"}],
    })
    assert score["final_score"] == 98, "a supported finding's deduction must be preserved"
    assert score["misses_json"] == ["Assertion is trivially true and proves nothing."]
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
    # 98 + 3 restored, capped at 100 by _round2_automation_findings_to_misses - the
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
    cand_token = _reach_automation_round(client, hr_token, monkeypatch, r1_rows=GATE_ROWS)
    _select(client, cand_token, (0, 1))

    # Refinement attribution: only TC0 gets a refinement note.
    client.post("/candidate/round/2/auto/refine", json={"row_index": 0, "note": "tc0-only refinement"}, cookies=_auth(cand_token))

    # Clarification attribution: TC0 gets a clarify turn first, via the
    # standalone /clarify endpoint (unaffected by /turn's own gate).
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps(
        {"status": "insufficient", "question": "What exactly should prove this step worked?"}))
    client.post("/candidate/round/2/auto/clarify", json={"candidate_prompt": "submit it", "row_index": 0}, cookies=_auth(cand_token))

    # TC0 is still locked after a clarify-only turn (no code_edit yet) -
    # its own follow-up /turn call goes through the gate's own
    # sufficiency check too, same as TC1's first turn below.
    _sequential_call_claude(monkeypatch, _SUFFICIENT, json.dumps(
        {"response_kind": "code_edit", "response_message": "Encoded TC0.", "code_after": "print('tc0 ai code')"}))
    client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "submit it and confirm it worked", "row_index": 0}, cookies=_auth(cand_token))

    # A code_edit turn and a direct edit on TC1 only.
    _sequential_call_claude(monkeypatch, _SUFFICIENT, json.dumps(
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

    monkeypatch.setattr(llm_service, "score_round2_automation_conversation", _fake_scoring)

    entries = [{"row_index": 0}, {"row_index": 1}]
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
    assert [t["response_kind"] for t in tc0["turns"]] == ["clarify", "code_edit"]
    assert [t["response_kind"] for t in tc1["turns"]] == ["code_edit"]

    # Code edit attribution - TC1's own direct edit never leaks into TC0.
    assert len(tc1["code_edits"]) == 1
    assert tc0["code_edits"] == []

    # Execution isolation - each TC's own run result, not the other's.
    assert "tc0 output" in tc0["execution_result"]["stdout"]
    assert "tc0 output" not in tc1["execution_result"]["stdout"]
    assert "tc1 edited" in tc1["execution_result"]["stdout"]
    assert "tc1 edited" not in tc0["execution_result"]["stdout"]

    # No candidate-written validation field anymore.
    assert "validation" not in tc0
    assert "validation" not in tc1


def test_quote_evidence_tagged_with_wrong_test_case_is_rejected_end_to_end(client, monkeypatch):
    """A finding quotes TC1's own code but tags the citation as TC0's
    evidence (evidence.test_case) - must be discarded and refunded, not
    silently matched because the quote is real text somewhere in the
    submission. See round2_automation_evidence_audit._check_evidence's "wrong_test_case"
    branch."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token, (0, 1))
    _unlock(client, monkeypatch, cand_token, row_index=0)
    _unlock(client, monkeypatch, cand_token, row_index=1)
    client.post("/candidate/round/2/auto/run", json={"code": "print('tc0')", "row_index": 0}, cookies=_auth(cand_token))
    client.post("/candidate/round/2/auto/run", json={"code": "print('tc1 uniquely worded')", "row_index": 1}, cookies=_auth(cand_token))

    monkeypatch.setattr(llm_service, "score_round2_automation_conversation", lambda **k: {
        "scores": {"automation_design": 18, "test_data_and_assertions": 18, "ai_usage": 18,
                   "ai_output_review": 18, "execution_and_validation": 18},
        "final_score": 90,
        "findings": [{
            "claim": "Test case 0: code does not match what was asked.",
            "severity": "low",
            "evidence": [{"quote": "print('tc1 uniquely worded')",
                          "test_case": "Test case 0: Reject zero amount"}],
        }],
        "feedback_text": "ok",
    })
    entries = [{"row_index": 0}, {"row_index": 1}]
    res = client.post("/candidate/round/2/auto/submit", json={"entries": entries}, cookies=_auth(cand_token))
    assert res.status_code == 201

    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_r2 = next(s for s in res.json() if s["round_number"] == 2)

    assert report_r2["score"]["misses_json"] == []
    assert report_r2["score"]["evidence_audit"]["not_established"] == 1
    assert report_r2["score"]["final_score"] == 93  # 90 + one low-severity refund (3)
