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

from pydantic import BaseModel, Field, field_validator


# ---- Auth ----
#
# No signup schema: accounts are seeded (see app/seed.py) or bulk-uploaded
# by HR (see routers/hr.py's upload endpoint, credential_service.py), not
# self-registered. Login is the only auth endpoint.

class LoginRequest(BaseModel):
    # Deliberately a plain string, not EmailStr: HR/seeded accounts log in
    # with their email, but bulk-uploaded candidates log in with a plain
    # username (their email's local part - not itself a valid email
    # shape), so this field has to accept either. auth.py's login looks
    # this up against both User.email and User.username.
    identifier: str = Field(min_length=1)
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class MeOut(BaseModel):
    """Who's actually behind this token - the frontend calls GET /auth/me
    to resolve this reliably (works whether the session just logged in or
    was restored from a stored token), rather than trusting a value it
    captured client-side at login time, which a page that was already
    signed in when this was added would never have had."""
    email: str
    role: str


# ---- Runtime-editable app settings (HR's Settings page - see
# routers/hr.py's GET/PUT /hr/settings, models.AppSettings). Deliberately
# separate from config.py's env-sourced Settings, which needs a server
# restart to change and holds deployment-level things (secrets, DB URL),
# not business rules HR should be able to self-serve. ----

class AppSettingsOut(BaseModel):
    round1_passing_score: int
    round2_passing_score: int
    round3_passing_score: int
    final_passing_score: int
    reapplication_window_months: int

    class Config:
        from_attributes = True


class AppSettingsUpdate(BaseModel):
    round1_passing_score: int = Field(ge=0, le=100)
    round2_passing_score: int = Field(ge=0, le=100)
    round3_passing_score: int = Field(ge=0, le=100)
    final_passing_score: int = Field(ge=0, le=300)
    reapplication_window_months: int = Field(ge=1)


# ---- Test case rows. Round 1's candidate submissions AND both round
# 1's and round 2's LLM-generated reference use this shape (HR's
# reference review table is unaffected by round 2's candidate-facing
# shape below - only what round 2 candidates *submit* changed, not what
# they're scored against). ----

class TestCaseRow(BaseModel):
    # min_length=1 on the substantive fields: the server must enforce
    # this itself, not just app.js's client-side "Add at least one row"
    # check - a direct API call could otherwise submit blank rows that
    # still burn the candidate's one attempt and an LLM scoring call.
    # preconditions is legitimately optional, so it's exempt.
    title: str = Field(min_length=1)
    preconditions: str = ""
    steps: str = Field(min_length=1)
    expected_result: str = Field(min_length=1)
    priority: Literal["High", "Medium", "Low"] = "Medium"
    type: Literal["Positive", "Negative", "Boundary", "Edge"] = "Positive"


# ---- Scenarios (HR authoring: draft -> published -> moved live for screening) ----

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
    is_live: bool
    time_limit_minutes: int
    published_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ScenarioOut(ScenarioPublicOut):
    """HR-facing shape - includes the reference answer for review/scoring."""
    reference_json: Optional[list[dict]] = None
    # Round 3 only: the auto-generated test-environment reference facts
    # and reference UI screens (see Scenario.environment_json/
    # ui_mockup_json). Not on ScenarioPublicOut either - each is included
    # there separately, as Round3EnvironmentOut/Round3UiMockupOut, shaped
    # for direct candidate display rather than HR's raw-JSON review.
    environment_json: Optional[dict] = None
    ui_mockup_json: Optional[dict] = None


# ---- Submissions / Round 1 ----

class SubmissionCreate(BaseModel):
    content: list[TestCaseRow] = Field(min_length=1)


# ---- Round 2 candidate submission ----
#
# Deliberately NOT TestCaseRow - a debugging investigation isn't a list
# of test cases, it's a list of things checked plus one concluding
# root-cause statement. HR's reference (still TestCaseRow-shaped, see
# above) and the candidate's answer are genuinely different shapes now;
# scoring bridges that (see llm_service.score_round2_submission).

