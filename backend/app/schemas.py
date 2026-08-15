"""
Pydantic schemas: the shapes of data going in/out of the API.

Keeping these separate from the SQLAlchemy models (models.py) is a
deliberate pattern, not duplication for its own sake - it means you can
accept/return a slightly different shape than what's stored (e.g. never
accidentally leak password_hash in an API response) without touching
the DB layer.
"""
from datetime import datetime
from typing import Optional, Any, Literal

from pydantic import BaseModel, EmailStr


# ---- Auth ----
#
# No signup schema: accounts are seeded (see app/seed.py), not
# self-registered. Login is the only auth endpoint.

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


# ---- Test case rows (Round 1 candidate submissions AND the LLM-generated
# reference use this exact shape, so scoring compares like-for-like) ----

class TestCaseRow(BaseModel):
    title: str
    preconditions: str = ""
    steps: str
    expected_result: str
    priority: Literal["High", "Medium", "Low"] = "Medium"
    type: Literal["Positive", "Negative", "Boundary", "Edge"] = "Positive"


# ---- Scenarios (HR authoring: draft -> published -> archived) ----

class ScenarioCreate(BaseModel):
    round_number: int
    title: str
    description: str
    experience_band: str  # "0-7" | "7+" - HR picks one explicitly, no "both"
    time_limit_minutes: int = 30
    config_json: dict[str, Any] = {}


class ScenarioUpdate(BaseModel):
    """Partial edit of a draft scenario - only settable while status == draft."""
    title: Optional[str] = None
    description: Optional[str] = None
    time_limit_minutes: Optional[int] = None
    reference_json: Optional[list[TestCaseRow]] = None


class ScenarioPublicOut(BaseModel):
    """Candidate-facing scenario shape - deliberately excludes
    reference_json. That's the answer key; it must never reach a
    candidate's browser (dev tools / network tab would expose it)."""
    id: int
    round_number: int
    title: str
    description: str
    experience_band: str
    status: str
    time_limit_minutes: int
    published_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ScenarioOut(ScenarioPublicOut):
    """HR-facing shape - includes the reference answer for review/scoring."""
    reference_json: Optional[list[dict]] = None


# ---- Submissions / Round 1 ----

class SubmissionCreate(BaseModel):
    content: list[TestCaseRow]


class ScoreOut(BaseModel):
    coverage_score: Optional[int]
    misses_json: list
    final_score: Optional[int]
    feedback_text: Optional[str]

    class Config:
        from_attributes = True


class SubmissionOut(BaseModel):
    """Candidate-facing: their own submission only, never the reference."""
    id: int
    scenario_id: int
    round_number: int
    status: str
    content: Optional[list[dict]] = None
    started_at: Optional[datetime] = None
    created_at: datetime
    score: Optional[ScoreOut] = None

    class Config:
        from_attributes = True


class SubmissionReportOut(SubmissionOut):
    """HR-facing only (candidate report drill-down): adds the scenario,
    including its reference answer, for side-by-side comparison."""
    scenario: Optional[ScenarioOut] = None


class RoundStateOut(BaseModel):
    """What the candidate's round screen needs: the live scenario (if any,
    with no reference answer attached) plus their own submission for it."""
    scenario: Optional[ScenarioPublicOut] = None
    submission: Optional[SubmissionOut] = None


# ---- HR candidate dashboard ----

class CandidateRoundSummary(BaseModel):
    round_number: int
    status: str  # "not_started" | "in_progress" | "submitted" | "scored"
    final_score: Optional[int] = None


class CandidateSummaryOut(BaseModel):
    id: int
    email: str
    experience_band: Optional[str]
    rounds: list[CandidateRoundSummary]
