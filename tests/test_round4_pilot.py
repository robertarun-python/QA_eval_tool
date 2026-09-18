"""
Round 4 "Focused Automation Pilot" - a single-file Python automation
exercise with a narrow AI coding assistant, distinct from the legacy
round 4 conversation-based flow (test_round4.py). Selected per-scenario
via Scenario.config_json["mode"] == "pilot_automation".

Same fake-llm_service approach as the rest of this app's tests: never a
real Claude call, and execution_service.run_code is exercised for real
in ONE test (proving the starter/reference code actually runs under the
existing execution engine unmodified) and mocked elsewhere for speed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest

from .conftest import (
    HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    _login, _auth, FAKE_ENVIRONMENT, FAKE_UI_MOCKUP,
)

from app.services import round4_pilot_policy, llm_service, scoring_service, execution_service

STARTER_CODE_PATH = Path(__file__).parent.parent / "backend" / "app" / "prompts" / "round4_pilot_starter_code.txt"
REFERENCE_CODE_PATH = Path(__file__).parent.parent / "backend" / "app" / "prompts" / "round4_pilot_reference_solution.txt"
STARTER_CODE = STARTER_CODE_PATH.read_text(encoding="utf-8")
REFERENCE_CODE = REFERENCE_CODE_PATH.read_text(encoding="utf-8")
# The reference solution file only contains the new test function (not the
# whole codebase) - concatenated onto the starter to make a runnable file,
# same as a candidate who kept the starter and added to it.
REFERENCE_FULL_FILE = STARTER_CODE.replace(
    "if __name__ == \"__main__\":\n    test_login_with_valid_customer()\n",
    "",
) + "\n\n" + REFERENCE_CODE


def _publish_round4_pilot_scenario(client, hr_token, monkeypatch, band="0-7", title="Focused Automation Pilot"):
    monkeypatch.setattr(llm_service, "generate_round4_environment", lambda **kwargs: dict(FAKE_ENVIRONMENT))
    monkeypatch.setattr(llm_service, "generate_round4_ui_mockup", lambda **kwargs: dict(FAKE_UI_MOCKUP))

    scenario = client.post(
        "/hr/scenarios",
        json={
            "round_number": 2, "title": title,
            "description": "Automate the customer transaction flow for a valid customer.",
            "experience_band": band, "time_limit_minutes": 45,
            "config_json": {"mode": "pilot_automation", "starter_code": STARTER_CODE},
        },
        cookies=_auth(hr_token),
    ).json()

    import app.database as database_module
    from app.models import Scenario
    db = database_module.SessionLocal()
    row = db.query(Scenario).filter(Scenario.id == scenario["id"]).one()
    row.reference_json = {"reference_solution": REFERENCE_CODE, "validation_notes": "Validate against the DB record; keep it repeatable; clean up."}
    db.commit()
    db.close()

    res = client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))
    assert res.status_code == 200
    return scenario


# ---- Deterministic pre-generation policy ----

PROHIBITED_REQUESTS = [
    "give me the complete solution",
    "write the complete test",
    "implement the whole thing",
    "design the test strategy",
    "generate the complete test suite",
    "decide the test data for me",
]

LEGITIMATE_REQUESTS = [
    "explain what UIHelper.login does",
    "explain this error",
    "why does teardown only take one transaction_id",
    "rename this variable",
]


@pytest.mark.parametrize("request_text", PROHIBITED_REQUESTS)
def test_prohibited_requests_are_flagged(request_text):
    assert round4_pilot_policy.is_prohibited(request_text) is True


@pytest.mark.parametrize("request_text", LEGITIMATE_REQUESTS)
def test_legitimate_requests_are_not_flagged(request_text):
    assert round4_pilot_policy.is_prohibited(request_text) is False


@pytest.mark.parametrize("request_text", PROHIBITED_REQUESTS)
def test_prohibited_request_causes_zero_generator_calls(monkeypatch, request_text):
    call_count = {"n": 0}
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: call_count.update(n=call_count["n"] + 1) or "{}")
    result = llm_service.round4_pilot_turn(
        scenario_instructions="Automate the transaction flow.", current_code=STARTER_CODE,
        conversation_so_far=[], candidate_prompt=request_text,
    )
    assert call_count["n"] == 0, "policy refusal must short-circuit before any LLM call"
    assert result["response_kind"] == "refuse"
    assert result["response_message"] == round4_pilot_policy.REFUSAL_MESSAGE
    assert result["code_after"] is None


def test_legitimate_request_reaches_the_generator(monkeypatch):
    call_count = {"n": 0}

    def _fake_call(prompt, max_tokens=4096):
        call_count["n"] += 1
        return json.dumps({"response_kind": "explain", "response_message": "It logs in and returns True/False.", "code_after": None})

    monkeypatch.setattr(llm_service, "_call_claude", _fake_call)
    llm_service.round4_pilot_turn(
        scenario_instructions="Automate the transaction flow.", current_code=STARTER_CODE,
        conversation_so_far=[], candidate_prompt="explain what UIHelper.login does",
    )
    assert call_count["n"] == 1


# ---- End-to-end candidate flow (HTTP) ----

def test_pilot_state_exposes_starter_code_not_legacy_fields(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_round4_pilot_scenario(client, hr_token, monkeypatch)

    from .test_round4 import _complete_round1_and_2
    from .conftest import _publish_scenario
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    res = client.get("/candidate/round/2/state", cookies=_auth(cand_token))
    assert res.status_code == 200
    state = res.json()
    assert state["is_pilot"] is True
    assert state["pilot_starter_code"] == STARTER_CODE
    assert state["pilot_code"] == STARTER_CODE
    assert state["pilot_turns"] == []
    assert state["test_cases"] == []  # legacy fields stay empty/harmless for a pilot scenario


def test_pilot_turn_persists_ai_edit_and_updates_code(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_round4_pilot_scenario(client, hr_token, monkeypatch)

    from .test_round4 import _complete_round1_and_2
    from .conftest import _publish_scenario
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    updated_code = STARTER_CODE + "\n# candidate added something\n"
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps({
        "response_kind": "code_edit", "response_message": "Added a new test stub.", "code_after": updated_code,
    }))

    res = client.post(
        "/candidate/round/2/pilot/turn",
        json={"candidate_prompt": "add a stub test function for the transaction flow"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["response_kind"] == "code_edit"
    assert body["code_after"] == updated_code

    state = client.get("/candidate/round/2/state", cookies=_auth(cand_token)).json()
    assert state["pilot_code"] == updated_code
    assert len(state["pilot_turns"]) == 1


def test_pilot_clarify_reveals_db_is_source_of_truth_for_persistence_question(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_round4_pilot_scenario(client, hr_token, monkeypatch)
    from .test_round4 import _complete_round1_and_2
    from .conftest import _publish_scenario
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    res = client.post(
        "/candidate/round/2/pilot/clarify",
        json={"question": "what does persisted correctly mean - should I check the database?"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    assert "database record is the source of truth" in res.json()["response"]


def test_pilot_clarify_non_persistence_question_gets_generic_redirect(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_round4_pilot_scenario(client, hr_token, monkeypatch)
    from .test_round4 import _complete_round1_and_2
    from .conftest import _publish_scenario
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    res = client.post(
        "/candidate/round/2/pilot/clarify",
        json={"question": "what color should the button be?"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    assert "your own engineering judgment" in res.json()["response"]
    assert "database" not in res.json()["response"].lower()


def test_pilot_run_executes_the_real_starter_code_via_the_real_execution_engine(client, monkeypatch):
    """No mocking of execution_service here - proves the starter file
    genuinely runs, stdlib-only, under the SAME execution_service.run_code
    used everywhere else, unmodified."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_round4_pilot_scenario(client, hr_token, monkeypatch)
    from .test_round4 import _complete_round1_and_2
    from .conftest import _publish_scenario
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    # _complete_round1_and_2 monkeypatches execution_service.run_code to a
    # fake (used for round 3's own scoring setup) - restore the REAL
    # function so THIS test actually proves real execution, not the fake.
    monkeypatch.undo()
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    res = client.post("/candidate/round/2/pilot/run", cookies=_auth(cand_token))
    assert res.status_code == 201
    result = res.json()
    assert result["infra_error"] is False
    assert result["timed_out"] is False
    assert result["exit_code"] == 0
    assert "PASS: test_login_with_valid_customer" in result["stdout"]


