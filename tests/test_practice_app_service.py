"""
HR's Round 2 practice-app workflow (services/practice_app/service.py and the
/hr/scenarios/{id}/practice-app endpoints): build -> report -> approve ->
live with its Round 1 scenario. The factory itself is replaced by a fake
here - no AI calls - that "builds" the hand-written Doctor Appointment app.
"""
import json
from pathlib import Path

import pytest

from app import database as database_module
from app.models import Scenario
from app.services import llm_service
from app.services.practice_app import checker, generator, service

from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login, _publish_scenario

APP = {lang: llm_service._load_prompt(f"round2_automation_helpers_appointments_{lang}.txt") for lang in checker.LANGUAGES}
CHECKLISTS = json.loads((Path(__file__).parent / "fixtures" / "practice_app" / "doctor_appointments.json").read_text())
PLAN = {
    "app_name": "Doctor Appointment System",
    "test_accounts": [{"login": "qa.patient.demo@testportal.io", "password": "Px!7mK@2024Test"}],
    "helpers": [{"layer": "UI", "name": "login", "params": ["email", "password"], "returns": "true/false", "behaviour": "logs in"}],
    "reference_sheet": {"fields": {"Test account email": "qa.patient.demo@testportal.io"}, "notes": "Only these doctors exist."},
    "screens": [
        {"name": "Login", "elements": [{"type": "label", "text": "Email Address"}, {"type": "input", "text": ""},
                                       {"type": "button", "text": "Log In"}, {"type": "sparkles", "text": "dropped"}]},
        {"name": "", "elements": [{"type": "text", "text": "a nameless screen is dropped"}]},
    ],
}


@pytest.fixture
def builds_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "BUILDS_DIR", tmp_path)
    return tmp_path


def _fake_factory(monkeypatch, ok=True):
    calls = []

    def generate(title, description, cases, known_facts=None, progress=None):
        calls.append({"title": title, "cases": cases, "known_facts": known_facts})
        result = generator.PracticeAppResult(ok=ok, plan=PLAN, checklists=CHECKLISTS, env_code_by_language=dict(APP),
                                             ai_calls=5, reference_titles=[c["title"] for c in CHECKLISTS])
        result.report = checker.inspect({"python": APP["python"]}, result.checklists)  # the real inspector
        if not ok:
            result.checklists = CHECKLISTS[1:]
        return result

    monkeypatch.setattr(generator, "generate", generate)
    return calls


def _hr(client):
    return _login(client, HR_EMAIL, HR_PASSWORD)


def _r1(client, token, monkeypatch, title="Login form"):
    return _publish_scenario(client, token, monkeypatch, round_number=1, title=title)


def _db_scenario(scenario_id):
    db = database_module.SessionLocal()
    try:
        return db.get(Scenario, scenario_id)
    finally:
        db.close()


def test_status_starts_empty_and_lists_the_build_steps(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    data = client.get(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token)).json()
    assert data["status"] == "none" and data["cannot_start"] is None
    assert data["steps"] == generator.STEPS
    assert "$" not in json.dumps(data)  # HR isn't shown the cost


def test_progress_is_saved_step_by_step_while_building(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    seen = []

    def generate(title, description, cases, known_facts=None, progress=None):
        for step, detail in [(0, ""), (3, "27 of 28 pass - fixing (round 1 of 2)")]:
            progress(step, detail)
            db = database_module.SessionLocal()
            try:
                seen.append(dict(db.get(Scenario, r1["id"]).config_json["practice_app"]))
            finally:
                db.close()
        return generator.PracticeAppResult(ok=False, error="stopped for the test")

    monkeypatch.setattr(generator, "generate", generate)
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    assert [(d["status"], d["step"], d["step_detail"]) for d in seen] == [
        ("building", 0, ""), ("building", 3, "27 of 28 pass - fixing (round 1 of 2)")]
    final = client.get(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token)).json()
    assert final["status"] == "failed" and final["error"] == "stopped for the test"


