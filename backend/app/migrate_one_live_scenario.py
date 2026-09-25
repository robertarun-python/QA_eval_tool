"""
Migration adding the "one live scenario per round and band" guard (see
the uq_scenarios_one_live_per_round_band index below models.Scenario).
Candidates are served the single live scenario for their round and band;
the HR endpoints already switch the old one off before switching a new
one on, and this index makes the database itself refuse a second live
row. `Base.metadata.create_all()` never adds an index to an existing
table, so an existing database needs this explicit CREATE INDEX.

Refuses to run - changing nothing - if some round and band already has
more than one live scenario: which one candidates should keep seeing is
HR's call, not this script's. It lists the clashing scenarios so HR can
pick one in the Live column (or fix is_live directly) and run it again.

The SQL matches what create_all() emits for the model's index, so a fresh
DB and a migrated one end up with identical schemas.

Idempotent - CREATE INDEX IF NOT EXISTS is safe to run more than once.

Run from backend/: python -m app.migrate_one_live_scenario
"""
import sqlite3

from .database import sqlite_db_path

INDEX_NAME = "uq_scenarios_one_live_per_round_band"
CREATE_INDEX = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME} "
    "ON scenarios (round_number, experience_band) WHERE is_live IS 1"
)


class MultipleLiveScenarios(Exception):
    pass


def migrate() -> bool:
    """True if the index was created, False if it already existed.
    Raises MultipleLiveScenarios (nothing changed) on a clash."""
    con = sqlite3.connect(sqlite_db_path())
    try:
        cur = con.cursor()
        if cur.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (INDEX_NAME,)).fetchone():
            return False
        clashes = cur.execute(
            "SELECT round_number, experience_band, group_concat(id || ' ' || quote(title), ', ') "
            "FROM scenarios WHERE is_live IS 1 GROUP BY round_number, experience_band HAVING count(*) > 1"
        ).fetchall()
        if clashes:
            lines = [f"  Round {r} ({band}): {ids}" for r, band, ids in clashes]
            raise MultipleLiveScenarios(
                "More than one live scenario for the same round and band - make only one live, then run this again:\n"
                + "\n".join(lines)
            )
        cur.execute(CREATE_INDEX)
        con.commit()
        return True
    finally:
        con.close()


if __name__ == "__main__":
    try:
        created = migrate()
    except MultipleLiveScenarios as e:
        raise SystemExit(str(e))
    print(f"{'Created' if created else 'Already present'}: {INDEX_NAME}")
