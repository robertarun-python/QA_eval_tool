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
    # max_length prevents a pre-auth request (no login needed to hit this
    # endpoint) from forcing a huge string into memory/DB comparison -
    # 254 is the standard maximum email length, comfortably above any
    # real username too.
    identifier: str = Field(min_length=1, max_length=254)
    # bcrypt itself only uses the first 72 bytes of a password anyway -
    # 128 is generous headroom above any real password while still
    # bounding a pre-auth request's size.
    password: str = Field(max_length=128)


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
    assessment_window_days: int
    # Per-round time limits (minutes) - null = each scenario's own limit.
    round1_time_limit_minutes: Optional[int] = None
    round2_time_limit_minutes: Optional[int] = None
    round3_time_limit_minutes: Optional[int] = None
    round4_time_limit_minutes: Optional[int] = None

    class Config:
        from_attributes = True


class AppSettingsUpdate(BaseModel):
    round1_passing_score: int = Field(ge=0, le=100)
    round2_passing_score: int = Field(ge=0, le=100)
    round3_passing_score: int = Field(ge=0, le=100)
    round4_passing_score: int = Field(ge=0, le=100)
    final_passing_score: int = Field(ge=0, le=400)
    reapplication_window_months: int = Field(ge=1)
    assessment_window_days: int = Field(ge=1)
    # Applies to every scenario in the round, live ones included, for
    # attempts started after saving (Scenario.round_time_limit_minutes).
    round1_time_limit_minutes: Optional[int] = Field(default=None, ge=1, le=480)
    round2_time_limit_minutes: Optional[int] = Field(default=None, ge=1, le=480)
    round3_time_limit_minutes: Optional[int] = Field(default=None, ge=1, le=480)
    round4_time_limit_minutes: Optional[int] = Field(default=None, ge=1, le=480)


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
    title: str = Field(min_length=1, max_length=300)
    preconditions: str = Field(default="", max_length=2000)
    steps: str = Field(min_length=1, max_length=5000)
    # The concrete values a tester would actually use (inputs, accounts,
    # amounts, dates, ...) as opposed to the narrative in `steps`.
    # Captured as its own field rather than left buried in prose so a
    # later round can consume it structurally - see round1_scoring.txt,
    # which now grades how SPECIFIC this is. default="": every R1
    # submission written before this field existed still validates, and
    # the submit gate is unchanged (title/steps/expected_result only),
    # so adding this can't retroactively block anyone - vagueness costs
    # score, it doesn't cost the ability to submit.
    test_data: str = Field(default="", max_length=2000)
    expected_result: str = Field(min_length=1, max_length=2000)
    priority: Literal["High", "Medium", "Low"] = "Medium"
    type: Literal["Positive", "Negative", "Boundary", "Edge"] = "Positive"


# ---- Scenarios (HR authoring: draft -> published -> moved live for screening) ----

class ScenarioCreate(BaseModel):
    round_number: int
    # No min_length: hr.py's create_scenario does its own whitespace-aware
    # emptiness check (a min_length=1 here wouldn't catch "   ", and would
    # turn that rejection into a generic 422 instead of hr.py's specific
    # 400 - see test_hr_scenario_create_validation.py).
    title: str = Field(max_length=300)
    description: str = Field(max_length=20000)
    experience_band: str  # "0-7" | "7+" - HR picks one explicitly, no "both"
    time_limit_minutes: int = 30
    config_json: dict[str, Any] = {}


class ScenarioUpdate(BaseModel):
    """Partial edit of a draft scenario - only settable while status == draft."""
    title: Optional[str] = Field(default=None, max_length=300)
    description: Optional[str] = Field(default=None, max_length=20000)
    time_limit_minutes: Optional[int] = None
    # Any, not list[TestCaseRow]: same reason as ScenarioOut.reference_json
    # above - list[dict] for rounds 1/2, {"test_cases": [...],
    # "expected_approach": "..."} for round 3. Pydantic can no longer
    # shape-check this on the wire, so hr.py's update_scenario handler
    # does the round-aware validation itself before writing it.
    reference_json: Optional[Any] = None


class ScenarioTimeLimitUpdate(BaseModel):
    """See hr.py's PATCH /scenarios/{id}/time-limit. Round 1-3 scenarios:
    draft-only, same as everything in ScenarioUpdate above (this has its
    own endpoint rather than going through ScenarioUpdate/
    _get_draft_scenario_or_404 only because Round 4 scenarios - which have
    no draft phase - also use it, gated on in-progress instead)."""
    time_limit_minutes: int = Field(ge=1)


