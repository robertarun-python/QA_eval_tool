"""
Migration for single-active-session enforcement (see models.py:
User.active_session_id, dependencies.get_current_user, routers/auth.py's
login/logout). `Base.metadata.create_all()` only creates missing tables,
so the existing users table needs an explicit ALTER for its new column.

Idempotent - checks column presence first, so it's safe to run more than
once.

Run from backend/: python -m app.migrate_active_session
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

        if not _has_column(cur, "users", "active_session_id"):
            cur.execute("ALTER TABLE users ADD COLUMN active_session_id TEXT")
            applied.append("users.active_session_id")

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
