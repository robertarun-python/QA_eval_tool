"""
Migration for HR's score-override + scoring-failure-visibility feature
(see models.py: RoundStatus.scoring_failed, Submission.scoring_error,
Score's override audit columns). `Base.metadata.create_all()` only
creates missing tables, so the existing submissions/scores tables need
explicit ALTERs for their new columns.

Idempotent - checks column presence first, so it's safe to run more than
once (e.g. after a fresh `create_all()` already created everything for a
brand new DB - nothing to do there).

Run from backend/: python -m app.migrate_score_override
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

        if not _has_column(cur, "submissions", "scoring_error"):
            cur.execute("ALTER TABLE submissions ADD COLUMN scoring_error TEXT")
            applied.append("submissions.scoring_error")

        if not _has_column(cur, "scores", "original_final_score"):
            cur.execute("ALTER TABLE scores ADD COLUMN original_final_score INTEGER")
            applied.append("scores.original_final_score")

        if not _has_column(cur, "scores", "overridden_by_user_id"):
            cur.execute("ALTER TABLE scores ADD COLUMN overridden_by_user_id INTEGER REFERENCES users(id)")
            applied.append("scores.overridden_by_user_id")

        if not _has_column(cur, "scores", "override_note"):
            cur.execute("ALTER TABLE scores ADD COLUMN override_note TEXT")
            applied.append("scores.override_note")

        if not _has_column(cur, "scores", "overridden_at"):
            cur.execute("ALTER TABLE scores ADD COLUMN overridden_at DATETIME")
            applied.append("scores.overridden_at")

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
