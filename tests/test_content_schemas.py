"""
Submission.content - a candidate's answers - checked against its round's
schema on every write (content_schemas.py / models.Submission._check_content)
and by the read-only audit (app/audit_content.py). Shapes below mirror the
records actually stored in the real database.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest

from app.content_schemas import content_problem
from app.models import Submission

TURN = {"turn_number": 1, "candidate_prompt": "encode step 1", "response_kind": "code_edit", "response_message": "Done.",
        "code_after": "x = 1", "planted_flaw": "assessor-only"}
VALID = {
    1: [{"title": "Login", "preconditions": "", "steps": "1. log in", "test_data": "u/p", "expected_result": "ok",
         "priority": "High", "type": "Positive"}, {"title": "", "steps": "", "expected_result": ""}],  # a draft row may be empty
    2: {"mode": "ai_test_automation", "language": "python", "refinements": [{"row_index": 0, "note": "n", "created_at": "t"}],
        "selected": [{"index": 0, "title": "Login", "code": "x = 1", "turns": [TURN], "code_edits": [{"seq": 1, "code": "x", "created_at": "t"}],
                      "last_run": {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "infra_error": False}, "validation": "",
                      "refinements": ["note"]}]},
    3: {"language": "python", "draft_prompt": ""},
    4: {"investigation": [{"area": "Checked the logs"}], "root_cause": "Timeout"},
}
INVALID = {
    "round 1 stored as an object": (1, {"title": "x"}),
    "round 1 row with a non-text field": (1, [{"title": ["x"]}]),
    "round 2 selected is not a list": (2, {"mode": "ai_test_automation", "selected": "tc0"}),
    "round 2 test case without an index": (2, {"mode": "ai_test_automation", "selected": [{"title": "x"}]}),
    "round 2 turn missing its reply": (2, {"mode": "ai_test_automation", "selected": [{"index": 0, "turns": [{"candidate_prompt": "x"}]}]}),
    "round 2 run with a text exit code": (2, {"mode": "ai_test_automation", "selected": [{"index": 0, "last_run": {"exit_code": "zero"}}]}),
    "round 3 draft that is not text": (3, {"draft_prompt": 5}),
    "round 4 investigation as text": (4, {"investigation": "logs", "root_cause": "x"}),
}


@pytest.mark.parametrize("round_number", sorted(VALID))
def test_real_shaped_answers_are_accepted(round_number):
    assert content_problem(round_number, VALID[round_number]) is None
    Submission(round_number=round_number, content=VALID[round_number])  # the write check lets it through


@pytest.mark.parametrize("case", sorted(INVALID))
def test_wrong_shapes_are_refused_at_write_time(case):
    round_number, content = INVALID[case]
    assert content_problem(round_number, content)
    with pytest.raises(ValueError, match="Refusing to store a malformed answer"):
        Submission(round_number=round_number, content=content)


@pytest.mark.parametrize("round_number,content", [(1, None), (2, None), (2, {"legacy": True}), (3, {}), (4, {})])
def test_empty_or_legacy_content_is_accepted(round_number, content):
    assert content_problem(round_number, content) is None


def test_audit_lists_records_that_dont_fit(tmp_path, monkeypatch):
    from app import audit_content
    from app.config import settings
    db = tmp_path / "audit.db"
    con = sqlite3.connect(db)
    con.execute("create table submissions (id integer primary key, round_number integer, content text)")
    con.executemany("insert into submissions values (?, ?, ?)", [(1, 4, json.dumps(VALID[4])), (2, 4, json.dumps({"investigation": "x"})),
                                                                 (3, 1, None)])
    con.commit()
    con.close()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db}")
    checked, problems = audit_content.audit()
    assert checked == {4: 2, 1: 1}
    assert len(problems) == 1 and problems[0].startswith("submission 2: round 4 content: investigation")