class Round2AutomationInstructionsUpdate(BaseModel):
    """See hr.py's PATCH /scenarios/{id}/round4-instructions - editing
    title/description on a round4 scenario regardless of status, same
    reasoning and blocking as the round's time limit. Round 1/2 keep
    title/description as draft-only edits (see ScenarioUpdate) because
    they gate a fixed reference answer that's meaningful to review before
    publishing; round 4 has no such reference, so there's no equivalent
    reason to restrict this to drafts."""
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=20000)


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
    # What a candidate starting now gets - HR Settings' round limit when set,
    # else time_limit_minutes (see models.Scenario.round_time_limit_minutes).
    round_time_limit_minutes: Optional[int] = None
    published_at: Optional[datetime] = None
    # Round 4 only: lets the pre-start briefing (before any submission
    # exists) distinguish the Focused Automation Pilot from the legacy
    # conversational flow - see models.Scenario.is_pilot. False for every
    # round 1-3 scenario and every pre-pilot round 4 scenario.
    is_pilot: bool = False
    # Round 4 only: the AI-Assisted Test Automation mode (the candidate
    # automates their own round 1 design) - see models.Scenario.is_auto.
    # False for every round 1-3 scenario and every other round 4 mode.
    is_auto: bool = False
    # Round 3 only: {"input", "output", "value_type"} - the task's fixed
    # stdin/stdout format, shown with the task (see models.Scenario.round3_io_format).
    round3_io_format: Optional[dict] = None

    class Config:
        from_attributes = True


class ScenarioOut(ScenarioPublicOut):
    """HR-facing shape - includes the reference answer for review/scoring."""
    reference_json: Optional[Any] = None  # list[dict] for rounds 1/2, {"test_cases": [...], "expected_approach": "..."} for round 3
    # Round 4 only: the auto-generated test-environment reference facts
    # and reference UI screens (see Scenario.environment_json/
    # ui_mockup_json). Not on ScenarioPublicOut either - each is included
    # there separately, as Round2AutomationEnvironmentOut/Round2AutomationUiMockupOut, shaped
    # for direct candidate display rather than HR's raw-JSON review.
    environment_json: Optional[dict] = None
    ui_mockup_json: Optional[dict] = None
    # Round 4 only: whether environment_json.fields were hand-set by HR
    # (see Scenario.environment_hr_edited) - the frontend uses this to
    # explain why the "resyncs automatically" note doesn't apply anymore.
    environment_hr_edited: bool = False
    # Round 4 only in practice (round 1/2 scenarios never set anything
    # here) - the round's mode lives here. HR-facing; candidates never
    # see this.
    config_json: dict[str, Any] = {}


# ---- Submissions / Round 1 ----

class SubmissionCreate(BaseModel):
    # max_length=100: comfortably above any real round 1 attempt (a
    # timed round, one sitting) while bounding how many rows - and LLM
    # scoring tokens - a single submission can force.
    content: list[TestCaseRow] = Field(min_length=1, max_length=100)


# ---- Round 2 candidate submission ----
#
# Deliberately NOT TestCaseRow - a debugging investigation isn't a list
# of test cases, it's a list of things checked plus one concluding
# root-cause statement. HR's reference (still TestCaseRow-shaped, see
# above) and the candidate's answer are genuinely different shapes now;
# scoring bridges that (see llm_service.score_round2_submission).

class Round2InvestigationRow(BaseModel):
    area: str = Field(min_length=1, max_length=2000)  # what the candidate checked/investigated at this step


class Round2SubmissionCreate(BaseModel):
    investigation: list[Round2InvestigationRow] = Field(min_length=1, max_length=50)
    root_cause: str = Field(min_length=1, max_length=5000)


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
    # Same row/length caps as SubmissionCreate/Round2SubmissionCreate
    # above, despite being otherwise permissive (see this class's own
    # docstring) - "whatever's there, even nothing" still shouldn't mean
    # "arbitrarily large."
    content: list[dict] = Field(default=[], max_length=100)          # round 1 rows, as collected client-side
    investigation: list[dict] = Field(default=[], max_length=50)     # round 2
    root_cause: str = Field(default="", max_length=5000)             # round 2


class TabSwitchOut(BaseModel):
    """Response to logging one fullscreen-exit - see candidate.py's
    log_tab_switch. strike_count is the running total for this
    submission (Submission.tab_switch_count); round_ended is True the
    moment the 3rd strike just force-finalized the round (see
    app.js's fullscreenchange handler, which reacts to this instead of
    guessing the count client-side)."""
    strike_count: int
    round_ended: bool