class Round2InvestigationRow(BaseModel):
    area: str = Field(min_length=1)  # what the candidate checked/investigated at this step


class Round2SubmissionCreate(BaseModel):
    investigation: list[Round2InvestigationRow] = Field(min_length=1)
    root_cause: str = Field(min_length=1)


class ScoreOut(BaseModel):
    coverage_score: Optional[int]
    misses_json: list
    final_score: Optional[int]
    feedback_text: Optional[str]
    # Human-override audit trail (see hr.py's PATCH /submissions/{id}/score) -
    # always present, all null/false until HR ever touches this score.
    original_final_score: Optional[int] = None
    overridden_by_hr: bool = False
    override_note: Optional[str] = None
    overridden_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ScoreOverrideRequest(BaseModel):
    """HR correcting or hand-entering a score - either to fix an LLM
    score they disagree with, or to manually score a submission stuck at
    scoring_failed. override_note is required (not just recommended):
    this is the human-accountability record for a number that gates a
    hiring decision, so it should never be silent."""
    final_score: int = Field(ge=0, le=100)
    feedback_text: Optional[str] = None
    override_note: str = Field(min_length=1)


class SubmissionOut(BaseModel):
    """Candidate-facing: their own submission only. Never the reference,
    and never the score - HR reviews results, not the candidate (their
    own request: results are HR's call to share, not automatic)."""
    id: int
    scenario_id: int
    round_number: int
    status: str
    # Shape is round-dependent: round 1 is list[TestCaseRow]-shaped,
    # round 2 is {"investigation": [...], "root_cause": str}, round 3 is
    # always None - there's nothing candidate-authored to store at the
    # submission level, the test cases/turns themselves (see
    # Round3TestCaseOut/Round3TurnOut) are the round 3 submission.
    content: Optional[Any] = None
    started_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SubmissionReportOut(SubmissionOut):
    """HR-facing only (candidate report drill-down): adds the scenario
    (including its reference answer) and the score, for side-by-side
    comparison - neither of which the candidate-facing SubmissionOut
    exposes. test_cases/conversation_turns are populated for round 3
    only, so the report can group turns by test case without a second
    round-trip."""
    scenario: Optional[ScenarioOut] = None
    score: Optional[ScoreOut] = None
    test_cases: Optional[list["Round3TestCaseOut"]] = None
    conversation_turns: Optional[list["Round3TurnOut"]] = None
    # Set when status == "scoring_failed" (see models.RoundStatus) - the
    # error from the failed background scoring attempt, so HR can see
    # why instead of a submission just looking stuck.
    scoring_error: Optional[str] = None
    # Anti-cheating (see models.Submission.tab_switch_events_json /
    # routers/candidate.py's tab-switch guard endpoints) - HR-facing only,
    # same tier as scoring_error above. tab_switch_count is a computed
    # property, not a DB column; the field name below mirrors the ORM
    # attribute name, same convention as ScoreOut.misses_json.
    tab_switch_count: int = 0
    tab_switch_events_json: list[datetime] = Field(default_factory=list)

    # The DB column is nullable (most submissions never trigger this) -
    # coerce None to [] rather than requiring every read site to know
    # that, same shape either way for the frontend.
    @field_validator("tab_switch_events_json", mode="before")
    @classmethod
    def _default_events(cls, v):
        return v or []


class RoundStateOut(BaseModel):
    """What the candidate's round screen needs: the live scenario (if any,
    with no reference answer attached) plus their own submission for it."""
    scenario: Optional[ScenarioPublicOut] = None
    submission: Optional[SubmissionOut] = None


# ---- HR candidate dashboard ----

class CandidateRoundSummary(BaseModel):
    round_number: int
    status: str  # "not_started" | "in_progress" | "submitted" | "scored" | "scoring_failed"
    final_score: Optional[int] = None
    # Surfaced here too (not just the drill-down report) so it's visible
    # on the first screen HR sees - see Submission.tab_switch_count.
    tab_switch_count: int = 0