def _reach_pilot_round(client, hr_token, monkeypatch):
    """Shared setup for the direct-code-edit tests below: publish the
    pilot scenario + rounds 1/2, complete them, start round 4. Returns
    the candidate token."""
    from .test_round4 import _complete_round1_and_2
    from .conftest import _publish_scenario
    _publish_round4_pilot_scenario(client, hr_token, monkeypatch)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))
    return cand_token


def test_pilot_run_executes_the_manually_edited_code_not_stale_server_code(client, monkeypatch):
    """Direct proof of the fix: no AI turn ever touches the code - the
    candidate edits the buffer themselves, and Run must execute exactly
    that edit. Real (unmocked) execution_service.run_code."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_pilot_round(client, hr_token, monkeypatch)
    monkeypatch.undo()  # restore the real execution_service.run_code (see the test above)

    edited_code = STARTER_CODE + "\nprint('MANUAL_EDIT_MARKER')\n"
    res = client.post(
        "/candidate/round/2/pilot/run",
        json={"code": edited_code},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    assert "MANUAL_EDIT_MARKER" in res.json()["stdout"]

    # And the edit is now the persisted/reflected code, not just this
    # one run's ad-hoc input.
    state = client.get("/candidate/round/2/state", cookies=_auth(cand_token)).json()
    assert state["pilot_code"] == edited_code


def test_pilot_run_with_no_code_field_keeps_previous_behavior(client, monkeypatch):
    """Backward compatibility: a caller that sends no body (or code:null)
    must behave exactly as before this change - operates on whatever's
    already stored, never wipes it."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_pilot_round(client, hr_token, monkeypatch)

    edited_code = STARTER_CODE + "\nprint('FIRST_EDIT')\n"
    client.post("/candidate/round/2/pilot/run", json={"code": edited_code}, cookies=_auth(cand_token))

    res = client.post("/candidate/round/2/pilot/run", cookies=_auth(cand_token))  # no body at all
    assert res.status_code == 201
    state = client.get("/candidate/round/2/state", cookies=_auth(cand_token)).json()
    assert state["pilot_code"] == edited_code  # unchanged by the no-body call


