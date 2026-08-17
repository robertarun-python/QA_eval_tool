"""
Migration for score provenance (see models.py: Score.scoring_model,
scoring_prompt_file, scoring_prompt_hash, scored_at). `Base.metadata.
create_all()` only creates missing tables, so the existing scores table
needs explicit ALTERs for its new columns.

Idempotent - checks column presence first, so it's safe to run more than
once (e.g. after a fresh `create_all()` already created everything for a
brand new DB - nothing to do there).

Run from backend/: python -m app.migrate_score_provenance
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

        if not _has_column(cur, "scores", "scoring_model"):
            cur.execute("ALTER TABLE scores ADD COLUMN scoring_model TEXT")
            applied.append("scores.scoring_model")

        if not _has_column(cur, "scores", "scoring_prompt_file"):
            cur.execute("ALTER TABLE scores ADD COLUMN scoring_prompt_file TEXT")
            applied.append("scores.scoring_prompt_file")

        if not _has_column(cur, "scores", "scoring_prompt_hash"):
            cur.execute("ALTER TABLE scores ADD COLUMN scoring_prompt_hash TEXT")
            applied.append("scores.scoring_prompt_hash")

        if not _has_column(cur, "scores", "scored_at"):
            cur.execute("ALTER TABLE scores ADD COLUMN scored_at DATETIME")
            applied.append("scores.scored_at")

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
