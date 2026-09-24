"""
Checks every stored Submission.content against its round's schema
(content_schemas.py) and lists the records that don't fit. Read-only.

Run from backend/: python -m app.audit_content
"""
import json
import sqlite3
from collections import Counter

from .content_schemas import content_problem
from .database import sqlite_db_path


def audit() -> tuple[Counter, list[str]]:
    con = sqlite3.connect(f"file:{sqlite_db_path()}?mode=ro", uri=True)
    checked, problems = Counter(), []
    try:
        for sid, rnd, raw in con.execute("select id, round_number, content from submissions order by id"):
            checked[rnd] += 1
            problem = content_problem(rnd, json.loads(raw) if raw else None)
            if problem:
                problems.append(f"submission {sid}: {problem}")
    finally:
        con.close()
    return checked, problems


if __name__ == "__main__":
    checked, problems = audit()
    print("Checked:", ", ".join(f"round {r}: {n}" for r, n in sorted(checked.items())))
    print("\n".join(problems) if problems else "Every stored answer fits its round's schema.")