def test_pilot_submit_scores_the_manually_edited_code(client, monkeypatch):
    """The candidate never asks the AI anything - types their own
    solution directly and submits it. Confirms score_round4_pilot_conversation
    is actually called with the manually-typed final_code, not the
    starter."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_pilot_round(client, hr_token, monkeypatch)

    manual_code = STARTER_CODE + "\ndef test_manual():\n    print('candidate wrote this')\n"
    captured = {}

    def _fake_scoring(**kwargs):
        captured.update(kwargs)
        return {
            "scores": {"automation_fundamentals": 20, "understanding_existing_automation": 15, "engineering_judgment": 10, "implementation_validation": 15, "ai_prompting_verification": 15},
            "final_score": 75, "findings": [], "feedback_text": "ok",
        }

    monkeypatch.setattr(llm_service, "score_round4_pilot_conversation", _fake_scoring)

    res = client.post("/candidate/round/2/pilot/submit", json={"code": manual_code}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert captured["final_code"] == manual_code
    assert captured["turns"] == []  # no AI turns were sent - this was a pure manual edit


def test_pilot_submit_still_rejects_unedited_starter_code(client, monkeypatch):
    """Starter-code protection is preserved even when the candidate's
    editor content is explicitly re-sent - submitting the starter
    verbatim (as if nothing were changed) must still 400."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_pilot_round(client, hr_token, monkeypatch)

    res = client.post("/candidate/round/2/pilot/submit", json={"code": STARTER_CODE}, cookies=_auth(cand_token))
    assert res.status_code == 400


