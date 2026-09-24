"""
Seeds the AI-Assisted Test Automation scenario (round 2 slot since the
2<->4 renumbering - see docs/superpowers/plans/2026-09-18-round2-round4-swap.md;
config_json["mode"] == "ai_test_automation"). The candidate automates the
test cases THEY designed in round 1, so this scenario carries no test-case
reference of its own - what it does carry is:

  config_json["environment_code_by_language"]  the small provided helper
      environment, per executable language (see prompts/round2_automation_helpers_*.txt)
  reference_json["ground_truth"]               HR/system-only: how that
      environment actually behaves, so the scorer can judge whether the
      candidate's interpretation of their own run is CORRECT
  reference_json["validation_notes"]           HR/system-only judging notes

Neither reference_json key is ever sent to the candidate or to the
generator - see routers/candidate.py's Round2AutomationStateOut (no field for
them) and prompts/round2_automation_scoring.txt's REFERENCE ONLY section.

No schema change and no migration: this inserts a normal Scenario row
using columns that already exist. Idempotent. Standalone entrypoint, same
convention as every migrate_*.py / seed_round4_pilot.py.

Run from backend/: python -m app.seed_round2_automation
"""
from pathlib import Path

from .config import settings
from .database import SessionLocal
from .models import Scenario, ScenarioStatus, ExperienceBand, User

PROMPTS_DIR = Path(__file__).parent / "prompts"

_HELPER_FILES = {
    "python": "round2_automation_helpers_python.txt",
    "javascript": "round2_automation_helpers_javascript.txt",
    "java": "round2_automation_helpers_java.txt",
}

INSTRUCTIONS = (
    "Automate the test case(s) you designed in Round 1.\n\n"
    "Pick one or two of your own Round 1 test cases, then use the AI assistant to turn that "
    "design into working automation against the provided environment. The assistant will only "
    "encode what you already specified - it won't invent test cases, test data, assertions or "
    "extra coverage, so anything missing from your design is yours to supply.\n\n"
    "Review everything it produces, edit the code yourself where you disagree, run it, and then "
    "explain what the result actually proves. You are responsible for the final automation."
)

GROUND_TRUTH = (
    "The provided environment behaves as follows, and this is the truth the candidate's "
    "interpretation should be judged against:\n"
    "- UI.login / login() returns True ONLY for jordan.rivera@example.com with password "
    "Passw0rd!2026; any other combination returns False. It does not raise.\n"
    "- API.create_record / createRecord REJECTS an amount <= 0: it returns "
    "{ok: False, error: 'Amount must be greater than zero', id: None}, sets the visible message "
    "to that same error, and writes NOTHING to the database.\n"
    "- For an amount > 0 it persists a record with status 'completed', returns "
    "{ok: True, error: None, id: 'REC-nnnn'}, and sets the visible message to 'Saved successfully'.\n"
    "- Database.find/find returns the persisted record or None. It is the only real proof of "
    "persistence; the API's own ok/id response is the system reporting on itself.\n"
    "- teardown(record_id) deletes that record; nothing else resets between runs within a single "
    "execution, and each execution starts from a fresh process.\n\n"
    "Consequences worth checking the candidate's interpretation against: a test that asserts only "
    "on the value it just passed in, or only on the API's own ok flag, does NOT prove persistence. "
    "A rejected-amount test that then asserts a record exists is asserting the opposite of the "
    "environment's real behaviour and should fail - if it passed, the candidate's test is not "
    "testing what they think it is. An exit code of 0 alone proves only that the file ran."
)

VALIDATION_NOTES = (
    "A strong submission: automates the candidate's own selected design faithfully, reuses the "
    "provided helpers instead of rebuilding them, encodes exactly the data and expected results "
    "they specified, and asserts something that would genuinely fail if the requirement were "
    "broken (not an assertion that restates an input). It shows real review of the assistant's "
    "output - a correction, a follow-up that catches a problem, or a direct edit - rather than "
    "wholesale acceptance. Its interpretation of the run matches the ground truth above, "
    "including noticing a test that passed for the wrong reason. Do not reward assertion COUNT, "
    "and do not penalise a valid approach that differs from any particular implementation."
)


def seed() -> Scenario | None:
    db = SessionLocal()
    try:
        existing = db.query(Scenario).filter(Scenario.round_number == 2).all()
        if any((s.config_json or {}).get("mode") == "ai_test_automation" for s in existing):
            return None

        hr_user = db.query(User).filter(User.email == settings.hr_email).first()
        if hr_user is None:
            raise RuntimeError(f"No HR user ({settings.hr_email}) found - run seed.py first.")

        environments = {
            language: (PROMPTS_DIR / filename).read_text(encoding="utf-8")
            for language, filename in _HELPER_FILES.items()
        }

        scenario = Scenario(
            round_number=2,
            title="AI-Assisted Test Automation",
            description=INSTRUCTIONS,
            # _live_scenario matches the candidate's own band exactly
            # (never "both") and every candidate here is seeded "0-7".
            experience_band=ExperienceBand.junior,
            created_by=hr_user.id,
            status=ScenarioStatus.draft,
            config_json={"mode": "ai_test_automation", "environment_code_by_language": environments},
            reference_json={"ground_truth": GROUND_TRUTH, "validation_notes": VALIDATION_NOTES},
            time_limit_minutes=45,
        )
        db.add(scenario)
        db.commit()
        db.refresh(scenario)
        return scenario
    finally:
        db.close()


if __name__ == "__main__":
    result = seed()
    if result is None:
        print("Nothing to do - an AI-assisted automation scenario already exists.")
    else:
        print(f"Created draft automation scenario (id={result.id}). Review and publish it from the HR Scenarios screen.")
