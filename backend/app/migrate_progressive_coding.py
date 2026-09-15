"""
Migration for Round 5 (Progressive Engineering) POC Phase 1: data model
only - see models_progressive.py, which this creates the tables for.

Standalone and self-contained, same as every other migrate_*.py script:
not imported by seed.py's Base.metadata.create_all() (that only sees
models.py's classes, since that's the only models module seed.py
imports) - run this explicitly, same as any other migration here, to
create these 5 new tables on an existing DB. No ordering dependency on
any other migration: every table this creates is brand new, and its
only foreign keys point at the pre-existing `users` table.

Idempotent - checks table presence first, so it's safe to run more than
once.

Run from backend/: python -m app.migrate_progressive_coding
"""
import sqlite3

from .database import sqlite_db_path


def _has_table(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def migrate() -> list[str]:
    con = sqlite3.connect(sqlite_db_path())
    applied = []
    try:
        cur = con.cursor()

        if not _has_table(cur, "progressive_problems"):
            cur.execute(
                """
                CREATE TABLE progressive_problems (
                    id INTEGER PRIMARY KEY,
                    title VARCHAR NOT NULL,
                    description TEXT NOT NULL,
                    input_spec_json TEXT NOT NULL DEFAULT '{}',
                    language VARCHAR NOT NULL,
                    starter_code TEXT,
                    difficulty VARCHAR,
                    time_limit_minutes INTEGER NOT NULL DEFAULT 30,
                    experience_band VARCHAR,
                    status VARCHAR NOT NULL DEFAULT 'draft',
                    created_by INTEGER NOT NULL REFERENCES users(id),
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
            applied.append("progressive_problems (table)")

        if not _has_table(cur, "progressive_requirements"):
            cur.execute(
                """
                CREATE TABLE progressive_requirements (
                    id INTEGER PRIMARY KEY,
                    problem_id INTEGER NOT NULL REFERENCES progressive_problems(id),
                    stage_order INTEGER NOT NULL,
                    requirement_text TEXT NOT NULL,
                    expected_behavior TEXT,
                    hidden_tests_json TEXT,
                    reference_solution TEXT,
                    frozen BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME,
                    UNIQUE (problem_id, stage_order),
                    CHECK (stage_order > 0)
                )
                """
            )
            cur.execute(
                "CREATE INDEX ix_progressive_requirements_problem_id ON progressive_requirements (problem_id)"
            )
            applied.append("progressive_requirements (table)")

        if not _has_table(cur, "progressive_attempts"):
            cur.execute(
                """
                CREATE TABLE progressive_attempts (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    problem_id INTEGER NOT NULL REFERENCES progressive_problems(id),
                    current_stage INTEGER NOT NULL DEFAULT 1,
                    status VARCHAR NOT NULL DEFAULT 'in_progress',
                    started_at DATETIME,
                    completed_at DATETIME,
                    archived BOOLEAN NOT NULL DEFAULT 0
                )
                """
            )
            cur.execute("CREATE INDEX ix_progressive_attempts_user_id ON progressive_attempts (user_id)")
            cur.execute("CREATE INDEX ix_progressive_attempts_problem_id ON progressive_attempts (problem_id)")
            applied.append("progressive_attempts (table)")

        if not _has_table(cur, "progressive_stage_results"):
            cur.execute(
                """
                CREATE TABLE progressive_stage_results (
                    id INTEGER PRIMARY KEY,
                    attempt_id INTEGER NOT NULL REFERENCES progressive_attempts(id),
                    stage_order INTEGER NOT NULL,
                    code_snapshot TEXT NOT NULL,
                    test_results_json TEXT,
                    passed BOOLEAN,
                    submitted_at DATETIME,
                    UNIQUE (attempt_id, stage_order)
                )
                """
            )
            cur.execute(
                "CREATE INDEX ix_progressive_stage_results_attempt_id ON progressive_stage_results (attempt_id)"
            )
            applied.append("progressive_stage_results (table)")

        if not _has_table(cur, "progressive_ai_turns"):
            cur.execute(
                """
                CREATE TABLE progressive_ai_turns (
                    id INTEGER PRIMARY KEY,
                    attempt_id INTEGER NOT NULL REFERENCES progressive_attempts(id),
                    stage_order INTEGER NOT NULL,
                    candidate_prompt TEXT NOT NULL,
                    effective_candidate_instruction TEXT,
                    generated_response TEXT NOT NULL,
                    detector_status VARCHAR,
                    detector_reason_codes TEXT,
                    judge_verdict VARCHAR,
                    judge_reason_codes TEXT,
                    judge_severity VARCHAR,
                    code_after TEXT,
                    created_at DATETIME
                )
                """
            )
            cur.execute(
                "CREATE INDEX ix_progressive_ai_turns_attempt_id ON progressive_ai_turns (attempt_id)"
            )
            applied.append("progressive_ai_turns (table)")

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