class ScoreOut(BaseModel):
    coverage_score: Optional[int]
    misses_json: list
    # Round 1 only - see models.Score.concept_coverage_json. Empty list
    # for rounds 2/4.
    concept_coverage_json: list = Field(default_factory=list)
    # Round 3 only - see models.Score.test_results_json. Empty list for
    # other rounds.
    test_results_json: list = Field(default_factory=list)
    # Round 3 only - see models.Score.correctness_score and its sibling
    # columns. None for other rounds.
    correctness_score: Optional[int] = None
    precision_score: Optional[int] = None
    efficiency_score: Optional[int] = None
    independent_judgment_score: Optional[int] = None
    final_score: Optional[int]
    feedback_text: Optional[str]
    # Human-override audit trail (see hr.py's PATCH /submissions/{id}/score) -
    # always present, all null/false until HR ever touches this score.
    original_final_score: Optional[int] = None
    overridden_by_hr: bool = False
    # Round 1 only - see models.Score.specificity. None for every other
    # round and for scores predating the specificity rubric. HR-only by
    # the same construction as evidence_audit below.
    specificity: Optional[dict] = None
    # Round 4 only - see models.Score.evidence_audit. None for every
    # other round. HR-only by construction: ScoreOut is only ever
    # embedded in SubmissionReportOut, which is only ever returned from
    # require_hr-gated routes - never from a candidate-facing endpoint.
    evidence_audit: Optional[dict] = None
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
    feedback_text: Optional[str] = Field(default=None, max_length=5000)
    override_note: str = Field(min_length=1, max_length=2000)


ASSESSOR_ONLY_CONTENT_KEYS = frozenset({"planted_flaw", "unrequested_checks", "fabricated_observations", "changed_values"})


def _strip_keys(value, keys):
    if isinstance(value, dict):
        return {k: _strip_keys(v, keys) for k, v in value.items() if k not in keys}
    if isinstance(value, list):
        return [_strip_keys(v, keys) for v in value]
    return value


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
    # This attempt's own time limit (recorded when it started - see
    # models.Submission.time_limit_minutes); the candidate's timer uses it.
    time_limit_minutes: Optional[int] = None
    # See models.Submission.submitted_at - when this round actually
    # finished (real submit, candidate timeout, or lazy server-side
    # timeout close). None while still in_progress.
    submitted_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True

    @field_validator("content", mode="after")
    @classmethod
    def _hide_assessor_only_fields(cls, v):
        """Content can carry notes meant only for HR and the scorer - e.g.
        planted_flaw on an automation turn (what the assistant deliberately
        weakened for the candidate to catch). Stripped from everything a
        candidate is sent; SubmissionReportOut (HR) keeps them."""
        return _strip_keys(v, ASSESSOR_ONLY_CONTENT_KEYS)


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
    round3_turns: Optional[list["Round3TurnAuditOut"]] = None

    @field_validator("content", mode="after")
    @classmethod
    def _hide_assessor_only_fields(cls, v):
        # Overrides SubmissionOut's: HR sees assessor-only notes such as
        # planted_flaw.
        return v
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
    # Round 1 only: a read-only look at the SAME test-environment
    # reference (credentials, API/DB details, ...) round 2 generates and
    # owns (Scenario.environment_json on the live round-2 scenario for
    # this band) - so a candidate can write round 1 test data that's
    # still accurate once they reach round 2, without round 1 ever
    # generating or storing its own separate copy. None if no round 2
    # scenario is live yet for this band. See candidate.py's get_round.
    environment: Optional["Round2AutomationEnvironmentOut"] = None
    # Same deal as environment above, for the reference app screens
    # (Scenario.ui_mockup_json on that same live round-2 scenario).
    ui_mockup: Optional["Round2AutomationUiMockupOut"] = None
    # Round 1, when its Round 2 runs on the real practice environment: the app's
    # structure (screens, API, database) - practice_engine.reference.round1_panel.
    reference_panel: Optional[dict] = None


# ---- HR candidate dashboard ----

