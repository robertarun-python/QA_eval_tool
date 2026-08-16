"""
Migration for round 3's open-ended-test-case redesign (see models.py:
Round3TestCase) plus the later additions to it (environment_json, then
ui_mockup_json - see Scenario). `Base.metadata.create_all()` only creates
*missing* tables, so the new round3_test_cases table needs an explicit
CREATE and the existing conversation_turns/scenarios tables need
explicit ALTERs for their new columns.

Idempotent - checks table/column presence first, so it's safe to run
more than once (e.g. after a fresh `create_all()` already created
everything for a brand new DB - nothing to do there).

The old `conversation_turns.phase` and `submissions.current_phase`
columns from the previous (fixed UI/API/DB phase) design are left in
place, inert - SQLite can't cheaply DROP COLUMN on older versions, and
it isn't worth the ceremony for two orphaned columns on a dev DB that
nothing reads anymore (models.py no longer maps them).

Run from backend/: python -m app.migrate_round3_freeform
Add --clear-existing to also delete any round-3 submissions/scores/turns
that predate this migration - they're shaped for the old fixed-phase
design (conversation_turns.phase, no test_case_id) and can't be
expressed under the new schema, so they'd render broken rather than
useful. Only pass this if you've confirmed that data is disposable
(e.g. your own exploratory testing, not real candidate submissions you
need to keep).
"""
import sqlite3
import sys

from .database import sqlite_db_path


def _has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _has_table(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def migrate(clear_existing: bool = False) -> dict:
    con = sqlite3.connect(sqlite_db_path())
    applied = []
    cleared = {}
    try:
        cur = con.cursor()

        if not _has_table(cur, "round3_test_cases"):
            cur.execute(
                """
                CREATE TABLE round3_test_cases (
                    id INTEGER PRIMARY KEY,
                    submission_id INTEGER NOT NULL REFERENCES submissions(id),
                    title VARCHAR,
                    draft_prompt TEXT NOT NULL DEFAULT '',
                    created_at DATETIME
                )
                """
            )
            applied.append("round3_test_cases (table)")

        if not _has_column(cur, "conversation_turns", "test_case_id"):
            cur.execute("ALTER TABLE conversation_turns ADD COLUMN test_case_id INTEGER")
            applied.append("conversation_turns.test_case_id")

        if not _has_column(cur, "scenarios", "environment_json"):
            cur.execute("ALTER TABLE scenarios ADD COLUMN environment_json TEXT")
            applied.append("scenarios.environment_json")

        if not _has_column(cur, "scenarios", "ui_mockup_json"):
            cur.execute("ALTER TABLE scenarios ADD COLUMN ui_mockup_json TEXT")
            applied.append("scenarios.ui_mockup_json")

        if clear_existing:
            cur.execute(
                "SELECT id FROM submissions WHERE round_number = 3 AND id NOT IN "
                "(SELECT DISTINCT submission_id FROM round3_test_cases)"
            )
            stale_ids = [row[0] for row in cur.fetchall()]
            if stale_ids:
                placeholders = ",".join("?" * len(stale_ids))
                cur.execute(
                    f"DELETE FROM conversation_turns WHERE submission_id IN ({placeholders})",
                    stale_ids,
                )
                cleared["conversation_turns"] = cur.rowcount
                cur.execute(
                    f"DELETE FROM scores WHERE submission_id IN ({placeholders})", stale_ids
                )
                cleared["scores"] = cur.rowcount
                cur.execute(
                    f"DELETE FROM submissions WHERE id IN ({placeholders})", stale_ids
                )
                cleared["submissions"] = cur.rowcount
                cleared["submission_ids"] = stale_ids

        con.commit()
    finally:
        con.close()
    return {"applied": applied, "cleared": cleared}


if __name__ == "__main__":
    result = migrate(clear_existing="--clear-existing" in sys.argv)
    if result["applied"]:
        print(f"Applied: {', '.join(result['applied'])}")
    else:
        print("Nothing to do - schema already up to date.")
    if result["cleared"]:
        print(f"Cleared (old-shaped round-3 data): {result['cleared']}")
