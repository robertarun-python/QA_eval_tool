"""
P0 cost savings for Round 2 (owner, 2026-09-28) - no AI calls, fake models only:
1. the assistant's prompt never carries the practice app's source code (only the Reference text);
2. the same message again with nothing changed is answered from the earlier reply, with no AI call;
3. "build again" with the Round 1 test cases unchanged since the approved build reuses it - no AI call.
"""
import pytest

from app import database as database_module
from app.models import Scenario
from app.services import llm_service
from app.services.practice_app import service
from app.services.practice_engine import practice_run, render
from app.services import execution_service
from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login
from .test_practice_app_service import _leave_round1, _recorded_leave
from .test_round2_typist_endpoints import SPEC, _environment_round


# ---- 1. no practice-app source in the assistant's prompt ----

def test_the_assistants_prompt_never_carries_the_practice_apps_source(client, monkeypatch):
    cand = _environment_round(client, monkeypatch)
    prompts = []
    monkeypatch.setattr(llm_service, "_call_claude_json",
                        lambda prompt, max_tokens=None, schema=None: prompts.append(prompt) or {"reply": "Noted.", "code": None})
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "Log in as jordan", "row_index": 0}, cookies=_auth(cand))
    assert res.status_code == 201, res.text
    prompt = prompts[0]
    for language in ("python", "javascript", "java"):
        _candidate_file, engine = render.files(SPEC, language)   # the candidate's own starter file is theirs, not the app
        source_lines = {line.strip() for code in engine.values() for line in code.splitlines()
                        if len(line.strip()) > 50 and not set(line.strip()) <= set("#/-=* ")}
        leaked = [line for line in source_lines if line in prompt]
        assert not leaked, f"{language} practice-app source in the prompt: {leaked[:3]}"
    assert len(prompt) < 40_000  # the Reference text and the conversation, not an application


# ---- 2. the same message again, nothing changed: no AI call ----

@pytest.fixture
def counted(monkeypatch):
    calls = []

    def model(prompt, max_tokens=None, schema=None):
        calls.append(prompt)
        return {"reply": f"Noted ({len(calls)}). Here is the code.", "code": f"incomplete('Log in as jordan {len(calls)}')\n"}
    monkeypatch.setattr(llm_service, "_call_claude_json", model)
    return calls


def _say(client, cand, text):
    res = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": text, "row_index": 0}, cookies=_auth(cand))
    assert res.status_code == 201, res.text
    return res.json()


def test_the_same_message_again_reuses_the_reply_with_no_ai_call(client, monkeypatch, counted):
    cand = _environment_round(client, monkeypatch)
    first = _say(client, cand, "Log in as jordan")
    again = _say(client, cand, "  log in as JORDAN. ")      # same words; case, spacing, end punctuation aside
    assert len(counted) == 1, "the repeat called the AI"
    assert again["response_message"] == first["response_message"] and again["code_after"] is None
    _say(client, cand, "Log in as jordan please")          # reworded: a new message
    assert len(counted) == 2


def test_after_an_edit_a_run_or_new_test_data_the_same_message_asks_again(client, monkeypatch, counted):
    cand = _environment_round(client, monkeypatch)
    _say(client, cand, "Log in as jordan. Generate the code.")   # the first code - Save and Run need it
    auth = _auth(cand)
    assert client.post("/candidate/round/2/auto/code", json={"code": "print('mine')", "row_index": 0}, cookies=auth).status_code in (200, 201)
    _say(client, cand, "Log in as jordan. Generate the code.")
    assert len(counted) == 2, "after an edit"
    ok = execution_service.ExecutionResult(stdout="PASS", stderr="", exit_code=0, timed_out=False, infra_error=False, duration_ms=5)
    monkeypatch.setattr(practice_run, "run", lambda *a, **k: ok)
    assert client.post("/candidate/round/2/auto/run", json={"code": "print('mine')", "row_index": 0}, cookies=auth).status_code == 201
    _say(client, cand, "Log in as jordan. Generate the code.")
    assert len(counted) == 3, "after a Run"
    assert client.post("/candidate/round/2/auto/test-data", json={"row_index": 0, "test_data": "jordan / Pass@1"}, cookies=auth).status_code in (200, 201)
    _say(client, cand, "Log in as jordan. Generate the code.")
    assert len(counted) == 4, "after new test data"
    _say(client, cand, "Log in as jordan. Generate the code.")
    assert len(counted) == 4, "nothing changed since: no call"


