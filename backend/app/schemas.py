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

from pydantic import BaseModel, Field, field_validator, model_validator


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


class LoginResponse(BaseModel):
    """The JWT itself now travels only as an httpOnly cookie (see
    routers/auth.py's Set-Cookie on /auth/login), never in this JSON body -
    returning it here too would let any injected script just read it off
    the fetch response, defeating the point of httpOnly. `role` is still
    useful in the body so the frontend can route to the right view
    immediately after login without a second round trip to /auth/me."""
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
    round4_passing_score: int
    final_passing_score: int
    reapplication_window_months: int
    # Read-only here - see config.py's round4_default_assistance_pct.
    # Not part of AppSettingsUpdate below: it's an env-sourced,
    # deployment-level fallback, not something HR edits through this
    # form. Exposed purely so app.js's round4 settings card can read the
    # real server default instead of hardcoding its own separate copy.
    round4_default_assistance_pct: int

    class Config:
        from_attributes = True


class AppSettingsUpdate(BaseModel):
    round1_passing_score: int = Field(ge=0, le=100)
    round2_passing_score: int = Field(ge=0, le=100)
    round3_passing_score: int = Field(ge=0, le=100)
    round4_passing_score: int = Field(ge=0, le=100)
    final_passing_score: int = Field(ge=0, le=400)
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
    # Any, not list[TestCaseRow]: same reason as ScenarioOut.reference_json
    # above - list[dict] for rounds 1/2, {"test_cases": [...],
    # "expected_approach": "..."} for round 3. Pydantic can no longer
    # shape-check this on the wire, so hr.py's update_scenario handler
    # does the round-aware validation itself before writing it.
    reference_json: Optional[Any] = None


class ScenarioTimeLimitUpdate(BaseModel):
    """See hr.py's PATCH /scenarios/{id}/time-limit - unlike everything in
    ScenarioUpdate above, this is allowed regardless of draft/published
    status, so it has its own narrow endpoint rather than going through
    _get_draft_scenario_or_404."""
    time_limit_minutes: int = Field(ge=1)


class Round4ConfigUpdate(BaseModel):
    """See hr.py's PATCH /scenarios/{id}/round4-config - the one round4-
    specific tunable exposed to HR: how often the simulated assistant
    gets things right per turn (Scenario.config_json["assistance_pct"],
    see llm_service.DEFAULT_ROUND4_CONFIG). Bounded away from the
    extremes - 0% or 100% both defeat the exercise, since an assistant
    that's always wrong or always right gives the candidate nothing
    real to verify."""
    assistance_pct: int = Field(ge=10, le=95)


class Round4InstructionsUpdate(BaseModel):
    """See hr.py's PATCH /scenarios/{id}/round4-instructions - editing
    title/description on a round4 scenario regardless of status, same
    reasoning and blocking as Round4ConfigUpdate above. Round 1/2 keep
    title/description as draft-only edits (see ScenarioUpdate) because
    they gate a fixed reference answer that's meaningful to review before
    publishing; round 4 has no such reference, so there's no equivalent
    reason to restrict this to drafts."""
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)


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
    reference_json: Optional[Any] = None  # list[dict] for rounds 1/2, {"test_cases": [...], "expected_approach": "..."} for round 3
    # Round 4 only: the auto-generated test-environment reference facts
    # and reference UI screens (see Scenario.environment_json/
    # ui_mockup_json). Not on ScenarioPublicOut either - each is included
    # there separately, as Round4EnvironmentOut/Round4UiMockupOut, shaped
    # for direct candidate display rather than HR's raw-JSON review.
    environment_json: Optional[dict] = None
    ui_mockup_json: Optional[dict] = None
    # Round 4 only in practice (round 1/2 scenarios never set anything
    # here) - see Round4ConfigUpdate. HR-facing so the assistant-accuracy
    # editor can show the current value; candidates never see this.
    config_json: dict[str, Any] = {}


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


# ---- Round timeout auto-close / in-progress autosave ----
#
# Deliberately permissive, unlike SubmissionCreate/Round2SubmissionCreate
# above: those exist to stop a candidate who still has time from wasting
# their one submit on empty/incomplete work. Both /expire (once the
# timer has actually hit zero) and /draft (periodic autosave while a
# round is still genuinely in progress - the rounds 1/2 equivalent of
# round 4's PATCH /round/4/test-case/{id}/draft) need the opposite rule:
# whatever's there, even nothing, must be acceptable. Shared by both
# rather than two near-identical schemas - see candidate.py's POST
# /round/{n}/expire and PATCH /round/{n}/draft.

