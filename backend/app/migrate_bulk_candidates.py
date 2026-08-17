"""
Migration for HR's bulk candidate upload / runtime settings / re-application
feature (see models.py: User.username, CandidateAppearance, Submission.archived
/appearance_id, AppSettings). `Base.metadata.create_all()` only creates
*missing* tables, so the new candidate_appearances/app_settings tables need
explicit CREATEs and the existing users/submissions tables need explicit
ALTERs for their new columns.

Idempotent - checks table/column presence first, so it's safe to run more
than once (e.g. after a fresh `create_all()` already created everything for
a brand new DB - nothing to do there).

Also seeds the app_settings singleton row (id=1) with defaults on first run,
from config.py's passing_score where applicable (its only remaining use -
see that file's docstring) - HR edits these afterward through the Settings
page, not by editing .env.

Run from backend/: python -m app.migrate_bulk_candidates
"""
import sqlite3

from .config import settings
from .database import sqlite_db_path


def _has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _has_table(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def migrate() -> list[str]:
    con = sqlite3.connect(sqlite_db_path())
    applied = []
    try:
        cur = con.cursor()

        if not _has_column(cur, "users", "username"):
            cur.execute("ALTER TABLE users ADD COLUMN username VARCHAR")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users(username)")
            applied.append("users.username")

        if not _has_table(cur, "candidate_appearances"):
            cur.execute(
                """
                CREATE TABLE candidate_appearances (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    email VARCHAR NOT NULL,
                    exam_date DATETIME NOT NULL,
                    is_current BOOLEAN NOT NULL DEFAULT 1,
                    reapplied_within_window BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME
                )
                """
            )
            applied.append("candidate_appearances (table)")

        if not _has_column(cur, "submissions", "archived"):
            cur.execute("ALTER TABLE submissions ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0")
            applied.append("submissions.archived")

        if not _has_column(cur, "submissions", "appearance_id"):
            cur.execute("ALTER TABLE submissions ADD COLUMN appearance_id INTEGER REFERENCES candidate_appearances(id)")
            applied.append("submissions.appearance_id")

        if not _has_table(cur, "app_settings"):
            cur.execute(
                """
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY,
                    round1_passing_score INTEGER NOT NULL DEFAULT 70,
                    round2_passing_score INTEGER NOT NULL DEFAULT 70,
                    round3_passing_score INTEGER NOT NULL DEFAULT 70,
                    final_passing_score INTEGER NOT NULL DEFAULT 210,
                    reapplication_window_months INTEGER NOT NULL DEFAULT 6,
                    updated_at DATETIME
                )
                """
            )
            default_round_score = settings.passing_score
            cur.execute(
                """
                INSERT INTO app_settings
                    (id, round1_passing_score, round2_passing_score, round3_passing_score,
                     final_passing_score, reapplication_window_months, updated_at)
                VALUES (1, ?, ?, ?, ?, 6, CURRENT_TIMESTAMP)
                """,
                (default_round_score, default_round_score, default_round_score, default_round_score * 3),
            )
            applied.append("app_settings (table + default row)")

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
