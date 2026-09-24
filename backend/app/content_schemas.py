"""
The shape of Submission.content - a candidate's answers - per round and mode.

Content is a JSON column, and until these schemas it had no shape anywhere:
61 reads in routers/candidate.py alone trusted whatever an earlier write
had stored, so a write that put the wrong type in (a string where a list was
expected, a missing index) only failed later, in a candidate's session or
in scoring. Submission.content is now checked against these on every write
(models.Submission._check_content), and `python -m app.audit_content`
checks every stored record.

Derived from the data actually stored (every round and mode in the real
database was surveyed), not from assumptions:
  - unknown extra keys are kept - legacy fields (Round 1's priority/type),
    assessor-only ones (planted_flaw, unrequested_checks) and anything new;
  - every KNOWN key is type-checked;
  - keys older records lack have defaults (test_data, turns, ...).
Validation never changes what is stored - it only rejects a wrong shape.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


# ---- Round 1: manual test cases - a list of rows (drafts may be partly empty) ----
class Round1Row(_Open):
    title: str = ""
    preconditions: str = ""
    steps: str = ""
    test_data: str = ""
    expected_result: str = ""
    priority: Optional[str] = None  # legacy
    type: Optional[str] = None      # legacy


# ---- Round 2: AI-assisted test automation (config_json mode "ai_test_automation") ----
class RunRecord(_Open):
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    infra_error: bool = False
    duration_ms: Optional[int] = None
    ran_at: Optional[str] = None


class AssistantTurn(_Open):
    turn_number: Optional[int] = None
    candidate_prompt: str
    response_kind: str
    response_message: str
    code_after: Optional[str] = None


class CodeEdit(_Open):
    seq: int
    code: str
    created_at: Optional[str] = None


class AutomatedTestCase(_Open):
    index: int
    title: str = ""
    preconditions: str = ""
    steps: str = ""
    test_data: str = ""
    expected_result: str = ""
    code: str = ""
    turns: list[AssistantTurn] = []
    code_edits: list[CodeEdit] = []
    last_run: Optional[RunRecord] = None
    validation: str = ""
    refinements: list[str] = []


class Refinement(_Open):
    row_index: int
    note: str
    created_at: Optional[str] = None


class Round2AutoContent(_Open):
    mode: Literal["ai_test_automation"]
    language: Optional[str] = None
    selected: list[AutomatedTestCase] = []
    refinements: list[Refinement] = []


# ---- Round 2: focused automation pilot (retired mode, still readable) ----
class Round2PilotContent(_Open):
    mode: Literal["pilot_automation"]
    language: Optional[str] = None
    code: str = ""
    turns: list[AssistantTurn] = []
    last_run: Optional[RunRecord] = None
    clarification_question: Optional[str] = None
    clarification_response: Optional[str] = None


# ---- Round 3: AI-prompted coding (turns and runs live in their own tables) ----
class Round3Content(_Open):
    language: Optional[str] = None
    draft_prompt: str = ""


# ---- Round 4: debugging investigation ----
class InvestigationRow(_Open):
    area: str = ""


class Round4Content(_Open):
    investigation: list[InvestigationRow] = []
    root_cause: str = ""


_ROUND1 = TypeAdapter(list[Round1Row])


def content_problem(round_number: Optional[int], content) -> Optional[str]:
    """None when `content` fits its round's schema, else a short description
    of what's wrong. Empty content (not written yet) always fits; so does a
    round-2 dict with no known mode (the retired conversational mode never
    stored content in this column)."""
    if content is None or round_number is None:
        return None
    try:
        if round_number == 1:
            _ROUND1.validate_python(content)
        elif round_number == 2:
            if not isinstance(content, dict):
                return f"round 2 content must be an object, got {type(content).__name__}"
            mode = content.get("mode")
            if mode == "ai_test_automation":
                Round2AutoContent.model_validate(content)
            elif mode == "pilot_automation":
                Round2PilotContent.model_validate(content)
        elif round_number == 3:
            Round3Content.model_validate(content)
        elif round_number == 4:
            Round4Content.model_validate(content)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "content"
        return f"round {round_number} content: {where}: {first['msg']} ({e.error_count()} problem{'s' if e.error_count() > 1 else ''})"
    return None
