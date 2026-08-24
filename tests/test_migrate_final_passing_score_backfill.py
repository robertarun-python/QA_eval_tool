"""
migrate_final_passing_score_backfill.py - backfills app_settings.
final_passing_score from the stale 3-round-era default (210) to the
corrected 4-round default (280) on an EXISTING row, which a plain
SQLAlchemy column default (see models.py) never touches. Runs against a
throwaway sqlite file (not the app's real DB or the in-memory test DB
the `client` fixture uses), monkeypatching settings.database_url so
sqlite_db_path() resolves to it - the same pattern every other
migrate_*.py script would need, since these are standalone scripts run
via `python -m app.migrate_...`, not part of the FastAPI app itself.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.config import settings
from app.migrate_final_passing_score_backfill import migrate


def _make_db(tmp_path, final_passing_score):
    db_path = tmp_path / "throwaway.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY, final_passing_score INTEGER NOT NULL)")
    con.execute("INSERT INTO app_settings (id, final_passing_score) VALUES (1, ?)", (final_passing_score,))
    con.commit()
    con.close()
    return db_path


def test_backfills_a_row_still_at_the_stale_default(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path, final_passing_score=210)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")

    applied = migrate()

    assert len(applied) == 1
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT final_passing_score FROM app_settings WHERE id=1").fetchone()[0] == 280
    con.close()


def test_leaves_a_row_hr_already_customized_untouched(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path, final_passing_score=250)  # HR's own deliberate value, not the stale default
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")

    applied = migrate()

    assert applied == []
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT final_passing_score FROM app_settings WHERE id=1").fetchone()[0] == 250
    con.close()


def test_idempotent_on_a_row_already_at_the_corrected_value(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path, final_passing_score=280)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")

    assert migrate() == []
    assert migrate() == []