class CandidateSummaryOut(BaseModel):
    id: int
    email: str
    experience_band: Optional[str]
    rounds: list[CandidateRoundSummary]
    # Populated only for bulk-uploaded candidates (see CandidateAppearance)
    # - None for the 3 seeded accounts, which never went through upload.
    exam_date: Optional[datetime] = None
    aggregate_score: Optional[int] = None  # sum of the 3 rounds' final_score, out of 300 - None until at least one is scored
    reapplied_within_window: bool = False


# ---- Bulk candidate upload (see routers/hr.py's POST /candidates/upload,
# credential_service.py) ----

class BulkUploadRowResult(BaseModel):
    row_number: int
    email: Optional[str] = None
    status: Literal["created", "reset", "error"]
    username: Optional[str] = None
    error: Optional[str] = None


class BulkUploadResult(BaseModel):
    created_count: int
    reset_count: int
    error_count: int
    rows: list[BulkUploadRowResult]


class CandidateBandUpdate(BaseModel):
    experience_band: Literal["0-7", "7+"]


# ---- Candidate appearance history (see routers/hr.py's
# /candidates/{id}/appearances[/...]) ----

class CandidateAppearanceOut(BaseModel):
    id: int
    exam_date: datetime
    is_current: bool
    reapplied_within_window: bool
    created_at: datetime
    aggregate_score: Optional[int] = None

    class Config:
        from_attributes = True


# ---- Cross-round candidate summary (HR's candidate-detail view -
# generated on demand, not persisted, see llm_service.generate_candidate_summary
# and routers/hr.py's /candidates/{id}/summary[/pdf]). Named
# CandidateAssessmentSummaryOut, not CandidateSummaryOut, to avoid
# colliding with the per-round-status shape above. ----

class CandidateRoundComment(BaseModel):
    round_number: int
    comment: str


class CandidateAssessmentSummaryOut(BaseModel):
    candidate_email: str
    experience_band: Optional[str] = None
    round_comments: list[CandidateRoundComment]  # one per round the candidate has actually reached
    final_summary: str  # the closing cross-round verdict paragraph


class CandidateSummaryPdfRequest(BaseModel):
    """The frontend already has the generated round_comments/final_summary
    on screen (from the /summary call above) - sending them back here
    instead of regenerating avoids a second LLM call just to produce the
    PDF."""
    round_comments: list[CandidateRoundComment]
    final_summary: str


# ---- Round 3 (conversational automation: the candidate writes their
# own open-ended, self-titled test cases - see routers/candidate.py's
# round 3 section. Deliberately no fixed UI/API/DB categories: picking
# what to test is itself part of what this round assesses.) ----


class Round1ContextOut(BaseModel):
    """What the candidate wrote in round 1, carried into round 3 so
    they're automating their own test cases, not a fresh scenario."""
    scenario_title: str
    scenario_description: str
    submitted_rows: list[dict]


class Round3EnvironmentOut(BaseModel):
    """Auto-generated, fictional test-environment reference facts (test
    login credentials, a simulated API base URL, ...) shown to the
    candidate alongside the scenario description - see
    llm_service.generate_round3_environment. A loose dict rather than
    fixed fields, since different scenarios legitimately need different
    reference facts (a login flow needs credentials; a reporting feature
    might need a date range instead)."""
    fields: dict[str, str]
    notes: Optional[str] = None


class Round3MockupElement(BaseModel):
    """One element on a reference screen, in display order (order IS the
    layout - no free-text position field for the renderer to interpret).
    Structured on purpose, never raw HTML/CSS - see
    llm_service.generate_round3_ui_mockup and app.js's renderMockupScreens,
    which renders each type through the app's own trusted CSS."""
    type: Literal["label", "input", "button", "link", "text"]
    text: str


