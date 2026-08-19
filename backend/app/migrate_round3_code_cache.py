"""
Migration for persisted round3 code snippets (see models.py:
ConversationTurn.generated_code_json). `Base.metadata.create_all()` only
creates missing tables, so the existing conversation_turns table needs
an explicit ALTER for its new column.

Idempotent - checks column presence first, so it's safe to run more than
once (e.g. after a fresh `create_all()` already created everything for a
brand new DB - nothing to do there).

Run from backend/: python -m app.migrate_round3_code_cache
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

        if not _has_column(cur, "conversation_turns", "generated_code_json"):
            cur.execute("ALTER TABLE conversation_turns ADD COLUMN generated_code_json TEXT")
            applied.append("conversation_turns.generated_code_json")

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