class ExpireRoundPayload(BaseModel):
    content: list[dict] = []          # round 1 rows, as collected client-side
    investigation: list[dict] = []    # round 2
    root_cause: str = ""              # round 2


class ScoreOut(BaseModel):
    coverage_score: Optional[int]
    misses_json: list
    # Round 1 only - see models.Score.concept_coverage_json. Empty list
    # for rounds 2/4.
    concept_coverage_json: list = Field(default_factory=list)
    # Round 3 only - see models.Score.test_results_json. Empty list for
    # other rounds.
    test_results_json: list = Field(default_factory=list)
    final_score: Optional[int]
    feedback_text: Optional[str]
    # Human-override audit trail (see hr.py's PATCH /submissions/{id}/score) -
    # always present, all null/false until HR ever touches this score.
    original_final_score: Optional[int] = None
    overridden_by_hr: bool = False
    override_note: Optional[str] = None
    overridden_at: Optional[datetime] = None
    # Provenance (see models.Score) - which model/prompt-version produced
    # this score. None for scores predating this column or entered purely
    # by hand (see the scoring_failed + manual-override path).
    scoring_model: Optional[str] = None
    scoring_prompt_file: Optional[str] = None
    scoring_prompt_hash: Optional[str] = None
    scored_at: Optional[datetime] = None

    class Config:
        from_attributes = True

    # concept_coverage_json is a newly-added column (see
    # migrate_concept_coverage.py) - an ALTER TABLE backfills existing
    # rows with NULL, not [], since the SQLAlchemy column default only
    # applies to new INSERTs. Same coercion as SubmissionReportOut's
    # tab_switch_events_json below, for the same reason.
    @field_validator("concept_coverage_json", mode="before")
    @classmethod
    def _default_concept_coverage(cls, v):
        return v or []

    # test_results_json is a newly-added column (see
    # migrate_round3_test_results.py) - same NULL-on-existing-rows
    # backfill reasoning as concept_coverage_json above.
    @field_validator("test_results_json", mode="before")
    @classmethod
    def _default_test_results(cls, v):
        return v or []


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
    # round 2 is {"investigation": [...], "root_cause": str}, round 4 is
    # always None - there's nothing candidate-authored to store at the
    # submission level, the test cases/turns themselves (see
    # Round4TestCaseOut/Round4TurnOut) are the round 4 submission.
    content: Optional[Any] = None
    started_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SubmissionReportOut(SubmissionOut):
    """HR-facing only (candidate report drill-down): adds the scenario
    (including its reference answer) and the score, for side-by-side
    comparison - neither of which the candidate-facing SubmissionOut
    exposes. test_cases/conversation_turns are populated for round 4
    only, so the report can group turns by test case without a second
    round-trip."""
    scenario: Optional[ScenarioOut] = None
    score: Optional[ScoreOut] = None
    test_cases: Optional[list["Round4TestCaseOut"]] = None
    conversation_turns: Optional[list["Round4TurnOut"]] = None
    round3_turns: Optional[list["Round3TurnOut"]] = None
    round3_runs: Optional[list["Round3RunOut"]] = None
    # Set when status == "scoring_failed" (see models.RoundStatus) - the
    # error from the failed background scoring attempt, so HR can see
    # why instead of a submission just looking stuck.
    scoring_error: Optional[str] = None
    # Set whenever a round closed WITHOUT the candidate clicking Submit -
    # see models.Submission.auto_closed_reason. None for a normal submit.
    auto_closed_reason: Optional[str] = None
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
    # Same reasoning - see Submission.auto_closed_reason.
    auto_closed_reason: Optional[str] = None


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


# ---- Round 4 (conversational automation: the candidate writes their
# own open-ended, self-titled test cases - see routers/candidate.py's
# round 4 section. Deliberately no fixed UI/API/DB categories: picking
# what to test is itself part of what this round assesses.) ----


class Round1ContextOut(BaseModel):
    """What the candidate wrote in round 1, carried into round 4 so
    they're automating their own test cases, not a fresh scenario."""
    scenario_title: str
    scenario_description: str
    submitted_rows: list[dict]


