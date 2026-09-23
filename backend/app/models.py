"""
ORM models. See ARCHITECTURE.md for the "why" behind this shape.

Note on JSON columns: SQLite doesn't have a native JSON type, but
SQLAlchemy's `JSON` type transparently serializes to/from a TEXT column,
so this same model code works unchanged if you migrate to Postgres
later (where JSON is native).
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, JSON, Enum, Boolean
)
from sqlalchemy.orm import relationship

from .database import Base
from .services import round3_io_format


class Role(str, enum.Enum):
    candidate = "candidate"
    hr = "hr"


class ExperienceBand(str, enum.Enum):
    junior = "0-7"      # 0-7 years
    senior = "7+"        # 7+ years
    both = "both"         # scenario applies to either band


class RoundStatus(str, enum.Enum):
    in_progress = "in_progress"
    submitted = "submitted"
    scored = "scored"
    # The background scoring call raised (a bad LLM response, a network
    # blip, a rate limit, ...) - distinct from `submitted` (still normal,
    # awaiting the background task) so a real failure is visible to HR
    # instead of leaving the submission looking like it's just pending
    # forever. See scoring_service.score_submission_in_background and
    # Submission.scoring_error.
    scoring_failed = "scoring_failed"


class ScenarioStatus(str, enum.Enum):
    draft = "draft"          # created, reference generated, HR still reviewing
    published = "published"   # approved and ready to be used - may or may not be the live one


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    # NULL for HR/seeded accounts, which still log in by email - only
    # bulk-uploaded candidates (see credential_service.py) get one, so
    # they can log in with a plain username instead of their full email.
    # SQLite's UNIQUE index permits multiple NULLs, which is exactly what
    # letting every non-bulk account leave this unset needs.
    username = Column(String, unique=True, index=True, nullable=True)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(Role), nullable=False)
    # NULL for HR users - the band only matters for candidates.
    experience_band = Column(Enum(ExperienceBand), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Single-active-session enforcement (see dependencies.get_current_user) -
    # a random id minted fresh on every login and embedded in that login's
    # JWT as the "sid" claim. A later login overwrites this, so an earlier
    # still-unexpired token's "sid" stops matching - that's the whole
    # mechanism, no server-side token/session table needed. Enforcement
    # only actually kicks a mismatched token out while its candidate has a
    # round in_progress (see the same function) - outside of an active
    # round there's nothing at stake, so an old session is left alone
    # rather than forcing a surprise logout for no reason. NULL for an
    # account that predates this column, or that has never logged in
    # since - get_current_user treats that the same as a match so
    # existing sessions at deploy time aren't retroactively kicked.
    active_session_id = Column(String, nullable=True)

    submissions = relationship("Submission", back_populates="candidate")
    appearances = relationship(
        "CandidateAppearance", back_populates="user",
        order_by="CandidateAppearance.created_at",
    )


class Scenario(Base):
    __tablename__ = "scenarios"
    # Without this, SQLite reuses a deleted row's id for the next insert
    # whenever that row held the current max id (its default rowid
    # behavior, not an SQLAlchemy quirk) - since scenarios.id is used to
    # pick "the most recent one" (list_scenarios), a recycled id would
    # sort as older than it really is. This forces ids to only ever
    # increase, matching a DELETE-capable table's actual needs.
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True)
    round_number = Column(Integer, nullable=False)  # 1, 2, or 3
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    experience_band = Column(Enum(ExperienceBand), default=ExperienceBand.both)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    # Round-specific extras, e.g. round 2's seeded bug, round 3's
    # target test case list. Keeping this as JSON avoids a wide table
    # of mostly-null columns for round-specific fields.
    config_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def is_pilot(self) -> bool:
        """Round 4 only: whether this is the Focused Automation Pilot
        (single-file coding exercise) rather than the legacy conversational
        flow - see seed_round4_pilot.py, which is the only writer of
        config_json["mode"] == "pilot_automation". A computed property
        (same pattern as Submission.tab_switch_count) so schemas.ScenarioPublicOut
        can expose it via from_attributes without duplicating the check -
        candidate.py's pre-start screen needs this to know which briefing
        to show before a submission (and therefore Round4StateOut.is_pilot)
        exists yet."""
        return (self.config_json or {}).get("mode") == "pilot_automation"

    @property
    def is_auto(self) -> bool:
        """Round 4 only: whether this is the AI-Assisted Test Automation
        round (the candidate automates the test cases they designed in
        round 1) - see seed_round4_auto.py, the only writer of
        config_json["mode"] == "ai_test_automation". Same computed-property
        pattern and same reason as is_pilot above."""
        return (self.config_json or {}).get("mode") == "ai_test_automation"

    @property
    def round3_io_format(self) -> dict | None:
        """Round 3 only: the task's stdin/stdout format - the one dict the
        candidate's screen shows, the hidden-test generator is given, and
        the assistant may repeat (see services/round3_io_format.py). Same
        computed-property pattern as is_pilot, so schemas.ScenarioPublicOut
        exposes it via from_attributes. None for every other round."""
        if self.round_number != 3:
            return None
        return round3_io_format.for_config(self.config_json)

    # Draft -> published lifecycle: HR reviews the generated reference
    # before candidates can see the scenario. Any number of scenarios
    # for the same (round_number, experience_band) can be `published`
    # at once (that's just "approved, in the library") - `is_live` is
    # the separate flag that says which single one of them candidates
    # actually get served, enforced in the "move to screening" endpoint.
    status = Column(Enum(ScenarioStatus), default=ScenarioStatus.draft, nullable=False)
    is_live = Column(Boolean, default=False, nullable=False)
    reference_json = Column(JSON, nullable=True)  # HR-approved "superhuman" reference answer
    # Round 4 only: auto-generated fictional test-environment reference
    # facts (credentials, a simulated API base URL, ...) shown to
    # candidates alongside the scenario description - see
    # llm_service.generate_round4_environment. Same lifecycle as
    # reference_json: generated at creation, HR can regenerate it,
    # required before publish.
    environment_json = Column(JSON, nullable=True)
    # Round 4 only: auto-generated structured reference screens (login
    # page, home page, ...) shown to candidates as a static visual
    # reference for the app they're automating - see
    # llm_service.generate_round4_ui_mockup. Structured (screens ->
    # ordered typed elements), never raw HTML, so the frontend renders it
    # through trusted CSS instead of injecting LLM-authored markup. Same
    # lifecycle as environment_json: generated at creation, HR can
    # regenerate it (together with environment_json), required before publish.
    ui_mockup_json = Column(JSON, nullable=True)
    # Round 4 only: set when HR hand-edits environment_json's fields (see
    # hr.py's update_round4_environment) - makes _resync_round4_reference_for_band
    # skip regenerating environment_json for this scenario when a different
    # round 1 scenario goes live for the same band, so an HR-typed credential
    # isn't silently overwritten by that background resync. Only HR's own
    # "Regenerate" button (regenerate_reference) clears it back to False.
    environment_hr_edited = Column(Boolean, default=False, nullable=False)
    time_limit_minutes = Column(Integer, default=30, nullable=False)
    published_at = Column(DateTime, nullable=True)

    submissions = relationship("Submission", back_populates="scenario")


class Submission(Base):
    __tablename__ = "submissions"

    id = Column(Integer, primary_key=True)
    # index=True on both - nearly every candidate-facing query filters on
    # one of these (start_round, submit_round, _live_scenario lookups,
    # _max_completed_round, HR's per-candidate report), and SQLite doesn't
    # auto-index foreign keys the way some other DBs do.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=False, index=True)
    round_number = Column(Integer, nullable=False)
    # Structured content (list of test-case row dicts for round 1) rather
    # than free text - see schemas.TestCaseRow. JSON works over SQLite
    # (serializes to a TEXT column transparently) same as reference_json.
    content = Column(JSON, nullable=True)
    status = Column(Enum(RoundStatus), default=RoundStatus.in_progress)
    # Set when the candidate starts the round (POST .../start), not at
    # row-creation time - this is the timer's zero point, kept
    # server-side so a page refresh can't reset the candidate's clock.
    started_at = Column(DateTime, nullable=True)
    # Set the moment status actually transitions to "submitted" - a real
    # submit (routers/candidate.py's 4 submit endpoints), a candidate-
    # triggered timeout (expire_round), or a lazy server-side timeout
    # close (scoring_service.close_expired_submissions, which backdates
    # this to the round's own deadline rather than whenever HR happened
    # to trigger the lazy check - see that function). NULL for anything
    # still in_progress. This is the real "when did they finish" signal
    # HR's dashboard needs - created_at is row-creation time (~equal to
    # started_at, not when the round ended), and status alone carries no
    # timestamp of its own.
    submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Re-application handling (see CandidateAppearance): a reset moves
    # every one of the candidate's current submissions to archived=True
    # rather than deleting them, so a fresh attempt doesn't collide with
    # the old one in the many `.first()` lookups across candidate.py that
    # otherwise assume "one submission per (user, scenario)" - every one
    # of those needs `archived == False` in its filter. appearance_id is
    # purely for grouping a cycle's submissions in HR's history drill-down,
    # not read by any gating logic.
    archived = Column(Boolean, nullable=False, default=False)
    appearance_id = Column(Integer, ForeignKey("candidate_appearances.id"), nullable=True)
    # Set when status becomes scoring_failed (see RoundStatus) - the
    # exception message from the failed attempt, capped in length by
    # scoring_service before it's written here. Cleared on a successful
    # retry or manual override.
    scoring_error = Column(Text, nullable=True)
    # Set whenever a round is finalized WITHOUT the candidate clicking
    # Submit - either POST /round/{n}/expire (their own browser's timer
    # hit zero) or the server-side lazy-expiry check in
    # scoring_service.close_expired_submissions (nobody's browser was
    # ever there to call expire at all - closed tab, crash, logout,
    # network loss). HR-visible only, alongside scoring_error/
    # tab_switch_count - lets HR tell "candidate submitted normally"
    # apart from "time simply ran out" at a glance, without changing
    # what gets scored (still whatever content exists, same as any
    # other submission).
    auto_closed_reason = Column(Text, nullable=True)
    # Anti-cheating: one ISO timestamp appended per detected tab-switch/
    # focus-loss during this round - purely passive logging (see app.js's
    # tab-switch guard and POST /candidate/round/{n}/tab-switch), same
    # pattern real assessment platforms use rather than interrupting the
    # candidate mid-round. HR-visible only (see tab_switch_count below /
    # SubmissionReportOut).
    tab_switch_events_json = Column(JSON, nullable=True)

    candidate = relationship("User", back_populates="submissions")
    scenario = relationship("Scenario", back_populates="submissions")
    score = relationship("Score", back_populates="submission", uselist=False)

    @property
    def tab_switch_count(self) -> int:
        return len(self.tab_switch_events_json or [])
    # created_at, not turn_number: turn_number resets per test case (see
    # ConversationTurn), so ordering by it alone would interleave test
    # cases. created_at is monotonic regardless, which is what "the
    # transcript in the order it happened" actually needs - code that
    # wants turns grouped by test case does that grouping itself, e.g.
    # scoring_service.score_round4_submission.
    conversation_turns = relationship(
        "ConversationTurn", back_populates="submission",
        order_by="ConversationTurn.created_at",
    )
    # Round 4 only: the candidate's own, self-titled test cases - see
    # Round4TestCase. Ordered by creation so the tab strip is stable.
    round4_test_cases = relationship(
        "Round4TestCase", back_populates="submission",
        order_by="Round4TestCase.created_at",
    )
    # Round 3 only (AI-prompted coding) - see Round3Turn/Round3ExecutionRun.
    round3_turns = relationship(
        "Round3Turn", back_populates="submission",
        order_by="Round3Turn.created_at",
    )
    round3_execution_runs = relationship(
        "Round3ExecutionRun", back_populates="submission",
        order_by="Round3ExecutionRun.created_at",
    )


class Score(Base):
    __tablename__ = "scores"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), unique=True, nullable=False)
    coverage_score = Column(Integer, nullable=True)  # 0-100
    misses_json = Column(JSON, default=list)         # list of missed cases/points
    # Round 1 only (empty list for rounds 2/3, which have no reference
    # type-tagging to group by - see round1_scoring.txt): per-category
    # breakdown [{category, total, covered, notes}, ...] grouped by the
    # reference set's existing Positive/Negative/Boundary/Edge tagging
    # (see round1_reference_generation.txt). Additive alongside
    # misses_json, never a replacement - misses_json stays a flat list of
    # plain strings because hr.py's scenario_history() groups it by exact
    # text match across candidates (see that function's docstring), which
    # a restructured shape would break.
    concept_coverage_json = Column(JSON, default=list)
    # Round 3 only (empty list for other rounds): the per-test-case
    # breakdown behind coverage_score - each entry is whatever the
    # reference test case had (input, expected_output, description, ...)
    # plus actual_output and passed, exactly as computed in
    # scoring_service.score_round3_submission. Was previously only
    # written into raw_llm_response_json (audit-only, never exposed to
    # the frontend) - promoted to its own column, same reasoning as
    # concept_coverage_json above, so HR's report can show which specific
    # cases passed/failed, not just the aggregate percentage.
    test_results_json = Column(JSON, default=list)
    # Round 3 only (NULL for other rounds): the sub-score breakdown
    # behind final_score, exactly as llm_service.score_round3_coding /
    # round3_coding_scoring.txt produce them - lets HR's report show
    # WHERE a candidate scored vs missed (precision of instructions,
    # pushing toward an efficient solution, working independently of the
    # assistant) rather than only the single blended final_score. Was
    # previously only written into raw_llm_response_json (audit-only,
    # never exposed to the frontend) - promoted to their own columns,
    # same reasoning as test_results_json above.
    correctness_score = Column(Integer, nullable=True)
    precision_score = Column(Integer, nullable=True)
    efficiency_score = Column(Integer, nullable=True)
    independent_judgment_score = Column(Integer, nullable=True)
    final_score = Column(Integer, nullable=True)      # 0-100
    feedback_text = Column(Text, nullable=True)
    raw_llm_response_json = Column(JSON, default=dict)  # full LLM output, for auditing
    created_at = Column(DateTime, default=datetime.utcnow)
    # Human-override audit trail (see hr.py's PATCH /submissions/{id}/score).
    # original_final_score is set once, on the FIRST override only, and
    # never touched again after that - it's always the LLM's real
    # original number even if HR overrides more than once. NULL means
    # this score has never been overridden (or never had an LLM score to
    # begin with - see the scoring_failed + manual-score path).
    original_final_score = Column(Integer, nullable=True)
    overridden_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    override_note = Column(Text, nullable=True)
    overridden_at = Column(DateTime, nullable=True)
    # Provenance (see llm_service.py's _prompt_hash / scoring_service.py's
    # _apply_provenance) - which model and which content-version of the
    # scoring prompt actually produced this score. Prompts are plain
    # hand-editable .txt files by design (see ARCHITECTURE.md), so a
    # score from last week may not match what today's prompt file would
    # produce; this is what makes "why did this candidate get 76"
    # answerable after the fact instead of only right after scoring.
    # All nullable: a score created before this column existed, or one
    # that's purely HR-hand-entered (never had a real LLM call), has none
    # of this.
    scoring_model = Column(String, nullable=True)
    scoring_prompt_file = Column(String, nullable=True)
    scoring_prompt_hash = Column(String, nullable=True)
    scored_at = Column(DateTime, nullable=True)

    submission = relationship("Submission", back_populates="score")

    @property
    def overridden_by_hr(self) -> bool:
        """Computed, not a column - schemas.ScoreOut reads this via
        from_attributes. HR only needs the fact that a human touched
        this score, not the raw user id."""
        return self.overridden_by_user_id is not None

    @property
    def specificity(self) -> dict | None:
        """Round 1 only - how concrete the candidate's test data and
        expected results were (see prompts/round1_scoring.txt's
        specificity_score/specificity_notes). Computed, not a column,
        for the same reason as evidence_audit below: it already lives
        inside raw_llm_response_json, so surfacing it needs no migration
        and no new column. None for every other round, and for any
        Round 1 score produced before this was added to the prompt."""
        scoring = (self.raw_llm_response_json or {}).get("scoring") or {}
        if not isinstance(scoring, dict) or scoring.get("specificity_score") is None:
            return None
        return {"score": scoring.get("specificity_score"), "notes": scoring.get("specificity_notes")}

    @property
    def evidence_audit(self) -> dict | None:
        """Round 4 only - the deterministic evidence-audit trail (see
        services.round4_evidence_audit.audit_round4_findings) recording
        which findings survived vs. were rejected and why. Computed, not
        a column - lives inside raw_llm_response_json (already HR-only
        via SubmissionReportOut), surfaced here as its own field so HR
        doesn't have to know that storage detail. None for every other
        round, and for any Round 4 score that predates this field."""
        return (self.raw_llm_response_json or {}).get("evidence_audit")


class Round4TestCase(Base):
    """Round 4 only: one candidate-created, self-titled automation test
    case. Deliberately no fixed category (UI/API/DB) - the candidate
    decides what to test and how many to write, which is the actual
    thing this round assesses. `title` is nullable: "Test case N" is a
    display-time fallback for an untitled one, not a stored default."""
    __tablename__ = "round4_test_cases"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False, index=True)
    title = Column(String, nullable=True)
    # Autosave target for the candidate's in-progress, unsent message -
    # see candidate.py's PATCH /round/4/test-case/{id}/draft. Cleared
    # back to "" once that text is actually sent as a turn.
    draft_prompt = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    submission = relationship("Submission", back_populates="round4_test_cases")
    turns = relationship(
        "ConversationTurn", back_populates="test_case",
        order_by="ConversationTurn.turn_number",
    )


class ConversationTurn(Base):
    """Round 4 only: each back-and-forth between candidate and the LLM,
    scoped to one of the candidate's own test cases (see Round4TestCase)."""
    __tablename__ = "conversation_turns"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False, index=True)
    test_case_id = Column(Integer, ForeignKey("round4_test_cases.id"), nullable=False, index=True)
    # 1-indexed *within this test case*, not global across the submission -
    # keeps "turn cap per test case" a plain count query and each test
    # case's transcript independently orderable.
    turn_number = Column(Integer, nullable=False)
    candidate_prompt = Column(Text, nullable=False)
    # Structured, not plain text: {response_text, steps, observed_result,
    # status} - see schemas.Round4TurnResponse / llm_service.round4_respond.
    # No code field, deliberately - the candidate reasons from an
    # execution trace (plain-English steps + what was observed), never
    # from reading an implementation. Every other LLM-output column in
    # this app (reference_json, misses_json, raw_llm_response_json) is
    # JSON for the same reason - the frontend renders these as distinct
    # pieces, it shouldn't have to parse a text blob to do that.
    model_response = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Trial feature (see routers/candidate.py's GET .../turn/{id}/code) -
    # {language: code}, populated lazily as each language is actually
    # viewed. Kept separate from model_response above, not merged into
    # it, for the same reason that field has no code of its own: this is
    # an optional, on-demand re-rendering of an already-decided response,
    # never itself part of what the candidate reasons from by default.
    # Persisted (not just cached client-side) so revisiting a turn shows
    # the SAME code every time rather than a different LLM roll, and so
    # switching back to an already-viewed language costs nothing.
    generated_code_json = Column(JSON, nullable=True)

    submission = relationship("Submission", back_populates="conversation_turns")
    test_case = relationship("Round4TestCase", back_populates="turns")


class Round3Turn(Base):
    """Round 3 only (AI-prompted coding): one candidate instruction and
    the LLM's classified response. Unlike Round 4's automation round,
    there's a single evolving code buffer per submission, not multiple
    self-titled test cases - so this is scoped straight to submission_id,
    no intermediate test-case table. See
    docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md."""
    __tablename__ = "round3_turns"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False, index=True)
    turn_number = Column(Integer, nullable=False)
    candidate_prompt = Column(Text, nullable=False)
    language = Column(String, nullable=False)
    response_kind = Column(String, nullable=False)  # "clarify" | "refuse" | "code_edit" | "direct_edit"
    response_message = Column(Text, nullable=False)
    # Full code snapshot after this turn, NOT a diff - NULL for
    # clarify/refuse turns (nothing changed). See the design spec's data
    # model section for why a full snapshot per turn is the right
    # tradeoff at this scale (one candidate, one submission, SQLite).
    code_after = Column(Text, nullable=True)
    # Cumulative snapshot of which required constructs (see
    # services/round3_constructs.py) the candidate has explicitly
    # declared as of this turn - written on every turn, not just
    # code_edit ones, so "what's currently known" is always a plain read
    # of the latest turn. NULL only for rows written before this column
    # existed. See
    # docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md.
    declared_constructs_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    submission = relationship("Submission", back_populates="round3_turns")

    def to_conversation_payload(self) -> dict:
        """The shape both the live turn-generation prompt (routers/
        candidate.py) and final scoring (services/scoring_service.py)
        feed the LLM as conversation history - kept in one place so the
        two call sites can never drift apart on what a turn looks like."""
        return {
            "turn_number": self.turn_number, "candidate_prompt": self.candidate_prompt,
            "response_kind": self.response_kind, "response_message": self.response_message,
            "code_after": self.code_after,
        }


