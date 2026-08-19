"""
Migration for auto-closed-round tracking (see models.py:
Submission.auto_closed_reason). `Base.metadata.create_all()` only
creates missing tables, so the existing submissions table needs an
explicit ALTER for its new column.

Idempotent - checks column presence first, so it's safe to run more than
once (e.g. after a fresh `create_all()` already created everything for a
brand new DB - nothing to do there).

Run from backend/: python -m app.migrate_auto_close
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

        if not _has_column(cur, "submissions", "auto_closed_reason"):
            cur.execute("ALTER TABLE submissions ADD COLUMN auto_closed_reason TEXT")
            applied.append("submissions.auto_closed_reason")

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
