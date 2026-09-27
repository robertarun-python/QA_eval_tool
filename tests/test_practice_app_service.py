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
from app.services.practice_app import checker, engine_build, generator, service

from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login, _publish_round4_scenario, _publish_scenario

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


def _fake_factory(monkeypatch, ok=True, approvable=False):
    calls = []

    def generate(title, description, cases, known_facts=None, progress=None, reuse=None):
        calls.append({"title": title, "cases": cases, "known_facts": known_facts, "reuse": reuse})
        result = generator.PracticeAppResult(ok=ok, plan=PLAN, checklists=CHECKLISTS, env_code_by_language=dict(APP),
                                             ai_calls=5, reference_titles=[c["title"] for c in CHECKLISTS])
        result.report = checker.inspect({"python": APP["python"]}, result.checklists)  # the real inspector
        if not ok:
            result.checklists = CHECKLISTS[1:]
        result.approvable = ok or approvable
        return result

    monkeypatch.setattr(engine_build, "generate", generate)
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
    assert data["steps"] == engine_build.STEPS
    assert "$" not in json.dumps(data)  # HR isn't shown the cost


def test_progress_is_saved_step_by_step_while_building(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    seen = []

    def generate(title, description, cases, known_facts=None, progress=None, reuse=None):
        for step, detail in [(0, ""), (3, "27 of 28 pass - fixing (round 1 of 2)")]:
            progress(step, detail)
            db = database_module.SessionLocal()
            try:
                seen.append(dict(db.get(Scenario, r1["id"]).config_json["practice_app"]))
            finally:
                db.close()
        return generator.PracticeAppResult(ok=False, error="stopped for the test")

    monkeypatch.setattr(engine_build, "generate", generate)
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
    assert res.status_code == 400 and "at least 90%" in res.json()["detail"]


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


def test_an_engine_built_apps_engine_file_goes_to_round2_and_runs_beside_the_candidates_code(client, monkeypatch, builds_dir):
    """The engine file travels build -> approval -> Round 2 scenario, and the
    candidate's Run places it next to their code (routers/candidate)."""
    from app.routers import candidate as candidate_router
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    _fake_factory(monkeypatch)
    real = engine_build.generate
    support = {"python": {"practice_engine.py": "X = 1"}}

    def with_engine(*args, **kwargs):
        result = real(*args, **kwargs)
        result.support_by_language = support
        return result
    monkeypatch.setattr(engine_build, "generate", with_engine)
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    res = client.post(f"/hr/scenarios/{r1['id']}/practice-app/approve", cookies=_auth(token))
    round2 = _db_scenario(res.json()["round2_scenario_id"])
    assert round2.config_json["environment_support_by_language"] == support
    assert candidate_router._auto_environment_support(round2, "python") == {"practice_engine.py": "X = 1"}
    assert candidate_router._auto_environment_support(round2, "java") == {}


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


def test_round2_switch_turns_the_old_one_off_before_the_new_one_on(client, monkeypatch, builds_dir):
    # The paired Round 2 is OLDER (lower id) than the live one it replaces -
    # saved in id order in one flush, the database's one-live rule would
    # see two live Round 2s for a moment and refuse (see models.py).
    token = _hr(client)
    _r1(client, token, monkeypatch, title="Live one")
    other_r1 = _r1(client, token, monkeypatch, title="Hotel Booking")
    _fake_factory(monkeypatch)
    client.post(f"/hr/scenarios/{other_r1['id']}/practice-app", cookies=_auth(token))
    paired_id = client.post(f"/hr/scenarios/{other_r1['id']}/practice-app/approve", cookies=_auth(token)).json()["round2_scenario_id"]
    newer_live = _publish_round4_scenario(client, token, monkeypatch, title="Generic automation")
    assert newer_live["id"] > paired_id and _db_scenario(newer_live["id"]).is_live

    res = client.post(f"/hr/scenarios/{other_r1['id']}/move-to-screening", cookies=_auth(token))

    assert res.status_code == 200, res.text
    assert _db_scenario(paired_id).is_live
    assert not _db_scenario(newer_live["id"]).is_live


def test_generate_again_offers_the_last_build_for_reuse(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    calls = _fake_factory(monkeypatch)
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    assert calls[0]["reuse"] is None  # nothing built yet
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    assert calls[1]["reuse"]["python"] == APP["python"]
    assert calls[1]["reuse"]["plan"] == PLAN and calls[1]["reuse"]["checklists"]


def test_a_build_from_different_round1_test_cases_is_not_reused(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    calls = _fake_factory(monkeypatch)
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    saved = builds_dir / f"scenario_{r1['id']}" / "latest.json"
    build = json.loads(saved.read_text())
    build["reference_hash"] = "different"
    saved.write_text(json.dumps(build))
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    assert calls[1]["reuse"] is None


def test_every_build_is_kept_not_just_the_latest(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    _fake_factory(monkeypatch, ok=False)
    moments = iter(["2026-09-25T10:00:00", "2026-09-25T10:20:00"])

    class _Clock(service.datetime):
        @classmethod
        def utcnow(cls):
            return cls.fromisoformat(next(moments, "2026-09-25T10:40:00"))
    monkeypatch.setattr(service, "datetime", _Clock)
    for _ in range(2):
        client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    history = sorted(p.name for p in (builds_dir / f"scenario_{r1['id']}" / "history").iterdir())
    assert len(history) == 2, history
    saved = json.loads((builds_dir / f"scenario_{r1['id']}" / "history" / history[0]).read_text())
    assert saved["coverage"] and saved["log"] is not None and not saved["ok"]


def test_a_mostly_verified_app_can_be_approved_and_the_scorer_is_told_what_wasnt(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    _fake_factory(monkeypatch, ok=False, approvable=True)  # one test case couldn't be verified
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    status = client.get(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token)).json()
    assert status["status"] == "ready"
    missing = CHECKLISTS[0]["title"]
    assert [r["title"] for r in status["unverified"]] == [missing]

    res = client.post(f"/hr/scenarios/{r1['id']}/practice-app/approve", cookies=_auth(token))
    assert res.status_code == 200, res.text
    notes = _db_scenario(res.json()["round2_scenario_id"]).reference_json["validation_notes"]
    assert "could NOT be verified" in notes and missing in notes


def test_a_fully_verified_app_gets_the_plain_notes(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    _fake_factory(monkeypatch)
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    assert client.get(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token)).json()["unverified"] == []
    round2_id = client.post(f"/hr/scenarios/{r1['id']}/practice-app/approve", cookies=_auth(token)).json()["round2_scenario_id"]
    assert _db_scenario(round2_id).reference_json["validation_notes"] == service.VALIDATION_NOTES


def test_a_reused_old_build_keeps_its_not_supported_cases(client, monkeypatch, builds_dir):
    """C5: builds saved before "unsupported" was stored had it only in the
    summary - which start_build replaces before the reuse is looked up."""
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    calls = _fake_factory(monkeypatch)
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    saved = builds_dir / f"scenario_{r1['id']}" / "latest.json"
    build = json.loads(saved.read_text())
    build.pop("unsupported")  # an old build
    saved.write_text(json.dumps(build))
    db = database_module.SessionLocal()
    try:
        scenario = db.get(Scenario, r1["id"])
        data = service.summary(scenario)
        data["coverage"] = data["coverage"] + [{"title": "SMS reminder", "status": "not supported", "details": ["no SMS"]}]
        service._set_summary(scenario, data)
        db.commit()
    finally:
        db.close()
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    assert calls[1]["reuse"]["unsupported"] == [{"title": "SMS reminder", "reason": "no SMS"}]


def _approved_round2(client, token, monkeypatch, r1):
    _fake_factory(monkeypatch)
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    res = client.post(f"/hr/scenarios/{r1['id']}/practice-app/approve", cookies=_auth(token))
    assert res.status_code == 200, res.text
    return res.json()["round2_scenario_id"]


def _submission(scenario_id, status):
    from datetime import datetime
    from app.models import RoundStatus, Submission, User
    from .conftest import CANDIDATE1_EMAIL
    db = database_module.SessionLocal()
    try:
        user = db.query(User).filter(User.email == CANDIDATE1_EMAIL).one()
        db.add(Submission(user_id=user.id, scenario_id=scenario_id, round_number=2, status=RoundStatus(status),
                          started_at=datetime.utcnow()))
        db.commit()
    finally:
        db.close()


def test_a_practice_apps_reference_sheet_cannot_be_edited(client, monkeypatch, builds_dir):
    """D1: the sheet matches the app's code - an edit would tell candidates a login the app rejects."""
    token = _hr(client)
    round2_id = _approved_round2(client, token, monkeypatch, _r1(client, token, monkeypatch))
    res = client.patch(f"/hr/scenarios/{round2_id}/round4-environment", cookies=_auth(token),
                       json={"fields": {"Test account email": "someone.else@example.com"}, "notes": ""})
    assert res.status_code == 400 and "can't be edited" in res.json()["detail"]
    assert _db_scenario(round2_id).environment_json["fields"] == {"Test account email": "qa.patient.demo@testportal.io"}


def test_a_new_round2_takes_its_time_limit_from_its_own_band(client, monkeypatch, builds_dir):
    """D2: the template for a new paired Round 2 is the live Round 2 of the same band."""
    token = _hr(client)
    other_band = _publish_round4_scenario(client, token, monkeypatch, band="7+")
    db = database_module.SessionLocal()
    db.get(Scenario, other_band["id"]).time_limit_minutes = 99
    db.commit()
    db.close()
    round2_id = _approved_round2(client, token, monkeypatch, _r1(client, token, monkeypatch))
    assert _db_scenario(round2_id).time_limit_minutes != 99


def test_reapproving_after_candidates_took_round2_makes_a_new_version(client, monkeypatch, builds_dir):
    """D3: candidates' results stay tied to the app they actually used."""
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    first = _approved_round2(client, token, monkeypatch, r1)
    _submission(first, "submitted")
    before = _db_scenario(first).config_json
    second = _approved_round2(client, token, monkeypatch, r1)
    assert second != first
    assert _db_scenario(first).config_json == before  # untouched
    assert _db_scenario(second).title.endswith("(version 2)")
    assert _db_scenario(second).is_live and not _db_scenario(first).is_live  # the newest version is the live one


def test_reapproving_before_anyone_took_it_updates_in_place(client, monkeypatch, builds_dir):
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    assert _approved_round2(client, token, monkeypatch, r1) == _approved_round2(client, token, monkeypatch, r1)


def test_approval_is_refused_while_a_candidate_is_mid_round2(client, monkeypatch, builds_dir):
    """C8: it used to skip the switch silently while the card said "ready to approve"."""
    token = _hr(client)
    live_r2 = _publish_round4_scenario(client, token, monkeypatch)
    _submission(live_r2["id"], "in_progress")
    r1 = _r1(client, token, monkeypatch)  # live
    _fake_factory(monkeypatch)
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    res = client.post(f"/hr/scenarios/{r1['id']}/practice-app/approve", cookies=_auth(token))
    assert res.status_code == 400 and "middle of Round 2" in res.json()["detail"]
    assert _db_scenario(live_r2["id"]).is_live


def test_approving_an_engine_built_app_puts_round2_on_the_real_environment(client, monkeypatch, builds_dir):
    from pathlib import Path as _Path

    from app.services import round2_typist
    spec = json.loads((_Path(__file__).parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())
    token = _hr(client)
    r1 = _r1(client, token, monkeypatch)
    _fake_factory(monkeypatch)
    real = engine_build.generate

    def engine_plan(*args, **kwargs):
        result = real(*args, **kwargs)
        result.plan = {**result.plan, "engine_spec": spec}
        return result
    monkeypatch.setattr(engine_build, "generate", engine_plan)
    client.post(f"/hr/scenarios/{r1['id']}/practice-app", cookies=_auth(token))
    res = client.post(f"/hr/scenarios/{r1['id']}/practice-app/approve", cookies=_auth(token))
    assert res.status_code == 200, res.text
    round2 = _db_scenario(res.json()["round2_scenario_id"])
    config = round2.config_json
    assert config["practice_spec"] == spec
    assert config["environment_code_by_language"] == round2_typist.STARTERS and config["environment_support_by_language"] == {}
    panel = config["reference_panel"]
    assert panel["app_name"] == spec["app_name"] and any(a["path"] == "/api/borrow-book" for a in panel["api"])
    assert "No copies available" not in json.dumps(panel)
    assert round2.ui_mockup_json is None  # the panel shows the real pages instead of a drawn sketch


def _recorded_leave():
    entry = json.loads((Path(__file__).parent / "fixtures/practice_engine/real_outputs/builds/r2_leave.json").read_text())
    checklists = [{k: v for k, v in c.items() if k != "recorded_pass"} for c in entry["checklists"]]
    return {"plan": {"engine_spec": entry["spec"]}, "checklists": checklists, "unsupported": []}, [c["title"] for c in checklists]


def _leave_round1(client, monkeypatch, titles):
    r1 = _r1(client, _hr(client), monkeypatch, title="Employee Leave Management")
    db = database_module.SessionLocal()
    try:
        s = db.get(Scenario, r1["id"])
        s.reference_json = [{"title": t, "priority": "Medium", "steps": "", "expected_result": ""} for t in titles]
        db.commit()
    finally:
        db.close()
    return r1


def test_a_build_already_paid_for_installs_without_any_ai_call(client, monkeypatch, builds_dir):
    """Measurement builds were paid for; installing one re-checks it with
    today's engine in all three languages and must never call the AI."""
    recorded, titles = _recorded_leave()
    r1 = _leave_round1(client, monkeypatch, titles)
    monkeypatch.setattr(llm_service, "_send", lambda *a, **k: pytest.fail("the install called the AI"))
    data = service.install_recorded(r1["id"], recorded)
    assert (data["status"], data["working"], data["total"]) == ("ready", 12, 12)
    assert service.approve(_db_scenario(r1["id"]), database_module.SessionLocal()).round_number == 2


def test_a_recorded_build_covering_only_some_test_cases_is_not_ready(client, monkeypatch, builds_dir):
    """This morning's builds used 12 of an older scenario's 25-28 test cases;
    installed, they must say so rather than look complete."""
    recorded, titles = _recorded_leave()
    r1 = _leave_round1(client, monkeypatch, titles + [f"Extra case {n}" for n in range(12)])
    monkeypatch.setattr(llm_service, "_send", lambda *a, **k: pytest.fail("the install called the AI"))
    data = service.install_recorded(r1["id"], recorded)
    assert (data["status"], data["working"], data["total"]) == ("not_ready", 12, 24)


def test_a_recorded_build_that_no_longer_checks_out_is_refused_plainly(client, monkeypatch, builds_dir):
    recorded, titles = _recorded_leave()
    recorded["plan"]["engine_spec"] = {"app_name": "broken"}
    r1 = _leave_round1(client, monkeypatch, titles)
    monkeypatch.setattr(llm_service, "_send", lambda *a, **k: pytest.fail("the install called the AI"))
    data = service.install_recorded(r1["id"], recorded)
    assert data["status"] == "failed" and "no longer pass today's checks" in data["error"]