class CandidateRoundSummary(BaseModel):
    round_number: int
    status: str  # "not_started" | "in_progress" | "submitted" | "scored" | "scoring_failed"
    final_score: Optional[int] = None
    # See models.Submission.submitted_at - when this specific round
    # actually finished. None for not_started/in_progress.
    submitted_at: Optional[datetime] = None
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
    aggregate_score: Optional[int] = None  # sum of the 4 rounds' final_score, out of 400 - None until at least one is scored
    reapplied_within_window: bool = False
    # "selected"/"not_selected" only once every round has actually been
    # scored (a partial aggregate_score mid-assessment is not a real
    # verdict) - "in_progress" otherwise, covering not-started, still
    # in-progress, and a scoring_failed round blocking a final read the
    # same way. Computed in hr.py's _build_candidate_summary against
    # AppSettings.final_passing_score - see that function for why this
    # lives server-side rather than being re-derived in the frontend.
    result: Literal["selected", "not_selected", "in_progress"] = "in_progress"


# ---- Bulk candidate upload (see routers/hr.py's POST /candidates/upload,
# credential_service.py) ----

class BulkUploadRowResult(BaseModel):
    row_number: int
    email: Optional[str] = None
    status: Literal["created", "reset", "error"]
    username: Optional[str] = None
    # Only set for status == "created" - a brand new account's randomly
    # generated temporary password (see credential_service.
    # generate_temporary_password), shown here exactly once so HR can
    # copy it into whatever out-of-band channel they use to reach the
    # candidate. None for "reset" (an existing account's password is
    # left exactly as it was - see candidate_upload_service._reset_and_
    # archive) and for "error" rows.
    password: Optional[str] = None
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


# ---- Cross-round candidate summary (HR's candidate-detail view - see
# models.CandidateSummary, llm_service.generate_candidate_summary, and
# routers/hr.py's GET/POST/DELETE /candidates/{id}/summary plus POST
# .../summary/pdf). Persisted, one per candidate - POST generates and
# saves it, GET reads the saved one back with no LLM call, DELETE clears
# it, and the PDF export always renders whatever's currently saved.
# Named CandidateAssessmentSummaryOut, not CandidateSummaryOut, to avoid
# colliding with the per-round-status shape above. ----

class CandidateRoundComment(BaseModel):
    """Bulleted, not a paragraph - see prompts/candidate_summary_generation.txt.
    did_well/missed are each a short list of concrete, single-fact
    bullets (may be empty - a round can genuinely have nothing to fault,
    or nothing praiseworthy, or no score yet at all)."""
    round_number: int
    did_well: list[str] = Field(default_factory=list, max_length=6)
    missed: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("did_well", "missed")
    @classmethod
    def _bullet_length(cls, bullets: list[str]) -> list[str]:
        for b in bullets:
            if len(b) > 500:
                raise ValueError("Each bullet must be under 500 characters - this is a pointer, not a paragraph.")
        return bullets


class CandidateAssessmentSummaryOut(BaseModel):
    candidate_email: str
    experience_band: Optional[str] = None
    round_comments: list[CandidateRoundComment]  # one per round the candidate has actually reached
    key_observations: list[str] = Field(default_factory=list, max_length=8)  # cross-round highlights, bulleted
    verdict: str = Field(max_length=1000)  # short, decisive - not a paragraph
    # Always set in practice (models.CandidateSummary.updated_at) - built
    # manually in hr.py from the ORM row's fields plus the User's own
    # email/band, never via from_attributes, so this stays Optional only
    # as a defensive shape guarantee, not because it's expected to be missing.
    generated_at: Optional[datetime] = None


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


class Round2AutomationEnvironmentOut(BaseModel):
    """Auto-generated, fictional test-environment reference facts (test
    login credentials, a simulated API base URL, ...) shown to the
    candidate alongside the scenario description - see
    llm_service.generate_round2_automation_environment. A loose dict rather than
    fixed fields, since different scenarios legitimately need different
    reference facts (a login flow needs credentials; a reporting feature
    might need a date range instead)."""
    fields: dict[str, str]
    notes: Optional[str] = None


class Round2AutomationEnvironmentUpdate(BaseModel):
    """Body for hr.py's update_round2_automation_environment - HR hand-editing the
    auto-generated environment_json.fields (e.g. pinning a specific test
    login) instead of only being able to regenerate the whole sheet. Same
    shape as Round2AutomationEnvironmentOut; kept separate since an update payload
    and a read shape can diverge later even though they match today."""
    fields: dict[str, str]
    notes: Optional[str] = None


class Round2AutomationMockupElement(BaseModel):
    """One element on a reference screen, in display order (order IS the
    layout - no free-text position field for the renderer to interpret).
    Structured on purpose, never raw HTML/CSS - see
    llm_service.generate_round2_automation_ui_mockup and app.js's renderMockupScreens,
    which renders each type through the app's own trusted CSS."""
    type: Literal["label", "input", "button", "link", "text"]
    text: str


