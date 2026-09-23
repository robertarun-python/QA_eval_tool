"""
Data migration: state the input/output format on the existing Round 3
"Second-Largest Distinct Value" task (see services/round3_io_format.py).

Its hidden tests were generated as one line of comma-separated integers,
but the task never said so - Run B's candidate asked, wasn't told, guessed
spaces, and failed 13/15 tests with otherwise correct logic. This sets the
task's config_json["round3_io_format"], which the candidate's screen, the
assistant and any regenerated reference all read from.

Refuses to write if any stored hidden-test input doesn't match the format
it would state. Idempotent - a task that already has the format is left
alone.

Run from backend/: python -m app.migrate_round3_io_format
"""
import json
import sqlite3

from .database import sqlite_db_path
from .services import round3_io_format

TASK_TITLE = "Second-Largest Distinct Value"

IO_FORMAT = {
    "input": "One line of comma-separated integers (no spaces), read from standard input. An empty line means an empty list.",
    "output": "Print a single integer on one line - the second-largest distinct value, or -1 - with no labels or extra text.",
    "value_type": "integers",
}


def migrate() -> list[str]:
    con = sqlite3.connect(sqlite_db_path())
    applied = []
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT id, config_json, reference_json FROM scenarios WHERE round_number = 3 AND title = ?",
            (TASK_TITLE,),
        )
        for scenario_id, config_raw, reference_raw in cur.fetchall():
            config = json.loads(config_raw) if config_raw else {}
            if config.get(round3_io_format.CONFIG_KEY):
                continue
            reference = json.loads(reference_raw) if reference_raw else {}
            off_format = [
                tc.get("input") for tc in reference.get("test_cases", [])
                if not round3_io_format.input_conforms(tc.get("input"), IO_FORMAT)
            ]
            if off_format:
                raise ValueError(f"scenario {scenario_id}: hidden-test inputs don't match the format: {off_format!r}")
            config[round3_io_format.CONFIG_KEY] = IO_FORMAT
            cur.execute("UPDATE scenarios SET config_json = ? WHERE id = ?", (json.dumps(config), scenario_id))
            applied.append(f"scenarios.{scenario_id}.config_json.{round3_io_format.CONFIG_KEY}")
        con.commit()
    finally:
        con.close()
    return applied


if __name__ == "__main__":
    applied = migrate()
    if applied:
        print(f"Applied: {', '.join(applied)}")
    else:
        print("Nothing to do - format already stated.")
