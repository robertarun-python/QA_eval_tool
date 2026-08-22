"""
Migration for the Round 3 -> Round 4 renumbering (see
docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md's
"Renumbering" section): the existing prompt-driven test automation round
moves from round_number 3 to round_number 4, freeing up round_number 3
for the new AI-prompted-coding round. This is a dev-seeded local SQLite
DB (no real candidate data at stake), so a straight in-place UPDATE/ALTER
is sufficient.

Idempotent - checks state first, so it's safe to run more than once.

Run from backend/: python -m app.migrate_round_renumber
"""
import sqlite3

from .database import sqlite_db_path


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
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

        cur.execute("UPDATE scenarios SET round_number = 4 WHERE round_number = 3")
        if cur.rowcount > 0:
            applied.append(f"scenarios: {cur.rowcount} row(s) round_number 3 -> 4")

        cur.execute("UPDATE submissions SET round_number = 4 WHERE round_number = 3")
        if cur.rowcount > 0:
            applied.append(f"submissions: {cur.rowcount} row(s) round_number 3 -> 4")

        if _table_exists(cur, "round3_test_cases") and not _table_exists(cur, "round4_test_cases"):
            cur.execute("ALTER TABLE round3_test_cases RENAME TO round4_test_cases")
            applied.append("round3_test_cases -> round4_test_cases (table renamed)")

        if _has_column(cur, "app_settings", "round3_passing_score") and not _has_column(cur, "app_settings", "round4_passing_score"):
            cur.execute("ALTER TABLE app_settings RENAME COLUMN round3_passing_score TO round4_passing_score")
            applied.append("app_settings.round3_passing_score -> round4_passing_score (column renamed)")

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
