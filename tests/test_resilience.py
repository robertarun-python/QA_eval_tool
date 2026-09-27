"""
What people see when things go wrong around them: the server restarts during
a practice-app build, or a route fails unexpectedly. (A dropped network is
tested in the browser: tests/e2e/test_resilience.py.) No AI calls.
"""
from fastapi.testclient import TestClient

from app.main import app
from app.models import Scenario
from app.services.practice_app import service


def _building(scenario_id=77):
    return Scenario(id=scenario_id, round_number=1, title="x", description="x", reference_json=[{"title": "t"}],
                    config_json={"practice_app": {"status": "building", "started_at": "2099-01-01T00:00:00", "step": 2}})


def test_a_build_cut_off_by_a_server_restart_is_reported_at_once_and_can_be_started_again(monkeypatch):
    monkeypatch.setattr(service, "_RUNNING", set())
    scenario = _building()
    data = service.summary(scenario)
    assert data["status"] == "failed" and data["error"] == service.INTERRUPTED
    monkeypatch.setattr(service.settings, "llm_fake_mode", False)
    assert service.can_start(scenario) is None


def test_a_build_still_running_in_this_server_shows_building_and_cannot_be_started_twice(monkeypatch):
    monkeypatch.setattr(service, "_RUNNING", {77})
    scenario = _building()
    assert service.summary(scenario)["status"] == "building"
    monkeypatch.setattr(service.settings, "llm_fake_mode", False)
    assert service.can_start(scenario) == "A practice app is already being built for this scenario."


def test_an_unexpected_server_error_gives_a_plain_message_not_a_stack_trace():
    @app.get("/__test_boom")
    def boom():
        raise RuntimeError("secret internal detail")
    try:
        res = TestClient(app, raise_server_exceptions=False).get("/__test_boom")
    finally:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/__test_boom"]
    assert res.status_code == 500
    assert res.json() == {"detail": "Something went wrong on our side. Please try again - if it keeps happening, tell the HR team."}
    assert "secret internal detail" not in res.text