def test_build_runs_the_factory_and_reports_every_test_case(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    calls = _fake_factory(monkeypatch)
    res = client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    assert res.status_code == 202 and res.json()["status"] == "building"
    data = client.get(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token)).json()
    assert data["status"] == "ready"
    assert calls and data["total"] == len(CHECKLISTS) and data["working"] == len(CHECKLISTS) and data["ai_calls"] == 5
    assert (builds_dir / f"scenario_{r1['id']}" / "latest.json").exists()
    # The heavy build stays out of the scenario list HR loads on every visit.
    listed = next(s for s in client.get("/hr/scenarios", cookies=_auth(token)).json() if s["id"] == r1["id"])
    assert "env_code_by_language" not in json.dumps(listed["config_json"])


def test_a_build_that_is_not_ready_cannot_be_approved(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    _fake_factory(monkeypatch, ok=False)
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    assert client.get(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token)).json()["status"] == "not_ready"
    res = client.post(f"/hr/scenarios/{r1['id']}/practice-app/approve", cookies=_auth(token))
    assert res.status_code == 400 and "passed every check" in res.json()["detail"]


def test_approving_creates_the_paired_round2_scenario_and_puts_it_live(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)  # the first Round 1 scenario is live
    _fake_factory(monkeypatch)
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    res = client.post(f"/hr/scenarios/{r1['id']}/practice-app/approve", cookies=_auth(token))
    assert res.status_code == 200, res.text
    round2 = _db_scenario(res.json()["round2_scenario_id"])
    assert round2.round_number == 2 and round2.is_live
    assert round2.config_json["mode"] == "ai_test_automation"
    assert round2.config_json["paired_round1_scenario_id"] == r1["id"]
    assert round2.config_json["environment_code_by_language"] == APP
    assert "qa.patient.demo@testportal.io" in round2.reference_json["ground_truth"]
    assert round2.environment_json["fields"] == {"Test account email": "qa.patient.demo@testportal.io"}
    assert round2.ui_mockup_json == {"screens": [{"name": "Login", "elements": [
        {"type": "label", "text": "Email Address"}, {"type": "input", "text": ""}, {"type": "button", "text": "Log In"}]}]}


def test_a_design_without_usable_screens_shows_none():
    assert service.screens_for({"screens": [{"name": "X", "elements": [{"type": "video", "text": "?"}]}]}) is None
    assert service.screens_for({}) is None


def test_round2_goes_live_when_its_round1_does(client, monkeypatch, builds_dir):
    token = _hr(client)
    live_r1 = _r1(client, token, monkeypatch, title="Live one")
    other_r1 = _r1(client, token, monkeypatch, title="Hotel Booking")  # published, not live
    assert not _db_scenario(other_r1["id"]).is_live
    _fake_factory(monkeypatch)
    client.post(f"/hr/scenarios/{other_r1['id']}/practice-app", cookies=_auth(token))
    round2_id = client.post(f"/hr/scenarios/{other_r1['id']}/practice-app/approve", cookies=_auth(token)).json()["round2_scenario_id"]
    assert not _db_scenario(round2_id).is_live  # its Round 1 isn't live yet
    client.post(f"/hr/scenarios/{other_r1['id']}/move-to-screening", cookies=_auth(token))
    assert _db_scenario(round2_id).is_live
    assert not _db_scenario(live_r1["id"]).is_live


def test_a_rebuild_reuses_the_facts_candidates_already_see(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    calls = _fake_factory(monkeypatch)
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    client.post(f"/hr/scenarios/{r1['id']}/practice-app/approve", cookies=_auth(token))
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    assert calls[1]["known_facts"]["fields"]["Test account email"] == "qa.patient.demo@testportal.io"


@pytest.mark.parametrize("why", ["round2", "fake_mode"])
def test_builds_are_refused_with_a_clear_reason(client, monkeypatch, builds_dir, why):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    target = r1["id"]
    if why == "round2":
        target = _publish_scenario(client, token, monkeypatch, round_number=3, title="Coding")["id"]
    else:
        monkeypatch.setattr(service.settings, "llm_fake_mode", True)
    res = client.post(f"/hr/scenarios/{target}/practice-app", cookies=_auth(token))
    assert res.status_code == 400 and res.json()["detail"]


def test_a_second_build_cannot_start_while_one_is_running(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    monkeypatch.setattr(service, "run_build", lambda scenario_id: None)  # leave it "building"
    assert client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token)).status_code == 202
    res = client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    assert res.status_code == 400 and "already being built" in res.json()["detail"]
