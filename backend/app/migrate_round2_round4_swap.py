"""
Migration for the Round 2 <-> Round 4 SWAP.

Before:  R1 Manual | R2 Debugging | R3 Coding | R4 Automation
After:   R1 Manual | R2 AI-Assisted Test Automation | R3 Coding | R4 Debugging

Unlike the earlier Round 3 -> Round 4 move (see
docs/superpowers/plans/2026-08-23-round3-to-round4-renumber.md), this one
VACATES NOTHING: every slot 1-4 still has content, so
routers/candidate.py's `_require_round_unlocked` gate stays satisfiable
and no round becomes permanently unreachable. That plan's warning about
shipping a renumbering that empties a slot does not apply here.

What this touches:
  scenarios.round_number    2 <-> 4
  submissions.round_number  2 <-> 4
  app_settings pass scores  round2_passing_score <-> round4_passing_score
      (the COLUMN names are slot-keyed and stay; the VALUES follow their
      content, since a threshold HR calibrated for debugging should keep
      applying to debugging at its new slot)

What this deliberately does NOT touch:
  - Table names (round4_test_cases), Python identifiers (Round4TestCase,
    score_round2_investigation, round4_*.txt prompts) and route helper
    names. These keep the round numbers they were built under, as a
    historical label - exactly the precedent this repo already set for
    migrate_round3_freeform.py / migrate_round3_code_cache.py. The single
    place that declares which slot runs which flow is
    scoring_service._SCORERS.
  - Candidate progress. A submission moves with its round_number, so a
    candidate who finished debugging keeps that completed submission - it
    simply now counts as slot 4. See this module's own
    `describe_progress_impact` and the migration test for the two cases
    that actually matter.

Idempotent via a marker row in app_settings? No - a straight double-swap
is self-inverse, so running this twice returns the DB to its ORIGINAL
state. Guard with --force if you really mean to run it again; the default
refuses once it detects the swap has already been applied.

Run from backend/: python -m app.migrate_round2_round4_swap
"""
import sqlite3
import sys

from .database import sqlite_db_path

_SWAP_MARKER = "round2_round4_swapped"


def _has_table(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _already_applied(cur: sqlite3.Cursor) -> bool:
    """Recorded in a tiny bookkeeping table rather than inferred from the
    data - the swap is self-inverse, so there is no way to tell "already
    swapped" apart from "not yet swapped" by looking at round numbers."""
    if not _has_table(cur, "schema_migrations"):
        return False
    cur.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (_SWAP_MARKER,))
    return cur.fetchone() is not None


def migrate(force: bool = False) -> list[str]:
    con = sqlite3.connect(sqlite_db_path())
    applied: list[str] = []
    try:
        cur = con.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")

        if _already_applied(cur) and not force:
            return []

        # -1/-3 as the temp slots: round_number has no CHECK constraint,
        # but negatives can never collide with a real round either way.
        for table in ("scenarios", "submissions"):
            cur.execute(f"UPDATE {table} SET round_number = -2 WHERE round_number = 2")
            moved_2 = cur.rowcount
            cur.execute(f"UPDATE {table} SET round_number = 2 WHERE round_number = 4")
            moved_4 = cur.rowcount
            cur.execute(f"UPDATE {table} SET round_number = 4 WHERE round_number = -2")
            if moved_2 or moved_4:
                applied.append(f"{table}: {moved_4} row(s) 4->2, {moved_2} row(s) 2->4")

        cur.execute("SELECT id, round2_passing_score, round4_passing_score FROM app_settings")
        for row_id, r2, r4 in cur.fetchall():
            if r2 != r4:
                cur.execute(
                    "UPDATE app_settings SET round2_passing_score = ?, round4_passing_score = ? WHERE id = ?",
                    (r4, r2, row_id),
                )
                applied.append(f"app_settings[{row_id}]: pass scores swapped ({r2}/{r4} -> {r4}/{r2})")

        cur.execute("INSERT OR REPLACE INTO schema_migrations (name) VALUES (?)", (_SWAP_MARKER,))
        con.commit()
    finally:
        con.close()
    return applied


if __name__ == "__main__":
    result = migrate(force="--force" in sys.argv)
    if result:
        print(f"Applied: {'; '.join(result)}")
    else:
        print("Nothing to do - the 2<->4 swap is already recorded as applied (pass --force to run it again).")