class Round3ExecutionRun(Base):
    """Round 3 only (AI-prompted coding): one Run click's result.
    Denormalizes language/code_snapshot rather than joining through
    turn_id, so a run record is self-contained even when turn_id is null
    (a re-run of already-generated code, no new turn since)."""
    __tablename__ = "round3_execution_runs"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False, index=True)
    turn_id = Column(Integer, ForeignKey("round3_turns.id"), nullable=True)
    language = Column(String, nullable=False)
    code_snapshot = Column(Text, nullable=False)
    stdin_json = Column(JSON, default=list)
    stdout = Column(Text, nullable=True)
    stderr = Column(Text, nullable=True)
    exit_code = Column(Integer, nullable=True)
    timed_out = Column(Boolean, nullable=False, default=False)
    # Set when the hosted execution API itself failed (network error,
    # rate limit, ...) - distinct from exit_code/stderr, which are the
    # CANDIDATE's program's own output. Never treat this run's
    # stdout/stderr as a real result when this is true - see
    # scoring_service.score_round3_submission.
    infra_error = Column(Boolean, nullable=False, default=False)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    submission = relationship("Submission", back_populates="round3_execution_runs")


class CandidateAppearance(Base):
    """One row per bulk-upload event for a candidate (see
    routers/hr.py's upload endpoint and credential_service.py) - the
    source of truth for both the re-application window check and HR's
    "past appearances" history view. Exactly one row per user has
    is_current=True at any time; re-uploading an existing email flips
    the old one to False and inserts a new current one, archiving that
    user's submissions in the same operation (see Submission.archived)."""
    __tablename__ = "candidate_appearances"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    email = Column(String, nullable=False)  # snapshot of the uploaded row, not a live FK to User.email
    exam_date = Column(DateTime, nullable=False)
    is_current = Column(Boolean, nullable=False, default=True)
    # Whether THIS appearance followed the previous one within
    # Settings.reapplication_window_months - computed once at upload
    # time, not recomputed later, so it stays historically accurate even
    # if the window setting changes afterward.
    reapplied_within_window = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="appearances")


