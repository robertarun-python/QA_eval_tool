"""app.backup_db - snapshots whatever database settings point at (a temp one here)."""
import sqlite3

from app import backup_db
from app.config import settings


def test_backup_is_a_full_copy_next_to_the_source(tmp_path, monkeypatch):
    source = tmp_path / "qa_eval.db"
    con = sqlite3.connect(source)
    con.execute("create table t (x)")
    con.execute("insert into t values (42)")
    con.commit()
    con.close()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{source}")

    target = backup_db.backup("before reset/../x")
    assert target.parent == tmp_path and target.name.startswith("qa_eval_before_reset_x_")
    assert sqlite3.connect(target).execute("select x from t").fetchall() == [(42,)]
