"""
Migration for Round 3's AI-prompted-coding build (see
docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md): new
round3_turns/round3_execution_runs tables plus a fresh
app_settings.round3_passing_score column - distinct from round4's
column, which the earlier renumbering migration already renamed.

Idempotent - checks table/column presence first, so it's safe to run
more than once (e.g. after a fresh create_all() already created
everything for a brand new DB - nothing to do there).

Ordering note: on any DB that needs both, migrate_round_renumber.py must
run BEFORE this script. That script renames any EXISTING
app_settings.round3_passing_score (the old automation round's column) to
round4_passing_score; this script then adds a brand new
round3_passing_score column for the new round. Run this script first
instead, and migrate_round_renumber.py would find the fresh
round3_passing_score column this script just added and rename THAT one
away to round4_passing_score, breaking AppSettings reads until this
script is re-run (see migrate_round_renumber.py's own docstring).

Run from backend/: python -m app.migrate_round3_coding
"""
import sqlite3

from .database import sqlite_db_path


def _has_table(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def migrate() -> list[str]:
    con = sqlite3.connect(sqlite_db_path())
    applied = []
    try:
        cur = con.cursor()

        if not _has_table(cur, "round3_turns"):
            cur.execute(
                """
                CREATE TABLE round3_turns (
                    id INTEGER PRIMARY KEY,
                    submission_id INTEGER NOT NULL REFERENCES submissions(id),
                    turn_number INTEGER NOT NULL,
                    candidate_prompt TEXT NOT NULL,
                    language VARCHAR NOT NULL,
                    response_kind VARCHAR NOT NULL,
                    response_message TEXT NOT NULL,
                    code_after TEXT,
                    created_at DATETIME
                )
                """
            )
            applied.append("round3_turns (table)")

        if not _has_table(cur, "round3_execution_runs"):
            cur.execute(
                """
                CREATE TABLE round3_execution_runs (
                    id INTEGER PRIMARY KEY,
                    submission_id INTEGER NOT NULL REFERENCES submissions(id),
                    turn_id INTEGER REFERENCES round3_turns(id),
                    language VARCHAR NOT NULL,
                    code_snapshot TEXT NOT NULL,
                    stdin_json TEXT,
                    stdout TEXT,
                    stderr TEXT,
                    exit_code INTEGER,
                    timed_out BOOLEAN NOT NULL DEFAULT 0,
                    infra_error BOOLEAN NOT NULL DEFAULT 0,
                    duration_ms INTEGER,
                    created_at DATETIME
                )
                """
            )
            applied.append("round3_execution_runs (table)")

        if not _has_column(cur, "app_settings", "round3_passing_score"):
            cur.execute("ALTER TABLE app_settings ADD COLUMN round3_passing_score INTEGER NOT NULL DEFAULT 70")
            applied.append("app_settings.round3_passing_score")

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
