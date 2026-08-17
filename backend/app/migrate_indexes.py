"""
Migration adding indexes on foreign-key columns that get filtered on
constantly but were never indexed (see models.py: Submission.user_id/
scenario_id, Round3TestCase.submission_id, ConversationTurn.submission_id/
test_case_id, CandidateAppearance.user_id). SQLite doesn't auto-index
foreign keys, and `Base.metadata.create_all()` only creates missing
tables/columns, not indexes on already-existing columns - this needs an
explicit CREATE INDEX.

Index names match SQLAlchemy's own `index=True` auto-naming convention
(ix_<table>_<column>), so a fresh DB created via create_all() and an
existing DB migrated via this script end up with identical schemas.

Idempotent - CREATE INDEX IF NOT EXISTS is safe to run more than once.

Run from backend/: python -m app.migrate_indexes
"""
import sqlite3

from .database import sqlite_db_path

INDEXES = [
    ("ix_submissions_user_id", "submissions", "user_id"),
    ("ix_submissions_scenario_id", "submissions", "scenario_id"),
    ("ix_round3_test_cases_submission_id", "round3_test_cases", "submission_id"),
    ("ix_conversation_turns_submission_id", "conversation_turns", "submission_id"),
    ("ix_conversation_turns_test_case_id", "conversation_turns", "test_case_id"),
    ("ix_candidate_appearances_user_id", "candidate_appearances", "user_id"),
]


def migrate() -> list[str]:
    con = sqlite3.connect(sqlite_db_path())
    applied = []
    try:
        cur = con.cursor()
        for index_name, table, column in INDEXES:
            cur.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})")
            applied.append(index_name)
        con.commit()
    finally:
        con.close()
    return applied


if __name__ == "__main__":
    applied = migrate()
    print(f"Ensured indexes exist: {', '.join(applied)}")