class Round2AutomationMockupScreen(BaseModel):
    name: str
    elements: list[Round2AutomationMockupElement]


class Round2AutomationUiMockupOut(BaseModel):
    """Auto-generated, structured reference screens (login page, home
    page, ...) shown to the candidate as a static visual reference for
    the app they're automating - see llm_service.generate_round2_automation_ui_mockup.
    Deliberately includes 1-2 pairs of easy-to-confuse element labels per
    scenario, by design - precision in describing the UI is part of what
    this round assesses."""
    screens: list[Round2AutomationMockupScreen]


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
    """One turn's reply in the retired conversational Round 2 format -
    kept so HR can still read those old results. No code field on purpose
    (see Round4ExecutionStep)."""
    response_text: str
    steps: list[Round4ExecutionStep]
    observed_result: str
    status: Literal["pass", "fail", "partial"]


class Round4PilotTurnResponse(BaseModel):
    """The assistant's structured reply for one code-writing turn (named
    for the retired pilot that introduced it; now validates every Round 2
    automation turn - see llm_service.round2_automation_turn). code_after
    carries the full updated file when response_kind is "code_edit" - same
    shape as Round3CodingTurnResponse."""
    response_kind: Literal["clarify", "explain", "code_edit", "refuse"]
    response_message: str
    code_after: Optional[str] = None


class Round2AutomationFindingEvidence(BaseModel):
    """One citation backing a Round2AutomationFinding - see
    services.round2_automation_evidence_audit, which is the deterministic (no LLM)
    layer that actually checks these against the transcript rather than
    trusting them. `turn` is a 1-indexed position in the flattened
    sequence of every turn across every test case, in payload order -
    not the per-test-case turn_number used elsewhere in this app (see
    round2_automation_scoring.txt). Exactly one of
    (turn + quote) or (test_case + no_turns) is expected; the auditor
    treats any other combination as invalid rather than guessing."""
    turn: Optional[int] = None
    quote: Optional[str] = None
    test_case: Optional[str] = None
    no_turns: bool = False


class Round2AutomationFinding(BaseModel):
    """One negative finding from the Round 2 automation scorer (prompts/
    round2_automation_scoring.txt), replacing a bare miss string with a claim the
    deterministic evidence auditor can actually check. See
    services.round2_automation_evidence_audit.audit_round2_automation_findings - a finding
    with no evidence, a fabricated quote, or an out-of-range turn
    reference never survives to become a scored weakness."""
    claim: str = Field(min_length=1)
    severity: Literal["low", "medium", "high"] = "low"
    evidence: list[Round2AutomationFindingEvidence] = Field(default_factory=list)


class Round4TestCaseOut(BaseModel):
    id: int
    title: Optional[str] = None
    draft_prompt: str
    created_at: datetime
    turn_count: int = 0

    class Config:
        from_attributes = True


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


class Round2EntryStateOut(BaseModel):
    """Everything the round 4 candidate screen needs in one call: the
    auto-generated test environment, their own round 1 context, their
    test cases (candidate-created, open-ended), and the full transcript
    (flat - the frontend groups turns by test_case_id)."""
    scenario: ScenarioPublicOut
    submission: SubmissionOut
    round1_context: Round1ContextOut
    environment: Optional[Round2AutomationEnvironmentOut] = None
    ui_mockup: Optional[Round2AutomationUiMockupOut] = None
    test_cases: list[Round4TestCaseOut]
    turns: list[Round4TurnOut]


# ---- AI-Assisted Test Automation round (Scenario.config_json["mode"] ==
# "ai_test_automation") - the candidate automates test cases THEY designed
# in round 1 - the only live round 2 format; see routers/candidate.py's
# /round/2/auto/* endpoints. ----

class Round2AutomationDesignRowOut(BaseModel):
    """One of the candidate's own Round 1 rows, as an immutable snapshot.
    `index` is its position in the Round 1 submission - the handle used to
    select it and to attach refinements to it (Round 1 rows still carry no
    id of their own). `refinements` is append-only; the other fields are
    never rewritten once selected."""
    index: int
    title: str = ""
    preconditions: str = ""
    steps: str = ""
    test_data: str = ""
    expected_result: str = ""
    refinements: list[str] = Field(default_factory=list)