class Round4EnvironmentOut(BaseModel):
    """Auto-generated, fictional test-environment reference facts (test
    login credentials, a simulated API base URL, ...) shown to the
    candidate alongside the scenario description - see
    llm_service.generate_round4_environment. A loose dict rather than
    fixed fields, since different scenarios legitimately need different
    reference facts (a login flow needs credentials; a reporting feature
    might need a date range instead)."""
    fields: dict[str, str]
    notes: Optional[str] = None


class Round4MockupElement(BaseModel):
    """One element on a reference screen, in display order (order IS the
    layout - no free-text position field for the renderer to interpret).
    Structured on purpose, never raw HTML/CSS - see
    llm_service.generate_round4_ui_mockup and app.js's renderMockupScreens,
    which renders each type through the app's own trusted CSS."""
    type: Literal["label", "input", "button", "link", "text"]
    text: str


class Round4MockupScreen(BaseModel):
    name: str
    elements: list[Round4MockupElement]


class Round4UiMockupOut(BaseModel):
    """Auto-generated, structured reference screens (login page, home
    page, ...) shown to the candidate as a static visual reference for
    the app they're automating - see llm_service.generate_round4_ui_mockup.
    Deliberately includes 1-2 pairs of easy-to-confuse element labels per
    scenario, by design - precision in describing the UI is part of what
    this round assesses."""
    screens: list[Round4MockupScreen]


class Round4ExecutionStep(BaseModel):
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


class Round4TurnResponse(BaseModel):
    """The assistant's structured reply for one turn - see
    llm_service.round4_respond and prompts/round4_partial_response.txt.
    No code field on purpose (see Round4ExecutionStep)."""
    response_text: str
    steps: list[Round4ExecutionStep]
    observed_result: str
    status: Literal["pass", "fail", "partial"]


class Round4TestCaseCreate(BaseModel):
    title: Optional[str] = None


class Round4TestCaseOut(BaseModel):
    id: int
    title: Optional[str] = None
    draft_prompt: str
    created_at: datetime
    turn_count: int = 0

    class Config:
        from_attributes = True


class Round4DraftUpdate(BaseModel):
    """PATCH body for autosaving a test case's in-progress, unsent
    message - see candidate.py's PATCH /round/4/test-case/{id}/draft."""
    draft_prompt: str


class Round4TurnCreate(BaseModel):
    test_case_id: int
    # min_length=1 for the same reason every other candidate-input field
    # has it (TestCaseRow, Round2InvestigationRow, Round2SubmissionCreate.
    # root_cause) - a client-side check alone doesn't stop a direct API
    # call from sending an empty prompt and burning an LLM call on it.
    candidate_prompt: str = Field(min_length=1)


class Round4TurnOut(BaseModel):
    id: int
    test_case_id: int
    turn_number: int
    candidate_prompt: str
    model_response: Round4TurnResponse
    created_at: datetime

    class Config:
        from_attributes = True
        # "model_response" collides with pydantic's own reserved
        # "model_" prefix (model_dump, model_validate, ...) - it's just
        # a field name here, not a real conflict, so silence the warning
        # rather than rename a column that mirrors the DB schema.
        protected_namespaces = ()


class Round4CodeSnippetOut(BaseModel):
    """Trial feature (see routers/candidate.py's GET /round/4/turn/{id}/code,
    llm_service.generate_round4_code_snippet) - an on-demand, candidate-
    facing rendering of an already-completed turn as code, in a language
    the candidate picks. Not persisted anywhere and has no effect on
    scoring - purely a rendering of what's already in the transcript."""
    language: str
    code: str


class Round4StateOut(BaseModel):
    """Everything the round 4 candidate screen needs in one call: the
    auto-generated test environment, their own round 1 context, their
    test cases (candidate-created, open-ended), and the full transcript
    (flat - the frontend groups turns by test_case_id)."""
    scenario: ScenarioPublicOut
    submission: SubmissionOut
    round1_context: Round1ContextOut
    environment: Optional[Round4EnvironmentOut] = None
    ui_mockup: Optional[Round4UiMockupOut] = None
    test_cases: list[Round4TestCaseOut]
    turns: list[Round4TurnOut]


# ---- Round 3 (AI-prompted coding: the candidate never writes code
# directly - they direct an LLM turn by turn, and it writes/edits the
# actual source. See docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md.) ----


