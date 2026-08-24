"""
Migration for the round-3 sub-score breakdown (see models.py:
Score.correctness_score / precision_score / efficiency_score /
independent_judgment_score). `Base.metadata.create_all()` only creates
missing tables, so the existing scores table needs explicit ALTERs for
these new columns.

Idempotent - checks column presence first, so it's safe to run more than
once (e.g. after a fresh `create_all()` already created everything for a
brand new DB - nothing to do there).

Run from backend/: python -m app.migrate_round3_subscores
"""
import sqlite3

from .database import sqlite_db_path

_NEW_COLUMNS = ("correctness_score", "precision_score", "efficiency_score", "independent_judgment_score")


def _has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def migrate() -> list[str]:
    con = sqlite3.connect(sqlite_db_path())
    applied = []
    try:
        cur = con.cursor()

        for column in _NEW_COLUMNS:
            if not _has_column(cur, "scores", column):
                cur.execute(f"ALTER TABLE scores ADD COLUMN {column} INTEGER")
                applied.append(f"scores.{column}")

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