def test_a_repeated_code_request_keeps_the_code_and_the_steps(client, monkeypatch):
    cand = _environment_round(client, monkeypatch)
    calls = []

    def model(prompt, max_tokens=None, schema=None):
        calls.append(1)
        return {"reply": "Here it is.", "steps": [{"step": "Log in as jordan", "missing": ""}],
                "code": "incomplete('Log in as jordan')\n"}
    monkeypatch.setattr(llm_service, "_call_claude_json", model)
    _say(client, cand, "Log in as jordan. Generate the code.")
    again = _say(client, cand, "Log in as jordan. Generate the code.")
    assert len(calls) == 1 and again["response_kind"] == "explain" and again["steps"] == [{"step": "Log in as jordan", "missing": ""}]
    state = client.get("/candidate/round/2/auto/state", cookies=_auth(cand)).json()
    assert state["tc_state"][0]["code"] == "incomplete('Log in as jordan')\n"  # the code is untouched


# ---- 3. build again with unchanged Round 1 test cases: nothing built, nothing paid ----

@pytest.fixture
def builds_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "BUILDS_DIR", tmp_path)
    return tmp_path


def _approved_leave(client, monkeypatch):
    recorded, titles = _recorded_leave()
    r1 = _leave_round1(client, monkeypatch, titles)
    monkeypatch.setattr(llm_service, "_send", lambda *a, **k: pytest.fail("an AI call was made"))
    service.install_recorded(r1["id"], recorded)
    db = database_module.SessionLocal()
    try:
        service.approve(db.get(Scenario, r1["id"]), db)
    finally:
        db.close()
    return r1


def test_build_again_with_unchanged_test_cases_reuses_the_approved_app(client, monkeypatch, builds_dir):
    r1 = _approved_leave(client, monkeypatch)
    monkeypatch.setattr(service, "run_build", lambda *a: pytest.fail("a build was started"))
    monkeypatch.setattr(service, "start_build", lambda *a: pytest.fail("a build was started"))
    hr = _auth(_login(client, HR_EMAIL, HR_PASSWORD))
    status = client.get(f"/hr/scenarios/{r1['id']}/practice-app", cookies=hr).json()
    assert status["unchanged"] is True and "haven't changed" in status["unchanged_message"]
    res = client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=hr)
    assert res.status_code == 200 and res.json()["unchanged"] is True
    assert "no AI call" in res.json()["message"] and res.json()["approved_round2_scenario_id"]


def test_changed_test_cases_or_a_fresh_request_build_as_before(client, monkeypatch, builds_dir):
    r1 = _approved_leave(client, monkeypatch)
    started = []
    monkeypatch.setattr(service, "run_build", lambda scenario_id: started.append(scenario_id))
    monkeypatch.setattr(service.settings, "llm_fake_mode", False)
    hr = _auth(_login(client, HR_EMAIL, HR_PASSWORD))
    res = client.post(f"/hr/scenarios/{r1['id']}/practice-app?fresh=true", cookies=hr)
    assert res.status_code == 202 and started == [r1["id"]]
    assert res.json()["approved_round2_scenario_id"]  # the approval is kept while the fresh build runs
    service._RUNNING.discard(r1["id"])
    db = database_module.SessionLocal()
    try:
        s = db.get(Scenario, r1["id"])
        s.reference_json = [*s.reference_json, {"title": "A new case", "priority": "Low", "steps": "", "expected_result": ""}]
        data = dict(s.config_json)
        data["practice_app"] = {**data["practice_app"], "status": "ready"}
        s.config_json = data
        db.commit()
    finally:
        db.close()
    assert client.get(f"/hr/scenarios/{r1['id']}/practice-app", cookies=hr).json()["unchanged"] is False
    res = client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=hr)
    assert res.status_code == 202 and started == [r1["id"], r1["id"]]


def test_an_approval_saved_before_the_fingerprint_keeps_the_old_behaviour(client, monkeypatch, builds_dir):
    r1 = _approved_leave(client, monkeypatch)
    db = database_module.SessionLocal()
    try:
        s = db.get(Scenario, r1["id"])
        data = dict(s.config_json)
        data["practice_app"] = {k: v for k, v in data["practice_app"].items() if k != "approved_reference_hash"}
        s.config_json = data
        db.commit()
        assert service.unchanged_since_approved(db.get(Scenario, r1["id"])) is False
    finally:
        db.close()


def test_the_hr_screen_offers_a_fresh_build_only_on_purpose():
    from .page_js import page_js
    js = page_js()
    assert "Build fresh anyway (paid)" in js and "?fresh=true" in js and "result.unchanged" in js
