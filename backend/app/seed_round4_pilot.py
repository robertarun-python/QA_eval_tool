"""
Seeds the one Round 4 "Focused Automation Pilot" scenario - a single-file
Python automation exercise (see prompts/round4_pilot_starter_code.txt and
prompts/round4_pilot_reference_solution.txt), distinct from the legacy
round 4 conversation-based flow (selected via Scenario.config_json
["mode"] == "pilot_automation" - see routers/hr.py's publish_scenario and
routers/candidate.py's pilot endpoints).

No schema change needed (unlike migrate_*.py scripts) - this only inserts
a normal Scenario row using columns that already exist (config_json/
reference_json, both designed to hold "round-specific extras" - see
models.Scenario's own docstring). Idempotent: does nothing if a round 4
pilot scenario already exists. Not wired into seed.py's create_all() -
same standalone-entrypoint convention as every migrate_*.py script.

Run from backend/: python -m app.seed_round4_pilot
"""
from pathlib import Path

from .config import settings
from .database import SessionLocal
from .models import Scenario, ScenarioStatus, ExperienceBand, User

PROMPTS_DIR = Path(__file__).parent / "prompts"

REQUIREMENT_TEXT = (
    "Automate the customer transaction flow for a valid customer.\n\n"
    "The test must verify that the transaction is successfully completed and that it "
    "is persisted correctly.\n\n"
    "The test should be repeatable and should not depend on data left behind by "
    "previous test executions.\n\n"
    "You may use the provided AI assistant for assistance. You are responsible for "
    "understanding, validating, and maintaining the final automation."
)

VALIDATION_NOTES = (
    "A strong submission: verifies the transaction reaches a completed state; "
    "validates persistence against the database record specifically (not just the "
    "UI/API's own report of success) - either because the candidate asked for "
    "clarification and was told the DB is the source of truth, or because they "
    "reasoned their way to checking it independently; generates its own transaction "
    "rather than reusing hard-coded data, so repeated runs don't collide; cleans up "
    "the transaction it created, not relying on incomplete existing teardown; and "
    "reuses the existing UIHelper/ApiHelper/DbHelper/setup/teardown helpers rather "
    "than reimplementing that logic inline. Do not penalize a different but valid "
    "structure - judge behavior and intent, not literal similarity to the reference."
)


def seed() -> Scenario | None:
    db = SessionLocal()
    try:
        existing = (
            db.query(Scenario)
            .filter(Scenario.round_number == 4)
            .all()
        )
        if any((s.config_json or {}).get("mode") == "pilot_automation" for s in existing):
            return None

        hr_user = db.query(User).filter(User.email == settings.hr_email).first()
        if hr_user is None:
            raise RuntimeError(f"No HR user ({settings.hr_email}) found - run seed.py first.")

        starter_code = (PROMPTS_DIR / "round4_pilot_starter_code.txt").read_text(encoding="utf-8")
        reference_solution = (PROMPTS_DIR / "round4_pilot_reference_solution.txt").read_text(encoding="utf-8")

        scenario = Scenario(
            round_number=4,
            title="Focused Automation Pilot: Customer Transaction Flow",
            description=REQUIREMENT_TEXT,
            # _live_scenario matches experience_band exactly against the
            # candidate's own band (never "both" - see candidate.py) - and
            # every candidate in this deployment is seeded as "0-7" (see
            # memory qa_eval_experience_band_hidden), so that's the band
            # that actually serves this scenario to real candidates.
            experience_band=ExperienceBand.junior,
            created_by=hr_user.id,
            status=ScenarioStatus.draft,
            config_json={"mode": "pilot_automation", "starter_code": starter_code},
            reference_json={"reference_solution": reference_solution, "validation_notes": VALIDATION_NOTES},
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
        print("Nothing to do - a round 4 pilot scenario already exists.")
    else:
        print(f"Created draft round 4 pilot scenario (id={result.id}). Review and publish it from the HR Scenarios screen.")
