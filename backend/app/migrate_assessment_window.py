"""
Migration for the overall assessment-window setting (see models.py:
AppSettings.assessment_window_days, scoring_service.
close_expired_assessment_windows). `Base.metadata.create_all()` only
creates missing tables, so the existing app_settings table needs an
explicit ALTER for its new column.

Idempotent - checks column presence first, so it's safe to run more than
once.

Run from backend/: python -m app.migrate_assessment_window
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

        if not _has_column(cur, "app_settings", "assessment_window_days"):
            cur.execute("ALTER TABLE app_settings ADD COLUMN assessment_window_days INTEGER NOT NULL DEFAULT 1")
            applied.append("app_settings.assessment_window_days")

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
