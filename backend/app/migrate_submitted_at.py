"""
Migration for models.py: Submission.submitted_at - "when did the
candidate actually finish this round" (real submit, candidate-triggered
timeout, or lazy server-side timeout close), previously not tracked
anywhere - only started_at (round start) and created_at (row creation,
~equal to started_at) existed.

Idempotent - checks column presence first, so it's safe to run more than
once (e.g. after a fresh `create_all()` already created everything for a
brand new DB - nothing to do there).

Best-effort backfill for EXISTING rows already in a terminal state
(submitted/scored/scoring_failed): there's no real record of exactly
when those finished, so this sets submitted_at to started_at as the
closest available approximation, rather than leaving historical rows
with no value at all (a bare NULL there would show as blank in HR's
dashboard even for a long-since-finished round, which reads as "still
missing data" rather than "finished before this column existed"). New
rows going forward get the real, precise value from the code paths that
now set it directly - see the models.py comment on this column.

Run from backend/: python -m app.migrate_submitted_at
"""
import sqlite3

from .database import sqlite_db_path


def _has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def migrate() -> list[str]:
    con = sqlite3.connect(sqlite_db_path())
    applied = []
    try:
        cur = con.cursor()

        if not _has_column(cur, "submissions", "submitted_at"):
            cur.execute("ALTER TABLE submissions ADD COLUMN submitted_at DATETIME")
            applied.append("submissions.submitted_at (column)")

        cur.execute(
            "UPDATE submissions SET submitted_at = started_at "
            "WHERE submitted_at IS NULL AND started_at IS NOT NULL "
            "AND status IN ('submitted', 'scored', 'scoring_failed')"
        )
        if cur.rowcount > 0:
            applied.append(f"submissions.submitted_at: {cur.rowcount} existing row(s) backfilled from started_at")

        con.commit()
    finally:
        con.close()
    return applied


if __name__ == "__main__":
    applied = migrate()
    if applied:
        print(f"Applied: {', '.join(applied)}")
    else:
        print("Nothing to do - schema already up to date.")