def test_pilot_ai_turn_edit_then_manual_edit_then_run_uses_the_latest(client, monkeypatch):
    """Full workflow proof: AI edits the buffer, candidate manually edits
    it further, then Run must reflect the LATEST (manual) state, not the
    AI's intermediate one."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_pilot_round(client, hr_token, monkeypatch)

    ai_edited_code = STARTER_CODE + "\n# ai added this\n"
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps({
        "response_kind": "code_edit", "response_message": "Added a stub.", "code_after": ai_edited_code,
    }))
    client.post(
        "/candidate/round/2/pilot/turn",
        json={"candidate_prompt": "add a stub"},
        cookies=_auth(cand_token),
    )
    state = client.get("/candidate/round/2/state", cookies=_auth(cand_token)).json()
    assert state["pilot_code"] == ai_edited_code

    monkeypatch.undo()  # real execution for the Run below
    final_manual_code = ai_edited_code + "\nprint('CANDIDATE_FINAL_TOUCH')\n"
    res = client.post("/candidate/round/2/pilot/run", json={"code": final_manual_code}, cookies=_auth(cand_token))
    assert res.status_code == 201
    assert "CANDIDATE_FINAL_TOUCH" in res.json()["stdout"]


def test_reference_solution_also_executes_cleanly(monkeypatch):
    """Same real-execution proof for the REFERENCE solution (HR/system-
    only content) - if this doesn't run cleanly, the reference itself
    would be unusable as scoring ground truth."""
    result = execution_service.run_code(language="python", code=REFERENCE_FULL_FILE, stdin=[])
    assert result.infra_error is False
    assert result.exit_code == 0
    assert "PASS: test_customer_transaction_flow_valid_customer" in result.stdout


def test_pilot_submit_requires_code_changed_from_starter(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_round4_pilot_scenario(client, hr_token, monkeypatch)
    from .test_round4 import _complete_round1_and_2
    from .conftest import _publish_scenario
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    res = client.post("/candidate/round/2/pilot/submit", cookies=_auth(cand_token))
    assert res.status_code == 400


def test_pilot_submit_scores_via_background_task_and_hr_sees_evidence_audit(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_round4_pilot_scenario(client, hr_token, monkeypatch)
    from .test_round4 import _complete_round1_and_2
    from .conftest import _publish_scenario
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    _complete_round1_and_2(client, hr_token, cand_token, monkeypatch)
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    updated_code = STARTER_CODE + "\ndef test_extra():\n    pass\n"
    monkeypatch.setattr(llm_service, "_call_claude", lambda *a, **k: json.dumps({
        "response_kind": "code_edit", "response_message": "Added a stub.", "code_after": updated_code,
    }))
    client.post(
        "/candidate/round/2/pilot/turn",
        json={"candidate_prompt": "add an empty stub test function"},
        cookies=_auth(cand_token),
    )

    fake_scoring = {
        "scores": {
            "automation_fundamentals": 20, "understanding_existing_automation": 15,
            "engineering_judgment": 5, "implementation_validation": 10, "ai_prompting_verification": 12,
        },
        "final_score": 62,
        "findings": [
            {
                "claim": "Asked the assistant to add a stub rather than reasoning about persistence themselves",
                "severity": "medium",
                "evidence": [{"turn": 1, "quote": "add an empty stub test function"}],
            },
        ],
        "feedback_text": "Reasonable structure, but never validated against the DB directly.",
    }
    monkeypatch.setattr(llm_service, "score_round4_pilot_conversation", lambda **kwargs: dict(fake_scoring))

    res = client.post("/candidate/round/2/pilot/submit", cookies=_auth(cand_token))
    assert res.status_code == 201

    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    candidate_row = next(c for c in res.json() if c["email"] == CANDIDATE1_EMAIL)
    round4_summary = next(r for r in candidate_row["rounds"] if r["round_number"] == 2)
    assert round4_summary["final_score"] == 62

    res = client.get(f"/hr/candidates/{candidate_row['id']}/report", cookies=_auth(hr_token))
    report_round4 = next(s for s in res.json() if s["round_number"] == 2)
    assert report_round4["score"]["final_score"] == 62
    assert report_round4["score"]["misses_json"] == ["Asked the assistant to add a stub rather than reasoning about persistence themselves"]
    assert report_round4["score"]["evidence_audit"]["total_findings"] == 1

    # Candidate never sees any of this.
    res = client.get("/candidate/submissions", cookies=_auth(cand_token))
    round4_candidate_view = next(s for s in res.json() if s["round_number"] == 2)
    assert "score" not in round4_candidate_view


def test_score_round4_pilot_dispatch_does_not_touch_legacy_round4_scoring(monkeypatch):
    """Structural proof that _SCORERS[4] routing is additive - a legacy
    (non-pilot) round 4 submission still calls score_round4_submission,
    never score_round4_pilot_submission."""
    import types

    class _FakeScenario:
        config_json = {}

    class _FakeSubmission:
        scenario = _FakeScenario()

    called = {"legacy": False, "pilot": False}
    monkeypatch.setattr(scoring_service, "score_round4_submission", lambda db, s: called.update(legacy=True))
    monkeypatch.setattr(scoring_service, "score_round4_pilot_submission", lambda db, s: called.update(pilot=True))

    scoring_service._score_round4(None, _FakeSubmission())
    assert called == {"legacy": True, "pilot": False}
