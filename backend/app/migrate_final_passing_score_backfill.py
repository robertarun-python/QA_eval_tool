"""
Data-backfill migration for app_settings.final_passing_score (see
models.py: the column's default was corrected from a stale 3-round-era
210 to 280 - out of 400, now that 4 rounds contribute to the sum -
but a SQLAlchemy column default only ever applies to a brand-new INSERT,
never to an existing row. Any install created before that fix is still
silently hiring against the old, easier 210/400 (52.5%) bar instead of
the intended 280/400 (70%), with nothing in HR's Settings UI to reveal
the drift.

Deliberately narrow: only touches a row that's still sitting at the
EXACT stale default (210) - never overwrites a value HR may have
deliberately customized to something else. Idempotent - once a row is
past 210 (whether from this migration or a real HR edit), re-running is
a no-op for it.

Run from backend/: python -m app.migrate_final_passing_score_backfill
"""
import sqlite3

from .database import sqlite_db_path

_STALE_DEFAULT = 210
_CORRECTED_DEFAULT = 280


def migrate() -> list[str]:
    con = sqlite3.connect(sqlite_db_path())
    applied = []
    try:
        cur = con.cursor()
        cur.execute(
            "UPDATE app_settings SET final_passing_score = ? WHERE final_passing_score = ?",
            (_CORRECTED_DEFAULT, _STALE_DEFAULT),
        )
        if cur.rowcount > 0:
            applied.append(f"app_settings.final_passing_score: {cur.rowcount} row(s) {_STALE_DEFAULT} -> {_CORRECTED_DEFAULT}")
        con.commit()
    finally:
        con.close()
    return applied


if __name__ == "__main__":
    applied = migrate()
    if applied:
        print(f"Applied: {', '.join(applied)}")
    else:
        print("Nothing to do - no row is still at the stale default.")