class Round3MockupScreen(BaseModel):
    name: str
    elements: list[Round3MockupElement]


class Round3UiMockupOut(BaseModel):
    """Auto-generated, structured reference screens (login page, home
    page, ...) shown to the candidate as a static visual reference for
    the app they're automating - see llm_service.generate_round3_ui_mockup.
    Deliberately includes 1-2 pairs of easy-to-confuse element labels per
    scenario, by design - precision in describing the UI is part of what
    this round assesses."""
    screens: list[Round3MockupScreen]


class Round3ExecutionStep(BaseModel):
    """One action in the execution trace, described in plain English -
    deliberately never code. The candidate is meant to reason from
    observed behavior (what a manual tester watching a screen or reading
    a response would see), not from reading an implementation - showing
    code would let coding fluency substitute for automation-thinking
    skill, which defeats what this round is actually assessing. `detail`
    is an optional concrete data point worth surfacing for this step (a
    field value, a status code, a row) - shown behind a disclosure
    rather than inline, so the transcript stays scannable."""
    description: str
    status: Literal["pass", "fail", "partial"]
    detail: Optional[str] = None


class Round3TurnResponse(BaseModel):
    """The assistant's structured reply for one turn - see
    llm_service.round3_respond and prompts/round3_partial_response.txt.
    No code field on purpose (see Round3ExecutionStep)."""
    response_text: str
    steps: list[Round3ExecutionStep]
    observed_result: str
    status: Literal["pass", "fail", "partial"]


class Round3TestCaseCreate(BaseModel):
    title: Optional[str] = None


class Round3TestCaseOut(BaseModel):
    id: int
    title: Optional[str] = None
    draft_prompt: str
    created_at: datetime
    turn_count: int = 0

    class Config:
        from_attributes = True


class Round3DraftUpdate(BaseModel):
    """PATCH body for autosaving a test case's in-progress, unsent
    message - see candidate.py's PATCH /round/3/test-case/{id}/draft."""
    draft_prompt: str


class Round3TurnCreate(BaseModel):
    test_case_id: int
    candidate_prompt: str


class Round3TurnOut(BaseModel):
    id: int
    test_case_id: int
    turn_number: int
    candidate_prompt: str
    model_response: Round3TurnResponse
    created_at: datetime

    class Config:
        from_attributes = True
        # "model_response" collides with pydantic's own reserved
        # "model_" prefix (model_dump, model_validate, ...) - it's just
        # a field name here, not a real conflict, so silence the warning
        # rather than rename a column that mirrors the DB schema.
        protected_namespaces = ()


class Round3StateOut(BaseModel):
    """Everything the round 3 candidate screen needs in one call: the
    auto-generated test environment, their own round 1 context, their
    test cases (candidate-created, open-ended), and the full transcript
    (flat - the frontend groups turns by test_case_id)."""
    scenario: ScenarioPublicOut
    submission: SubmissionOut
    round1_context: Round1ContextOut
    environment: Optional[Round3EnvironmentOut] = None
    ui_mockup: Optional[Round3UiMockupOut] = None
    test_cases: list[Round3TestCaseOut]
    turns: list[Round3TurnOut]


# ---- HR screening history (per-scenario performance, across everyone
# who's ever attempted it) ----

class MissPattern(BaseModel):
    """One recurring gap and how many scored submissions hit it. Grouped
    by exact text match on the scoring LLM's `misses` strings - see
    scenario_history() in routers/hr.py for why that's a deliberate
    choice over an LLM-summarized version."""
    text: str
    count: int


class ScenarioHistoryOut(BaseModel):
    scenario_id: int
    round_number: int
    experience_band: str
    title: str
    is_live: bool
    first_used_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    total_attempted: int
    scored_count: int
    pending_count: int
    cleared_count: int
    not_cleared_count: int
    cleared_pct: Optional[float] = None  # None until at least one submission is scored
    passing_score: int
    common_misses: list[MissPattern]