class Round3StartRequest(BaseModel):
    language: Literal["python", "java", "javascript"]


class Round3DraftUpdate(BaseModel):
    """PATCH body for autosaving the candidate's in-progress, unsent
    message - see candidate.py's PATCH /round/3/draft. Submission-scoped,
    not per-test-case, since round 3 (coding) has a single evolving code
    buffer, not Round 4's multiple self-titled test cases."""
    draft_prompt: str


class Round3TurnCreate(BaseModel):
    candidate_prompt: str = Field(min_length=1)


class Round3CodingTurnResponse(BaseModel):
    """The LLM's classified response for one turn - see
    llm_service.round3_coding_turn and prompts/round3_coding_turn.txt.
    code_after is required when response_kind is "code_edit" (the full
    updated code) and must be absent otherwise (clarify/refuse never
    touch the code)."""
    response_kind: Literal["clarify", "refuse", "code_edit"]
    response_message: str
    code_after: Optional[str] = None

    @model_validator(mode="after")
    def _code_after_required_for_code_edit(self):
        if self.response_kind == "code_edit" and not self.code_after:
            raise ValueError("code_after is required when response_kind is 'code_edit'")
        if self.response_kind != "code_edit" and self.code_after:
            raise ValueError("code_after must not be set when response_kind is 'clarify' or 'refuse'")
        return self


class Round3TurnOut(BaseModel):
    id: int
    turn_number: int
    candidate_prompt: str
    language: str
    response_kind: str
    response_message: str
    code_after: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class Round3RunInputCreate(BaseModel):
    """POST /candidate/round/3/run/input - one line the candidate typed
    in response to whatever the live process's last input() prompt was.
    See execution_service.InteractiveSession.write_input."""
    line: str


class Round3RunPollOut(BaseModel):
    """Response shape for /round/3/run/start and /round/3/run/poll -
    the FULL accumulated output so far (not just what's new since the
    last poll), so the frontend can just re-render the terminal from
    this each time rather than tracking byte offsets itself. Once
    `exited` is true, this is the final state and polling can stop."""
    stdout: str = ""
    stderr: str = ""
    exited: bool = False
    exit_code: Optional[int] = None
    timed_out: bool = False
    infra_error: bool = False


class Round3RunOut(BaseModel):
    id: int
    language: str
    # The exact stdin this run actually used - whatever the candidate
    # typed, or the server's own sample-input fallback when they left the
    # box blank (see candidate.py's round3_coding_run). Always shown
    # alongside the output so a run's result is never a mystery about
    # what input produced it. Field name matches the ORM attribute
    # (models.Round3ExecutionRun.stdin_json), same convention as
    # misses_json/concept_coverage_json elsewhere in this file.
    stdin_json: list[str] = Field(default_factory=list)
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    timed_out: bool
    infra_error: bool
    duration_ms: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class Round3StateOut(BaseModel):
    """Everything the round 3 (coding) candidate screen needs in one
    call: the scenario, their submission (content.language/draft_prompt
    live inside SubmissionOut.content), the full turn history, and every
    run's result."""
    scenario: ScenarioPublicOut
    submission: SubmissionOut
    language: Optional[str] = None
    turns: list[Round3TurnOut]
    runs: list[Round3RunOut]


# ---- HR screening history (per-scenario performance, across everyone
# who's ever attempted it) ----

class MissPattern(BaseModel):
    """One recurring gap and how many scored submissions hit it. Grouped
    by exact text match on the scoring LLM's `misses` strings - see
    scenario_history() in routers/hr.py for why that's a deliberate
    choice over an LLM-summarized version."""
    text: str
    count: int


class ConceptCoverageAverage(BaseModel):
    """One test-case category (Positive/Negative/Boundary/Edge), averaged
    across every scored Round 1 submission against this scenario - see
    scenario_history() in routers/hr.py. avg_pct is None only if no
    scored submission reported this category at all (e.g. the current
    reference set happens to have zero cases of it)."""
    category: str
    avg_pct: Optional[float] = None
    sample_count: int


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
    # Round 1 only - empty for round 2/4 scenarios, which have no
    # concept_coverage data to average (see ConceptCoverageAverage).
    concept_coverage_averages: list[ConceptCoverageAverage] = Field(default_factory=list)
