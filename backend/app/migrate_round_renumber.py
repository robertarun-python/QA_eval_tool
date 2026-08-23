"""
Migration for the Round 3 -> Round 4 renumbering (see
docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md's
"Renumbering" section): the existing prompt-driven test automation round
moves from round_number 3 to round_number 4, freeing up round_number 3
for the new AI-prompted-coding round. This is a dev-seeded local SQLite
DB (no real candidate data at stake), so a straight in-place UPDATE/ALTER
is sufficient.

Idempotent for its own step - checks state first, so it's safe to run
more than once *while round_number=3 still only ever means the old
automation round*. It is NOT safe to blanket-rerun once the new
AI-prompted-coding round (also round_number=3) has real scenarios/
submissions: re-running the plain "round_number = 3 -> 4" UPDATE then
would reclassify that real data as round 4 too. To guard against that,
the scenarios/submissions UPDATEs below skip any row that's genuinely
part of the NEW round 3: a submission with associated round3_turns rows
(see migrate_round3_coding.py), or a scenario referenced only by such
submissions. The old (pre-renumbering) round-3-automation submissions
never have round3_turns rows - that table didn't exist when they were
created, and a submission already migrated away to round_number=4
wouldn't have any either - so this is a reliable way to tell the two
apart without a dedicated marker column.

Ordering note: this script must run BEFORE migrate_round3_coding.py on
any DB that needs both. migrate_round3_coding.py adds a fresh
app_settings.round3_passing_score column; this script renames any
EXISTING round3_passing_score (the old automation round's column) to
round4_passing_score. Run in the wrong order and this script would rename
away the new column that migrate_round3_coding.py just added, breaking
AppSettings reads until that script is re-run.

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

        # round3_turns only exists once migrate_round3_coding.py has run
        # (see the ordering note above) - if it's absent, the new round 3
        # can't have any real data yet, so the plain unconditional
        # UPDATEs below are safe exactly as before.
        has_round3_turns = _table_exists(cur, "round3_turns")
        protected_submissions_clause = (
            " AND id NOT IN (SELECT DISTINCT submission_id FROM round3_turns)" if has_round3_turns else ""
        )
        protected_scenarios_clause = (
            """
            AND id NOT IN (
                SELECT DISTINCT sub.scenario_id
                FROM submissions sub
                JOIN round3_turns rt ON rt.submission_id = sub.id
            )
            """ if has_round3_turns else ""
        )

        cur.execute(f"UPDATE scenarios SET round_number = 4 WHERE round_number = 3{protected_scenarios_clause}")
        if cur.rowcount > 0:
            applied.append(f"scenarios: {cur.rowcount} row(s) round_number 3 -> 4")

        cur.execute(f"UPDATE submissions SET round_number = 4 WHERE round_number = 3{protected_submissions_clause}")
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
