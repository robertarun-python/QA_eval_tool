"""
Migration for Round 5 (Progressive Engineering) POC Phase 5: adds
progressive_ai_turns.candidate_facing_response (see
models_progressive.ProgressiveAiTurn) - a new nullable column, not a new
table, so this is a separate incremental migration on top of
migrate_progressive_coding.py rather than an edit to that already-applied
script, same convention every other incremental change in this codebase
follows (e.g. migrate_round3_freeform.py on top of
migrate_round3_coding.py).

Idempotent - checks column presence first, so it's safe to run more than
once.

Run from backend/: python -m app.migrate_progressive_pipeline
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
        if not _has_column(cur, "progressive_ai_turns", "candidate_facing_response"):
            cur.execute("ALTER TABLE progressive_ai_turns ADD COLUMN candidate_facing_response TEXT")
            applied.append("progressive_ai_turns.candidate_facing_response")
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
