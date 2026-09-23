"""
Batch B of the Sep 2026 assistant guardrail fixes: the flaw the automation
assistant deliberately plants in a test case's first generated code is
recorded for scoring, kept away from the candidate, and not planted at all
when the language can't run (the candidate could only catch it by running
the test).
"""
import json
from datetime import datetime

from app import database as database_module
from app.models import Submission
from app.schemas import SubmissionOut, SubmissionReportOut
from app.services import execution_service, llm_service

from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login
from .test_round4_auto import _SUFFICIENT, _reach_automation_round, _select, _sequential_call_claude, _state

_FLAW = "Asserts the header against a constant it set itself instead of reading the page."
_CODE = "def test_login():\n    header = 'Welcome, Jordan'\n    assert header == 'Welcome, Jordan'\n"


def _generated(planted=_FLAW):
    body = {"response_kind": "code_edit", "response_message": "Automated it.", "code_after": _CODE}
    if planted is not None:
        body["planted_flaw"] = planted
    return json.dumps(body)


def _turn_kwargs(prompt="encode the steps"):
    return dict(language="python", selected_design=[{"title": "Login"}], environment_code="# env",
                current_code="", conversation_so_far=[], candidate_prompt=prompt)


# ---- llm_service.round4_auto_turn ------------------------------------------

def test_planted_flaw_is_requested_and_returned_on_the_first_generation(monkeypatch):
    captured = {}

    def _fake(prompt, max_tokens=4096):
        captured["prompt"] = prompt
        return _generated()

    monkeypatch.setattr(llm_service, "_call_claude", _fake)
    result = llm_service.round4_auto_turn(**_turn_kwargs(), inject_flaw=True)
    assert '"planted_flaw"' in captured["prompt"]
    assert result["planted_flaw"] == _FLAW


def test_an_undescribed_plant_is_still_recorded(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda p, max_tokens=4096: _generated(planted=None))
    result = llm_service.round4_auto_turn(**_turn_kwargs(), inject_flaw=True)
    assert result["planted_flaw"] == "A flaw was planted in this code but not described."


def test_no_planted_flaw_is_recorded_on_later_turns(monkeypatch):
    # Even if the model volunteers the field, a turn that wasn't asked to plant anything records nothing.
    monkeypatch.setattr(llm_service, "_call_claude", lambda p, max_tokens=4096: _generated())
    assert "planted_flaw" not in llm_service.round4_auto_turn(**_turn_kwargs(), inject_flaw=False)


# ---- Scoring ---------------------------------------------------------------

def test_scorer_is_told_what_was_planted(monkeypatch):
    captured = {}

    def _fake(prompt, max_tokens=4096):
        captured["prompt"] = prompt
        return '{"scores": {}, "final_score": 0, "findings": [], "feedback_text": ""}'

    monkeypatch.setattr(llm_service, "_call_claude", _fake)
    evidence = [{"label": "TC1", "turns": [{"turn_number": 1, "response_kind": "code_edit", "planted_flaw": _FLAW}]}]
    llm_service.score_round4_auto_conversation(language="python", tc_evidence=evidence, ground_truth="", validation_notes="")
    prompt = captured["prompt"]
    assert _FLAW in prompt
    assert "never attribute its presence in that turn's code to them" in prompt
    assert "Never mention that anything was planted" in prompt


# ---- Kept away from the candidate, visible to HR ---------------------------

def _submission_content():
    return {"selected": [{"index": 0, "turns": [
        {"turn_number": 1, "response_kind": "code_edit", "code_after": _CODE, "planted_flaw": _FLAW},
    ]}]}


def _row(content):
    return {"id": 1, "scenario_id": 1, "round_number": 2, "status": "submitted", "content": content,
            "created_at": datetime(2026, 9, 23)}


def test_candidate_facing_submission_never_includes_the_planted_flaw():
    out = SubmissionOut.model_validate(_row(_submission_content())).model_dump()
    turn = out["content"]["selected"][0]["turns"][0]
    assert "planted_flaw" not in turn
    assert turn["code_after"] == _CODE  # everything else is untouched


def test_hr_report_keeps_the_planted_flaw():
    out = SubmissionReportOut.model_validate(_row(_submission_content())).model_dump()
    assert out["content"]["selected"][0]["turns"][0]["planted_flaw"] == _FLAW


def test_first_generation_stores_the_flaw_but_never_sends_it_to_the_candidate(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token)
    _sequential_call_claude(monkeypatch, _SUFFICIENT, _generated())

    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode step 1"}, cookies=_auth(cand_token))
    assert res.status_code == 201, res.text
    assert "planted_flaw" not in res.json()
    assert _FLAW not in json.dumps(_state(client, cand_token))

    db = database_module.SessionLocal()
    try:
        submission = db.query(Submission).filter(Submission.round_number == 2).order_by(Submission.id.desc()).first()
        stored = submission.content["selected"][0]["turns"][-1]
    finally:
        db.close()
    assert stored["planted_flaw"] == _FLAW


def test_no_flaw_is_planted_when_the_language_cannot_run(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)
    _select(client, cand_token)
    monkeypatch.setattr(execution_service, "toolchain_available", lambda language: False)
    seen = {}
    real_turn = llm_service.round4_auto_turn

    def _spy(**kwargs):
        seen["inject_flaw"] = kwargs.get("inject_flaw")
        return real_turn(**kwargs)

    monkeypatch.setattr(llm_service, "round4_auto_turn", _spy)
    _sequential_call_claude(monkeypatch, _SUFFICIENT, _generated())
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "encode step 1"}, cookies=_auth(cand_token))
    assert res.status_code == 201, res.text
    assert seen["inject_flaw"] is False


def test_toolchain_check_treats_the_macos_java_placeholder_as_missing(monkeypatch):
    class _Failed:
        returncode = 1

    monkeypatch.setattr(execution_service, "_TOOLCHAIN_CACHE", {})
    monkeypatch.setattr(execution_service.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(execution_service.subprocess, "run", lambda *a, **k: _Failed())
    assert execution_service.toolchain_available("java") is False
    assert execution_service.toolchain_available("python") is True
