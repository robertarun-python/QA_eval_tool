"""
The Doctor Appointment practice environment (seed_round2_appointments.py):
what Round 1 candidates design tests for can be automated in Round 2, in
every language, and the paired scenario's facts stay fixed.
"""
import sys
from pathlib import Path

import pytest

from app.services import execution_service

from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login, _publish_scenario

PROMPTS = Path(__file__).resolve().parent.parent / "backend" / "app" / "prompts"
ENV = {lang: (PROMPTS / f"round2_automation_helpers_appointments_{lang}.txt").read_text() for lang in ("python", "javascript", "java")}

PY_TEST = '''
def test_booking():
    setup()
    assert UI.login("qa.patient.demo@testportal.io", "Px!7mK@2024Test")
    assert "Dr. Sarah Johnson" in UI.search_doctors(specialty="Cardiology")
    assert UI.open_doctor("Dr. Sarah Johnson")["consultation_fee"] == "$150"
    assert "09:00" not in UI.available_slots("2025-01-16")
    assert UI.book("2025-01-15", "10:30")
    appointment_id = UI.confirmation()["id"]
    assert Database.find_appointment(appointment_id)["status"] == "confirmed"
    assert not UI.book("2025-01-15", "10:30") and UI.visible_message() == "This slot is no longer available"
    teardown(appointment_id)
    assert not Database.slot_is_booked("D-101", "2025-01-15", "10:30")
    print("PASS")

if __name__ == "__main__":
    test_booking()
'''
JS_TEST = '''
function check(c, w) { if (!c) throw new Error(w); }
setup();
check(UI.login("qa.patient.demo@testportal.io", "Px!7mK@2024Test"), "login");
check(UI.searchDoctors("Cardiology").includes("Dr. Sarah Johnson"), "search");
check(UI.openDoctor("Dr. Sarah Johnson").consultationFee === "$150", "profile");
check(!UI.availableSlots("2025-01-16").includes("09:00"), "taken slot");
check(UI.book("2025-01-15", "10:30"), "book");
const id = UI.confirmation().id;
check(Database.findAppointment(id).status === "confirmed", "stored");
check(!UI.book("2025-01-15", "10:30") && UI.visibleMessage() === "This slot is no longer available", "double booking");
teardown(id);
check(!Database.slotIsBooked("D-101", "2025-01-15", "10:30"), "freed");
console.log("PASS");
'''
JAVA_TEST = '''
    static void check(boolean c, String w) { if (!c) throw new RuntimeException(w); }
    public static void main(String[] args) {
        setup();
        check(UI.login("qa.patient.demo@testportal.io", "Px!7mK@2024Test"), "login");
        check(UI.searchDoctors("Cardiology", null).contains("Dr. Sarah Johnson"), "search");
        check(UI.openDoctor("Dr. Sarah Johnson").consultationFee.equals("$150"), "profile");
        check(!UI.availableSlots("2025-01-16").contains("09:00"), "taken slot");
        check(UI.book("2025-01-15", "10:30"), "book");
        String id = UI.confirmation().id;
        check(Database.findAppointment(id).status.equals("confirmed"), "stored");
        check(!UI.book("2025-01-15", "10:30") && UI.visibleMessage().equals("This slot is no longer available"), "double booking");
        teardown(id);
        check(!Database.slotIsBooked("D-101", "2025-01-15", "10:30"), "freed");
        System.out.println("PASS");
    }
}
'''


def _program(language):
    env = ENV[language]
    if language == "python":
        return env.replace('if __name__ == "__main__":\n    pass\n', "") + PY_TEST
    if language == "javascript":
        return env + JS_TEST
    return env[: env.index("    public static void main(String[] args) {")] + JAVA_TEST


@pytest.mark.skipif(sys.platform != "darwin", reason="runs code in the macOS sandbox")
@pytest.mark.parametrize("language", ["python", "javascript", "java"])
def test_a_round1_booking_design_can_be_automated(language):
    if not execution_service.toolchain_available(language):
        pytest.skip(f"{language} isn't installed here")
    result = execution_service.run_code(language=language, code=_program(language), stdin=[])
    assert result.stdout.strip() == "PASS", (result.stdout, result.stderr)


def test_every_language_keeps_the_marker_the_page_splits_on():
    for env in ENV.values():
        assert "TODO: write your automated test(s) below" in env


def _seed(make_live):
    from app import seed_round2_appointments
    return seed_round2_appointments.seed(make_live=make_live)


def test_seed_creates_the_paired_scenario_and_can_make_it_live(client, monkeypatch):
    import app.database as database_module
    from app.models import Scenario
    from .conftest import _publish_round4_scenario
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    old = _publish_round4_scenario(client, hr, monkeypatch)  # the current live Round 2 scenario
    created = _seed(make_live=True)
    assert created.is_live and created.config_json["paired_round1_title"] == "Doctor Appointment System"
    assert _seed(make_live=True) is None  # idempotent
    db = database_module.SessionLocal()
    assert db.get(Scenario, old["id"]).is_live is False
    assert db.get(Scenario, created.id).environment_json["fields"]["Test account email"] == "qa.patient.demo@testportal.io"
    db.close()


def test_the_paired_scenario_is_never_regenerated(client, monkeypatch):
    from app.services import llm_service
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    created = _seed(make_live=True)
    calls = []
    monkeypatch.setattr(llm_service, "generate_round2_automation_ui_mockup", lambda **k: calls.append(1) or {"screens": []})
    monkeypatch.setattr(llm_service, "generate_round2_automation_environment", lambda **k: calls.append(1) or {"fields": {}})
    # A Round 1 scenario going live would normally regenerate the live Round 2 reference.
    _publish_scenario(client, hr, monkeypatch, round_number=1, title="Something else entirely")
    assert calls == []
    res = client.post(f"/hr/scenarios/{created.id}/regenerate-reference", cookies=_auth(hr))
    assert res.status_code == 400 and "fixed practice environment" in res.json()["detail"]
