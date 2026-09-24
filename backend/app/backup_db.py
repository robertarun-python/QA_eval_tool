"""
Snapshot the real database before changing it by hand (a migration, a
candidate reset, a data fix):

    cd backend && python -m app.backup_db before_candidate4_reset

Writes backend/qa_eval_<label>_<timestamp>.db using SQLite's own backup
API, so it's a consistent copy even while the server is running and
writing (a plain file copy mid-write can be torn). Restore by stopping
the server and copying the snapshot back over qa_eval.db.
"""
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from .database import sqlite_db_path


def backup(label: str = "manual") -> Path:
    source = Path(sqlite_db_path())
    if not source.exists():
        raise SystemExit(f"No database at {source}")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", label).strip("_") or "manual"
    target = source.with_name(f"{source.stem}_{safe}_{datetime.now():%Y%m%d_%H%M%S}.db")
    src, dst = sqlite3.connect(f"file:{source}?mode=ro", uri=True), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return target


if __name__ == "__main__":
    print(backup(sys.argv[1] if len(sys.argv) > 1 else "manual"))