class Round2AutomationLanguageCreate(BaseModel):
    """The candidate's language for this round. Writable exactly once,
    and before any test case can be selected - see routers/candidate.py's
    round2_automation_lock_language. Round 3 inherits whatever is locked here
    (see _round3_language_for) rather than asking again."""
    language: Literal["python", "java", "javascript"]


class Round2AutomationSelectCreate(BaseModel):
    """1-2 Round 1 rows, by index - normally sent one at a time (the
    candidate automates a test case, then decides whether to add a
    second), but a caller may still send both together. Only after the
    language is locked, and never re-selecting a row already added - see
    routers/candidate.py's round2_automation_select."""
    row_indexes: list[int] = Field(min_length=1, max_length=2)


class Round2AutomationRefineCreate(BaseModel):
    row_index: int
    note: str = Field(min_length=1, max_length=2000)


class Round2AutomationTestDataUpdate(BaseModel):
    """Corrects ONE selected test case's own test data for automation
    purposes - see routers/candidate.py's round2_automation_update_test_data.
    Never touches the immutable Round 1 record or the original design
    snapshot's own test_data, which stays available for HR/scoring
    transparency once a correction is made (see
    scoring_service._auto_tc_design_only)."""
    row_index: int
    test_data: str = Field(min_length=1, max_length=2000)


class Round2AutomationTurnCreate(BaseModel):
    """row_index picks which selected test case this turn is about -
    optional only when exactly one test case is selected (see
    routers/candidate.py's _resolve_tc_row), required once there are two."""
    candidate_prompt: str = Field(min_length=1, max_length=10000)
    row_index: Optional[int] = None


class Round2AutomationTurnOut(BaseModel):
    row_index: int
    turn_number: int
    candidate_prompt: str
    response_kind: Literal["clarify", "explain", "code_edit", "refuse"]
    response_message: str
    code_after: Optional[str] = None


class Round2AutomationClarifyCreate(BaseModel):
    """The candidate's automation instruction, submitted to the
    clarification-only flow (see routers/candidate.py's
    round2_automation_clarify) rather than /turn - this endpoint never writes
    code, regardless of how complete the instruction turns out to be.
    row_index: see Round2AutomationTurnCreate."""
    candidate_prompt: str = Field(min_length=1, max_length=10000)
    row_index: Optional[int] = None


class Round2AutomationClarifyLLMResponse(BaseModel):
    """The raw shape the clarify-check LLM call returns - see
    llm_service.round2_automation_clarify and prompts/round2_automation_clarify.txt.
    Internal to that function; the endpoint's actual response is the
    same Round2AutomationTurnOut every other R2 turn uses, with code_after
    always null - this flow never generates code.

    The LLM is deliberately confined to CLASSIFICATION (a bounded
    status, plus the raw material a deterministic rule needs), not
    given authority over the actual outcome - round2_automation_clarify_policy
    .build_clarify_response is what decides what the candidate sees.
    "contradicts_prior" is its own status, not folded into
    "insufficient": a candidate who already said two different things
    has too much information, not too little, and needs a different
    follow-up (which one did you mean) than an honestly incomplete
    instruction does (what should happen)."""
    status: Literal["sufficient", "insufficient", "contradicts_prior"]
    question: Optional[str] = None
    prior_value: Optional[str] = None
    current_value: Optional[str] = None

    @model_validator(mode="after")
    def _fields_match_status(self):
        if self.status == "insufficient" and not self.question:
            raise ValueError("question is required when status is 'insufficient'")
        if self.status == "contradicts_prior" and not (self.prior_value and self.current_value):
            raise ValueError("prior_value and current_value are required when status is 'contradicts_prior'")
        return self


class Round2AutomationCodeUpdate(BaseModel):
    """The candidate's own direct edit to one selected test case's own
    code buffer. Recorded as its own audit entry (see
    Round2AutomationTCStateOut.code_edits_count) so "what the assistant
    produced" and "what the candidate changed themselves" stay
    distinguishable at scoring time. row_index: see Round2AutomationTurnCreate."""
    code: str = Field(min_length=1, max_length=200000)
    row_index: Optional[int] = None


class Round2AutomationRunCreate(BaseModel):
    """Optional body for run - one test case's current editor contents,
    so it acts on exactly what's on screen for that TC. code: None = use
    what's already stored. row_index: see Round2AutomationTurnCreate."""
    code: Optional[str] = Field(default=None, min_length=1, max_length=200000)
    row_index: Optional[int] = None