class CandidateSummary(Base):
    """The AI cross-round synthesis for one candidate (see routers/hr.py's
    GET/POST/DELETE /candidates/{id}/summary, llm_service.
    generate_candidate_summary) - used to be generated fresh on every
    click with nothing saved, which meant downloading the PDF later
    meant regenerating it (a fresh LLM call, and not even guaranteed to
    read the same as what HR reviewed on screen). One row per candidate
    (user_id is unique) - POST upserts it, GET reads it back with no LLM
    call, DELETE clears it. Not tied to a specific CandidateAppearance -
    it describes whatever the candidate's current cycle looks like right
    now; HR re-generates it after a re-application if a fresh one is
    needed, same as any other regenerate-on-demand content in this app."""
    __tablename__ = "candidate_summaries"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    # list[{"round_number": int, "did_well": list[str], "missed": list[str]}] -
    # bulleted pointers, not a paragraph (see prompts/
    # candidate_summary_generation.txt) - matches how every other feedback
    # surface in this app is laid out (score / coverage / misses, clearly
    # labeled) rather than dumping everything into undifferentiated prose.
    round_comments_json = Column(JSON, nullable=False)
    key_observations_json = Column(JSON, nullable=False)  # list[str] - cross-round highlights, bulleted
    verdict = Column(Text, nullable=False)  # short and decisive, not a paragraph
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User")


