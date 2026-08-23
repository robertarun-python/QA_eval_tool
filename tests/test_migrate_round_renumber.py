"""
Migration test for migrate_round_renumber.py (see that script's
docstring, and the design spec's Testing section, which called this test
out as planned but not yet written). Uses a real on-disk SQLite file (not
the app's usual in-memory test DB) since the migration script talks to
the DB via the stdlib sqlite3 module against a filesystem path
(database.sqlite_db_path()), not through SQLAlchemy's engine.

Covers two things:
- The original behavior: round_number=3 rows move to 4, round 1/2 rows
  are untouched, running it twice is a no-op the second time.
- Fix 6 (final whole-branch review): the guard against reclassifying
  real NEW round-3 (AI-prompted-coding) data - identified by having
  associated round3_turns rows - if this script is ever re-run after
  that round has real scenarios/submissions.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import User, Scenario, Submission, Round3Turn, Role, ExperienceBand, ScenarioStatus, RoundStatus
from app import migrate_round_renumber


def _make_db(tmp_path):
    """A real on-disk SQLite file, schema built via the app's own
    Base.metadata.create_all() - this already reflects TODAY's
    post-renumbering target schema (round4_test_cases,
    app_settings.round3_passing_score AND round4_passing_score,
    round3_turns all already exist), so the migration's schema-level
    steps (table/column rename) are no-ops here; only the row-level
    UPDATE guard logic under test actually does anything."""
    db_path = tmp_path / "renumber_test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return str(db_path), Session, engine


def test_old_round3_data_moves_to_round4_and_1_2_are_untouched_and_rerun_is_a_noop(tmp_path, monkeypatch):
    db_path, Session, engine = _make_db(tmp_path)
    session = Session()
    hr = User(email="hr@example.com", password_hash="x", role=Role.hr)
    session.add(hr)
    session.commit()

    r1 = Scenario(round_number=1, title="R1", description="d", experience_band=ExperienceBand.both, created_by=hr.id, status=ScenarioStatus.published)
    r2 = Scenario(round_number=2, title="R2", description="d", experience_band=ExperienceBand.both, created_by=hr.id, status=ScenarioStatus.published)
    old_r3 = Scenario(round_number=3, title="Old automation", description="d", experience_band=ExperienceBand.both, created_by=hr.id, status=ScenarioStatus.published)
    session.add_all([r1, r2, old_r3])
    session.commit()

    cand = User(email="cand@example.com", password_hash="x", role=Role.candidate, experience_band=ExperienceBand.both)
    session.add(cand)
    session.commit()

    sub1 = Submission(user_id=cand.id, scenario_id=r1.id, round_number=1, status=RoundStatus.submitted)
    sub2 = Submission(user_id=cand.id, scenario_id=r2.id, round_number=2, status=RoundStatus.submitted)
    # Old-style round-3 (prompt-driven automation) submission - no
    # round3_turns rows, since that table didn't exist when this kind of
    # submission was created.
    old_sub3 = Submission(user_id=cand.id, scenario_id=old_r3.id, round_number=3, status=RoundStatus.submitted)
    session.add_all([sub1, sub2, old_sub3])
    session.commit()
    ids = {"r1": r1.id, "r2": r2.id, "old_r3": old_r3.id, "sub1": sub1.id, "sub2": sub2.id, "old_sub3": old_sub3.id}
    session.close()

    monkeypatch.setattr(migrate_round_renumber, "sqlite_db_path", lambda: db_path)
    applied = migrate_round_renumber.migrate()
    assert any("scenarios" in a for a in applied)
    assert any("submissions" in a for a in applied)

    check = Session()
    assert check.get(Scenario, ids["r1"]).round_number == 1
    assert check.get(Scenario, ids["r2"]).round_number == 2
    assert check.get(Scenario, ids["old_r3"]).round_number == 4
    assert check.get(Submission, ids["sub1"]).round_number == 1
    assert check.get(Submission, ids["sub2"]).round_number == 2
    assert check.get(Submission, ids["old_sub3"]).round_number == 4
    check.close()

    # Idempotent: a second run finds nothing left at round_number=3 to move.
    applied_again = migrate_round_renumber.migrate()
    assert not any("round_number" in a for a in applied_again)


def test_rerun_does_not_reclassify_real_new_round3_data_with_round3_turns(tmp_path, monkeypatch):
    """The guard this fix adds: a submission (and its scenario) that has
    real round3_turns rows is genuinely the NEW AI-prompted-coding round,
    not old automation data - must never be swept into round_number=4."""
    db_path, Session, engine = _make_db(tmp_path)
    session = Session()
    hr = User(email="hr@example.com", password_hash="x", role=Role.hr)
    session.add(hr)
    session.commit()

    new_r3 = Scenario(round_number=3, title="Add two numbers", description="d", experience_band=ExperienceBand.both, created_by=hr.id, status=ScenarioStatus.published)
    session.add(new_r3)
    session.commit()

    cand = User(email="cand@example.com", password_hash="x", role=Role.candidate, experience_band=ExperienceBand.both)
    session.add(cand)
    session.commit()

    new_sub3 = Submission(user_id=cand.id, scenario_id=new_r3.id, round_number=3, status=RoundStatus.submitted)
    session.add(new_sub3)
    session.commit()

    turn = Round3Turn(
        submission_id=new_sub3.id, turn_number=1, candidate_prompt="read two ints and sum them",
        language="python", response_kind="code_edit", response_message="ok",
        code_after="a=int(input());b=int(input());print(a+b)",
    )
    session.add(turn)
    session.commit()
    ids = {"new_r3": new_r3.id, "new_sub3": new_sub3.id}
    session.close()

    monkeypatch.setattr(migrate_round_renumber, "sqlite_db_path", lambda: db_path)
    applied = migrate_round_renumber.migrate()
    # Nothing eligible to move - the only round_number=3 rows are
    # protected by their round3_turns association.
    assert not any("round_number" in a for a in applied)

    check = Session()
    assert check.get(Scenario, ids["new_r3"]).round_number == 3
    assert check.get(Submission, ids["new_sub3"]).round_number == 3
    check.close()


def test_mixed_old_and_new_round3_data_only_migrates_the_old_one(tmp_path, monkeypatch):
    """The realistic re-run scenario: some genuinely-old round-3-automation
    data still needs renumbering (e.g. this script wasn't run yet on this
    DB) alongside real new-round-3-coding data that must stay put."""
    db_path, Session, engine = _make_db(tmp_path)
    session = Session()
    hr = User(email="hr@example.com", password_hash="x", role=Role.hr)
    session.add(hr)
    session.commit()

    old_r3 = Scenario(round_number=3, title="Old automation", description="d", experience_band=ExperienceBand.both, created_by=hr.id, status=ScenarioStatus.published)
    new_r3 = Scenario(round_number=3, title="Add two numbers", description="d", experience_band=ExperienceBand.both, created_by=hr.id, status=ScenarioStatus.published)
    session.add_all([old_r3, new_r3])
    session.commit()

    cand = User(email="cand@example.com", password_hash="x", role=Role.candidate, experience_band=ExperienceBand.both)
    session.add(cand)
    session.commit()

    old_sub3 = Submission(user_id=cand.id, scenario_id=old_r3.id, round_number=3, status=RoundStatus.submitted)
    new_sub3 = Submission(user_id=cand.id, scenario_id=new_r3.id, round_number=3, status=RoundStatus.submitted)
    session.add_all([old_sub3, new_sub3])
    session.commit()

    turn = Round3Turn(
        submission_id=new_sub3.id, turn_number=1, candidate_prompt="x",
        language="python", response_kind="code_edit", response_message="ok", code_after="print(1)",
    )
    session.add(turn)
    session.commit()
    ids = {"old_r3": old_r3.id, "new_r3": new_r3.id, "old_sub3": old_sub3.id, "new_sub3": new_sub3.id}
    session.close()

    monkeypatch.setattr(migrate_round_renumber, "sqlite_db_path", lambda: db_path)
    migrate_round_renumber.migrate()

    check = Session()
    assert check.get(Scenario, ids["old_r3"]).round_number == 4
    assert check.get(Scenario, ids["new_r3"]).round_number == 3
    assert check.get(Submission, ids["old_sub3"]).round_number == 4
    assert check.get(Submission, ids["new_sub3"]).round_number == 3
    check.close()