class Round2AutomationTCSubmitEntry(BaseModel):
    """One selected test case's final code - see Round2AutomationSubmitCreate.entries.
    No candidate-written interpretation: whether the run genuinely proves
    the expected result is judged from the code and execution result
    alone (see prompts/round2_automation_scoring.txt)."""
    row_index: int
    code: Optional[str] = Field(default=None, min_length=1, max_length=200000)


class Round2AutomationSubmitCreate(BaseModel):
    """`entries` is one per selected test case - submit checks that each
    selected test case was independently run at least once, not just one
    shared run for the whole round (see routers/candidate.py's
    round2_automation_submit).

    Back-compat convenience: with exactly one test case selected, the
    flat `code` field below may be used instead of `entries` - the shape
    every existing single-TC caller already sends, wrapped into a
    single-entry list for that one TC. Two or more selected test cases
    must use `entries` explicitly - there's no longer a single buffer a
    flat `code` could unambiguously refer to."""
    entries: Optional[list[Round2AutomationTCSubmitEntry]] = None
    code: Optional[str] = Field(default=None, min_length=1, max_length=200000)


class Round2AutomationRunOut(BaseModel):
    stdout: str
    stderr: str
    exit_code: Optional[int] = None
    timed_out: bool = False
    infra_error: bool = False
    # Both already computed by execution_service.run_code (duration_ms)
    # or trivial to stamp at the call site (ran_at) - not persisted
    # before this, so an older run recorded before this field existed
    # reads back as None. "Where available" in the per-TC presentation
    # requirement, not a new logging system - this is still exactly one
    # snapshot (the last run), same as every field above it.
    duration_ms: Optional[int] = None
    ran_at: Optional[datetime] = None
    # passed / failed / incomplete / timed_out / error - "passed" only for a
    # complete test that exited cleanly (services/round2_typist.run_status).
    status: Optional[str] = None


class Round2AutomationTCStateOut(BaseModel):
    """One selected test case's own independent automation state - its
    own code, AI turns, direct-edit count, last run and interpretation,
    untouched by any other selected test case (see routers/candidate.py's
    round2_automation_turn/code/run, all row_index-scoped). code starts as the
    provided environment at selection time (see round2_automation_select)."""
    row_index: int
    code: str = ""
    turns: list[Round2AutomationTurnOut] = Field(default_factory=list)
    code_edits_count: int = 0
    last_run: Optional[Round2AutomationRunOut] = None
    validation: str = ""


class Round2AutomationStateOut(BaseModel):
    # The real practice environment's reference panel (practice_engine.reference) - None for older practice apps.
    reference_panel: Optional[dict] = None
    """Everything the automation round's candidate screen needs. Nothing
    here carries ground truth, validation notes, the scoring rubric, or
    the traceability signal - those are HR/system-only (see
    prompts/round2_automation_scoring.txt's REFERENCE ONLY section).

    language is None until the candidate locks it (see
    round2_automation_lock_language) - test-case selection is blocked until
    then, so every other field below is meaningless pre-lock.

    tc_state carries each selected test case's own independent
    automation state (see Round2AutomationTCStateOut); `selected` itself stays
    the immutable design snapshot only, never mutable state."""
    language: Optional[str] = None
    language_locked: bool = False
    available_rows: list[Round2AutomationDesignRowOut]
    selected: list[Round2AutomationDesignRowOut]
    selection_locked: bool
    environment_code: str
    # Candidate-facing reference material, reused unmodified from whatever
    # the legacy round 4 generator already produced for this scenario at
    # creation time (see hr.py's create_scenario - it runs for every
    # round_number==2 scenario regardless of mode) - never code, never the
    # solution, just what a real QA engineer would be handed before
    # writing a test plan: the app's own screens and its test-environment
    # facts (credentials, base URL, ...). None if this scenario predates
    # that generator or it failed - the frontend just shows nothing then.
    environment: Optional[Round2AutomationEnvironmentOut] = None
    ui_mockup: Optional[Round2AutomationUiMockupOut] = None
    tc_state: list[Round2AutomationTCStateOut] = Field(default_factory=list)


# ---- Round 3 (AI-prompted coding: the candidate never writes code
# directly - they direct an LLM turn by turn, and it writes/edits the
# actual source. See docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md.) ----


class Round3StartRequest(BaseModel):
    """language is accepted but ignored: round 3 now inherits whatever
    the candidate locked in round 2's automation round rather than asking
    again (see routers/candidate.py's _round3_language_for). Kept
    Optional, rather than removed, purely so an existing client still
    sending it isn't broken by this change."""
    language: Optional[Literal["python", "java", "javascript"]] = None