class AppSettings(Base):
    """Singleton row (id is always 1) holding the runtime-editable
    settings HR can change through the UI without a server restart -
    deliberately separate from config.py's Settings, which is
    env-sourced, deployment-level, and requires a restart to change (see
    that file's docstring for the split). See routers/hr.py's
    get_settings() for the get-or-create-singleton accessor."""
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)
    round1_passing_score = Column(Integer, nullable=False, default=70)
    round2_passing_score = Column(Integer, nullable=False, default=70)
    round3_passing_score = Column(Integer, nullable=False, default=70)
    round4_passing_score = Column(Integer, nullable=False, default=70)
    final_passing_score = Column(Integer, nullable=False, default=280)  # out of 400 (sum of the four rounds)
    reapplication_window_months = Column(Integer, nullable=False, default=6)
    # How long a candidate has, from when they started round 1 (or, if
    # they never even did that, their CandidateAppearance.exam_date), to
    # complete all four rounds - see scoring_service.
    # close_expired_assessment_windows. A round that was never even
    # started has no started_at, so nothing about per-round timeouts can
    # ever expire it on its own; without this, a candidate who simply
    # never begins the next round sits at "not_started" forever, with
    # nothing forcing a resolution. Default of 1 day means same-day
    # completion once started, by design - HR can widen this if candidates
    # are meant to spread the four rounds across more than one sitting.
    assessment_window_days = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
