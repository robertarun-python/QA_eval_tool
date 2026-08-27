"""
Migration for Round 3's construct-checklist state (see models.py:
Round3Turn.declared_constructs_json). `Base.metadata.create_all()` only
creates missing tables, so the existing round3_turns table needs an
explicit ALTER for its new column. See
docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md.

Idempotent - checks column presence first, so it's safe to run more than
once (e.g. after a fresh `create_all()` already created everything for a
brand new DB - nothing to do there).

Run from backend/: python -m app.migrate_round3_construct_state
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

        if not _has_column(cur, "round3_turns", "declared_constructs_json"):
            cur.execute("ALTER TABLE round3_turns ADD COLUMN declared_constructs_json TEXT")
            applied.append("round3_turns.declared_constructs_json")

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