class Round3DraftUpdate(BaseModel):
    """PATCH body for autosaving the candidate's in-progress, unsent
    message - see candidate.py's PATCH /round/3/draft. Submission-scoped,
    not per-test-case, since round 3 (coding) has a single evolving code
    buffer, not Round 4's multiple self-titled test cases."""
    draft_prompt: str = Field(max_length=10000)


class Round3TurnCreate(BaseModel):
    candidate_prompt: str = Field(min_length=1, max_length=10000)


class Round3DirectEditCreate(BaseModel):
    """POST body for /round/3/edit - the candidate's own raw code, typed
    or pasted directly into the editable code pane (see
    docs/superpowers/specs/2026-08-27-round3-direct-code-edit-design.md)."""
    # 100k chars is generous for genuinely pasted/typed code while still
    # bounding the syntax-fix LLM call's input and execution_service's
    # subprocess payload.
    code: str = Field(min_length=1, max_length=100_000)


class CategoryStatusEntry(BaseModel):
    """One construct category's classification for a single turn - see
    llm_service.round3_coding_turn and round3_construct_engine.decide.
    "declared" commits to a specific choice (value required); only
    "attempted_but_vague" is ever actually asked about, so only it
    requires the vocabulary-free neutral_question - "not_addressed"
    stays silently open and is never surfaced to the candidate, so
    forcing a question for it would be pure overhead with no consumer."""
    status: Literal["declared", "attempted_but_vague", "not_addressed"]
    value: Optional[str] = None
    neutral_question: Optional[str] = None

    @model_validator(mode="after")
    def _fields_match_status(self):
        if self.status == "declared" and not self.value:
            raise ValueError("value is required when status is 'declared'")
        if self.status == "attempted_but_vague" and not self.neutral_question:
            raise ValueError("neutral_question is required when status is 'attempted_but_vague'")
        return self


class Round3CodingTurnResponse(BaseModel):
    """The LLM's classified response for one turn - see
    llm_service.round3_coding_turn/round3_syntax_fix and
    prompts/round3_coding_turn.txt/round3_syntax_fix.txt. code_after is
    required when response_kind is "code_edit" or "direct_edit" (the
    full updated code) and must be absent otherwise (clarify/refuse
    never touch the code). category_status is empty whenever the turn
    has no open construct-checklist categories (an unscoped scenario,
    or every required category already declared/evidenced) - see
    round3_construct_engine."""
    response_kind: Literal["clarify", "refuse", "code_edit", "direct_edit", "explain"]
    response_message: str
    code_after: Optional[str] = None
    category_status: dict[str, CategoryStatusEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _code_after_required_for_code_edit(self):
        needs_code = self.response_kind in ("code_edit", "direct_edit")
        if needs_code and not self.code_after:
            raise ValueError("code_after is required when response_kind is 'code_edit' or 'direct_edit'")
        if not needs_code and self.code_after:
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


class Round3TurnAuditOut(Round3TurnOut):
    """HR-only audit view of the same turn (see hr.py's
    _build_submission_reports, the only place this is built) - never
    served to the candidate, who only ever sees the plain Round3TurnOut
    above via /candidate/round/3/state. Adds exactly what
    Round3TurnOut's fields don't already make explicit for an audit
    trail: whether this turn actually changed the code (derivable from
    response_kind, but spelled out so a reviewer doesn't have to know
    the kind vocabulary), a short human label for what the turn was
    asking for, and how many lines changed versus the previous turn's
    code snapshot. Round 3 is a single evolving code buffer, not a
    multi-file repo, so there is no separate "files affected" - every
    change is to that one buffer."""
    # Defaulted, not required: SubmissionReportOut.round3_turns shares its
    # field name with the ORM relationship (Submission.round3_turns), so
    # Pydantic auto-populates it straight from the ORM objects the moment
    # SubmissionReportOut.model_validate(submission) runs, before
    # _build_submission_reports ever gets to overwrite it with real
    # computed values below - same as round3_runs already does harmlessly.
    # Without defaults here that eager pass fails immediately, since these
    # three fields don't exist on the ORM Round3Turn object itself.
    code_modified: bool = False
    requested_scope: str = ""
    lines_changed: Optional[int] = None


class Round3RunInputCreate(BaseModel):
    """POST /candidate/round/3/run/input - one line the candidate typed
    in response to whatever the live process's last input() prompt was.
    See execution_service.InteractiveSession.write_input."""
    line: str = Field(max_length=2000)


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
