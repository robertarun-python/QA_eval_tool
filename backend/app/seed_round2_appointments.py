"""
Creates the Round 2 (AI-assisted automation) scenario PAIRED with the
"Doctor Appointment System" Round 1 scenario: a practice environment that
implements what candidates design tests for in Round 1 (log in, search
doctors, view a profile, pick a date and slot, book, see the
confirmation), in Python, JavaScript and Java.

Why: the only automation environment before this one supported logging in
and saving money amounts, so a candidate who designed "book a Cardiology
appointment" in Round 1 had nothing to automate in Round 2 - the assistant
could only explain that, and the conversation stalled.

The reference facts and screens candidates see are written here to match
the environment exactly and are fixed (config paired_round1_title - see
routers/hr.py: never regenerated, and HR is warned if a different Round 1
scenario goes live).

    cd backend && python -m app.seed_round2_appointments             # creates a draft
    cd backend && python -m app.seed_round2_appointments --make-live  # also publishes it and makes it live

Back up first (python -m app.backup_db before_appointments_env). Idempotent:
does nothing if the paired scenario already exists.
"""
import sys
from datetime import datetime
from pathlib import Path

from .config import settings
from . import database
from .models import ExperienceBand, Scenario, ScenarioStatus, User
from .seed_round2_automation import INSTRUCTIONS

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PAIRED_ROUND1_TITLE = "Doctor Appointment System"
TITLE = "AI-Assisted Test Automation - Doctor Appointments"
_HELPER_FILES = {
    "python": "round2_automation_helpers_appointments_python.txt",
    "javascript": "round2_automation_helpers_appointments_javascript.txt",
    "java": "round2_automation_helpers_appointments_java.txt",
}

GROUND_TRUTH = (
    "The provided environment (a Doctor Appointment System) behaves as follows, and this is the truth the "
    "candidate's interpretation should be judged against:\n"
    "- Login succeeds ONLY for qa.patient.demo@testportal.io with password Px!7mK@2024Test (message "
    "'Welcome back, Alex', page 'Doctor Search'). Any other combination returns False with the message "
    "'Invalid email or password'. It does not raise.\n"
    "- Doctors: Dr. Sarah Johnson (Cardiology, 12 years, $150), Dr. Michael Chen (Cardiology, 8 years, $120), "
    "Dr. Priya Patel (Dermatology), Dr. James Wilson (Orthopedics), Dr. Emily Brown (Pediatrics). Search by "
    "specialty is case-insensitive; an unknown specialty returns no doctors and shows 'No doctors found'.\n"
    "- Dates 2025-01-15 to 2025-01-17, slots 09:00, 10:30, 14:00, 15:30. Dr. Sarah Johnson's 2025-01-16 09:00 "
    "slot is already booked by another patient and is not offered.\n"
    "- Booking a free slot while logged in stores an appointment with status 'confirmed' (id 'APT-nnnn'), "
    "removes that slot from availability, shows 'Appointment confirmed' and opens a Confirmation page with the "
    "doctor, specialty, date, time and fee. Booking a taken slot is refused with 'This slot is no longer "
    "available'; booking without logging in is refused with 'Please log in to book'. A refused booking writes "
    "NOTHING.\n"
    "- Database.find_appointment / slot_is_booked are the only real proof of what was stored; the confirmation "
    "page and the API's ok flag are the system reporting on itself. teardown(appointment_id) cancels a booking "
    "and frees its slot; each execution starts from a fresh process.\n\n"
    "Consequences worth checking the candidate's interpretation against: a test that asserts only the value it "
    "just passed in, only the ok flag, or only that a Confirmation page opened does NOT prove the booking was "
    "stored. A test that books a taken slot and then asserts an appointment exists is asserting the opposite of "
    "the real behaviour and should fail - if it passed, it isn't testing what the candidate thinks. An exit "
    "code of 0 alone proves only that the file ran."
)

VALIDATION_NOTES = (
    "A strong submission: automates the candidate's own selected design faithfully, reuses the provided helpers "
    "instead of rebuilding them, encodes exactly the data and expected results they specified, and asserts "
    "something that would genuinely fail if the requirement were broken - for a booking, reading the stored "
    "appointment or the slot's availability, not just the confirmation page. It shows real review of the "
    "assistant's output - a correction, a follow-up that catches a problem, or a direct edit - rather than "
    "wholesale acceptance. Its interpretation of the run matches the ground truth above, including noticing a "
    "test that passed for the wrong reason. Do not reward assertion COUNT, and do not penalise a valid approach "
    "that differs from any particular implementation."
)

