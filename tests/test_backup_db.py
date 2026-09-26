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


def test_startup_backups_are_pruned_to_the_newest(tmp_path, monkeypatch):
    source = tmp_path / "qa_eval.db"
    sqlite3.connect(source).close()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{source}")
    for n in range(12):
        (tmp_path / f"qa_eval_startup_20260926_0000{n:02d}.db").write_bytes(b"x")
    (tmp_path / "qa_eval_before_reset_20260101_000000.db").write_bytes(b"x")  # other labels are never touched
    removed = backup_db.prune("startup", keep=10)
    assert len(removed) == 2 and all("00000" in p.name or "000001" in p.name for p in removed)
    assert len(list(tmp_path.glob("qa_eval_startup_*.db"))) == 10
    assert (tmp_path / "qa_eval_before_reset_20260101_000000.db").exists()
