"""
Migration for per-round time limits set in HR Settings (see models.py:
AppSettings.round1..4_time_limit_minutes, Scenario.round_time_limit_minutes
and Submission.time_limit_minutes_at_start). `Base.metadata.create_all()`
only creates missing tables, so the existing app_settings and submissions
tables need explicit ALTERs. The new columns start empty (null), which
keeps today's behaviour - each scenario's own limit - until HR sets one.

Idempotent - checks column presence first, so it's safe to run more than
once.

Run from backend/: python -m app.migrate_round_time_limits
"""
import sqlite3

from .database import sqlite_db_path

_NEW_COLUMNS = (
    ("app_settings", "round1_time_limit_minutes"),
    ("app_settings", "round2_time_limit_minutes"),
    ("app_settings", "round3_time_limit_minutes"),
    ("app_settings", "round4_time_limit_minutes"),
    ("submissions", "time_limit_minutes_at_start"),
)


def _has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def migrate() -> list[str]:
    con = sqlite3.connect(sqlite_db_path())
    applied = []
    try:
        cur = con.cursor()
        for table, column in _NEW_COLUMNS:
            if not _has_column(cur, table, column):
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER")
                applied.append(f"{table}.{column}")
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