# What candidates are shown (Round 1 reference panel and Round 2) - the same
# facts the environment implements, so their designs can be automated as written.
ENVIRONMENT_FACTS = {
    "fields": {
        "Product URL": "https://healthconnect-staging.qa.mediportal.io/patient",
        "Test account email": "qa.patient.demo@testportal.io",
        "Test account password": "Px!7mK@2024Test",
        "Doctors": ("Dr. Sarah Johnson - Cardiology, 12 years, $150; Dr. Michael Chen - Cardiology, 8 years, $120; "
                    "Dr. Priya Patel - Dermatology, 10 years, $130; Dr. James Wilson - Orthopedics, 15 years, $180; "
                    "Dr. Emily Brown - Pediatrics, 6 years, $100"),
        "Available dates": "2025-01-15, 2025-01-16, 2025-01-17",
        "Time slots": "09:00, 10:30, 14:00, 15:30 (Dr. Sarah Johnson's 2025-01-16 09:00 is already booked)",
    },
    "notes": "Only these doctors, dates and slots exist in the test environment.",
}

UI_MOCKUP = {
    "screens": [
        {"name": "Login", "elements": [
            {"type": "text", "text": "Welcome Back"}, {"type": "label", "text": "Email Address"}, {"type": "input", "text": ""},
            {"type": "label", "text": "Password"}, {"type": "input", "text": ""}, {"type": "button", "text": "Log In"},
            {"type": "text", "text": "Invalid email or password (shown on a failed login)"}]},
        {"name": "Doctor Search", "elements": [
            {"type": "text", "text": "Find a Doctor"}, {"type": "text", "text": "Welcome back, Alex"},
            {"type": "label", "text": "Search by Specialty"}, {"type": "input", "text": ""},
            {"type": "label", "text": "Search by Doctor Name"}, {"type": "input", "text": ""}, {"type": "button", "text": "Search"},
            {"type": "text", "text": "No doctors found (shown when nothing matches)"}]},
        {"name": "Doctor Profile and Booking", "elements": [
            {"type": "text", "text": "Dr. Sarah Johnson"}, {"type": "label", "text": "Specialty"}, {"type": "text", "text": "Cardiology"},
            {"type": "label", "text": "Experience"}, {"type": "text", "text": "12 years"},
            {"type": "label", "text": "Consultation Fee"}, {"type": "text", "text": "$150"},
            {"type": "label", "text": "Select Date"}, {"type": "text", "text": "2025-01-15 | 2025-01-16 | 2025-01-17"},
            {"type": "label", "text": "Available Time Slots"}, {"type": "text", "text": "09:00 | 10:30 | 14:00 | 15:30"},
            {"type": "button", "text": "Book Appointment"}]},
        {"name": "Confirmation", "elements": [
            {"type": "text", "text": "Appointment confirmed"}, {"type": "label", "text": "Appointment ID"}, {"type": "text", "text": "APT-0001"},
            {"type": "label", "text": "Doctor"}, {"type": "text", "text": "Dr. Sarah Johnson (Cardiology)"},
            {"type": "label", "text": "Date and time"}, {"type": "text", "text": "2025-01-15 10:30"},
            {"type": "label", "text": "Fee"}, {"type": "text", "text": "$150"}]},
    ],
}


def seed(make_live: bool = False) -> Scenario | None:
    db = database.SessionLocal()  # looked up at call time, so tests' database is used
    try:
        existing = db.query(Scenario).filter(Scenario.round_number == 2).all()
        if any((s.config_json or {}).get("paired_round1_title") == PAIRED_ROUND1_TITLE for s in existing):
            return None
        hr_user = db.query(User).filter(User.email == settings.hr_email).first()
        if hr_user is None:
            raise RuntimeError(f"No HR user ({settings.hr_email}) found - run seed.py first.")
        environments = {lang: (PROMPTS_DIR / name).read_text(encoding="utf-8") for lang, name in _HELPER_FILES.items()}
        scenario = Scenario(
            round_number=2, title=TITLE, description=INSTRUCTIONS, experience_band=ExperienceBand.junior,
            created_by=hr_user.id, status=ScenarioStatus.draft, time_limit_minutes=45,
            config_json={"mode": "ai_test_automation", "environment_code_by_language": environments,
                         "paired_round1_title": PAIRED_ROUND1_TITLE},
            reference_json={"ground_truth": GROUND_TRUTH, "validation_notes": VALIDATION_NOTES},
            environment_json=ENVIRONMENT_FACTS, ui_mockup_json=UI_MOCKUP, environment_hr_edited=True,
        )
        db.add(scenario)
        db.flush()
        if make_live:
            # The same state HR's Publish + "make live" produce (routers/hr.py
            # publish_scenario / move_to_screening) - one live per round and band.
            db.query(Scenario).filter(
                Scenario.round_number == 2, Scenario.experience_band == scenario.experience_band,
                Scenario.is_live.is_(True), Scenario.id != scenario.id,
            ).update({"is_live": False})
            scenario.status = ScenarioStatus.published
            scenario.published_at = datetime.utcnow()
            scenario.is_live = True
        db.commit()
        db.refresh(scenario)
        return scenario
    finally:
        db.close()


if __name__ == "__main__":
    result = seed(make_live="--make-live" in sys.argv[1:])
    if result is None:
        print("Nothing to do - the Doctor Appointments automation scenario already exists.")
    else:
        print(f"Created '{result.title}' (id={result.id}, {result.status.value}{', live' if result.is_live else ''}).")
