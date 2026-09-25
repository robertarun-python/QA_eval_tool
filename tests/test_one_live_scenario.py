"""
At most one live scenario per round and band - candidates are served "the"
live one, so the database refuses a second (the unique partial index below
models.Scenario), and migrate_one_live_scenario.py adds that index to an
existing database without guessing which of two live scenarios to keep.
"""
import sqlite3

import pytest
from sqlalchemy.exc import IntegrityError

from app import database as database_module
from app.config import settings
from app.migrate_one_live_scenario import INDEX_NAME, MultipleLiveScenarios, migrate
from app.models import ExperienceBand, Scenario, User

from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login, _publish_scenario


def _add(db, round_number=1, band=ExperienceBand.junior, is_live=False, title="S"):
    hr_id = db.query(User).filter(User.email == HR_EMAIL).one().id
    db.add(Scenario(round_number=round_number, title=title, description="d", experience_band=band, is_live=is_live, created_by=hr_id))
    db.commit()


def test_the_database_refuses_a_second_live_scenario_for_the_same_round_and_band(client):
    db = database_module.SessionLocal()
    try:
        _add(db, is_live=True)
        with pytest.raises(IntegrityError):
            _add(db, is_live=True)
    finally:
        db.rollback()
        db.close()


def test_other_bands_rounds_and_non_live_scenarios_are_unaffected(client):
    db = database_module.SessionLocal()
    try:
        _add(db, is_live=True)
        _add(db, band=ExperienceBand.senior, is_live=True)
        _add(db, round_number=2, is_live=True)
        _add(db)
        _add(db)
        assert db.query(Scenario).filter(Scenario.is_live.is_(True)).count() == 3
    finally:
        db.close()


def test_making_another_scenario_live_still_swaps_them(client, monkeypatch):
    token = _login(client, HR_EMAIL, HR_PASSWORD)
    first = _publish_scenario(client, token, monkeypatch, title="First")  # the first one goes live
    second = _publish_scenario(client, token, monkeypatch, title="Second")
    res = client.post(f"/hr/scenarios/{second['id']}/move-to-screening", cookies=_auth(token))
    assert res.status_code == 200, res.text
    db = database_module.SessionLocal()
    try:
        assert db.get(Scenario, second["id"]).is_live
        assert not db.get(Scenario, first["id"]).is_live
    finally:
        db.close()


# ---- the migration, against a throwaway sqlite file ----

def _old_db(tmp_path, monkeypatch, live_rows):
    db_path = tmp_path / "throwaway.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE scenarios (id INTEGER PRIMARY KEY, round_number INTEGER, experience_band TEXT, "
                "title TEXT, is_live BOOLEAN NOT NULL)")
    con.executemany("INSERT INTO scenarios (round_number, experience_band, title, is_live) VALUES (?, ?, ?, ?)", live_rows)
    con.commit()
    con.close()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")
    return db_path


def _has_index(db_path):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (INDEX_NAME,)).fetchone() is not None
    finally:
        con.close()


def test_migration_adds_the_index_and_is_idempotent(tmp_path, monkeypatch):
    db_path = _old_db(tmp_path, monkeypatch, [(1, "junior", "A", 1), (1, "junior", "B", 0), (1, "senior", "C", 1)])
    assert migrate() is True
    assert _has_index(db_path)
    assert migrate() is False
    con = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE scenarios SET is_live = 1 WHERE title = 'B'")
    con.close()


def test_migration_changes_nothing_when_two_are_already_live(tmp_path, monkeypatch):
    db_path = _old_db(tmp_path, monkeypatch, [(1, "junior", "Credit Card", 1), (1, "junior", "Library", 1)])
    with pytest.raises(MultipleLiveScenarios) as err:
        migrate()
    assert "'Credit Card'" in str(err.value) and "'Library'" in str(err.value)
    assert not _has_index(db_path)
