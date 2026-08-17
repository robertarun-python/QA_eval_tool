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
    # Candidate switched tabs/apps during a timed round and chose "Exit
    # test" from the forced-choice popup (see routers/candidate.py's
    # /round/{n}/abandon and app.js's tab-switch guard) rather than
    # submitting what they had. Terminal, like scored/scoring_failed -
    # _max_completed_round only advances on submitted/scored, so an
    # abandoned round leaves the candidate stuck there; it is not a
    # scoring outcome, just an early exit.
    abandoned = "abandoned"


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

    # Draft -> published lifecycle: HR reviews the generated reference
    # before candidates can see the scenario. Any number of scenarios
    # for the same (round_number, experience_band) can be `published`
    # at once (that's just "approved, in the library") - `is_live` is
    # the separate flag that says which single one of them candidates
    # actually get served, enforced in the "move to screening" endpoint.
    status = Column(Enum(ScenarioStatus), default=ScenarioStatus.draft, nullable=False)
    is_live = Column(Boolean, default=False, nullable=False)
    reference_json = Column(JSON, nullable=True)  # HR-approved "superhuman" reference answer
    # Round 3 only: auto-generated fictional test-environment reference
    # facts (credentials, a simulated API base URL, ...) shown to
    # candidates alongside the scenario description - see
    # llm_service.generate_round3_environment. Same lifecycle as
    # reference_json: generated at creation, HR can regenerate it,
    # required before publish.
    environment_json = Column(JSON, nullable=True)
    # Round 3 only: auto-generated structured reference screens (login
    # page, home page, ...) shown to candidates as a static visual
    # reference for the app they're automating - see
    # llm_service.generate_round3_ui_mockup. Structured (screens ->
    # ordered typed elements), never raw HTML, so the frontend renders it
    # through trusted CSS instead of injecting LLM-authored markup. Same
    # lifecycle as environment_json: generated at creation, HR can
    # regenerate it (together with environment_json), required before publish.
    ui_mockup_json = Column(JSON, nullable=True)
    time_limit_minutes = Column(Integer, default=30, nullable=False)
    published_at = Column(DateTime, nullable=True)

    submissions = relationship("Submission", back_populates="scenario")


class Submission(Base):
    __tablename__ = "submissions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=False)
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
    # Anti-cheating: one ISO timestamp appended per detected tab-switch/
    # focus-loss during this round (see app.js's tab-switch guard and
    # POST /candidate/round/{n}/tab-switch) - logged the instant it's
    # detected, independent of which button the candidate then picks on
    # the forced-choice popup, so even "closed the tab entirely" leaves a
    # trace. HR-visible only (see tab_switch_count below / SubmissionReportOut).
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
    # scoring_service.score_round3_submission.
    conversation_turns = relationship(
        "ConversationTurn", back_populates="submission",
        order_by="ConversationTurn.created_at",
    )
    # Round 3 only: the candidate's own, self-titled test cases - see
    # Round3TestCase. Ordered by creation so the tab strip is stable.
    round3_test_cases = relationship(
        "Round3TestCase", back_populates="submission",
        order_by="Round3TestCase.created_at",
    )


class Score(Base):
    __tablename__ = "scores"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), unique=True, nullable=False)
    coverage_score = Column(Integer, nullable=True)  # 0-100
    misses_json = Column(JSON, default=list)         # list of missed cases/points
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

    submission = relationship("Submission", back_populates="score")

    @property
    def overridden_by_hr(self) -> bool:
        """Computed, not a column - schemas.ScoreOut reads this via
        from_attributes. HR only needs the fact that a human touched
        this score, not the raw user id."""
        return self.overridden_by_user_id is not None


class Round3TestCase(Base):
    """Round 3 only: one candidate-created, self-titled automation test
    case. Deliberately no fixed category (UI/API/DB) - the candidate
    decides what to test and how many to write, which is the actual
    thing this round assesses. `title` is nullable: "Test case N" is a
    display-time fallback for an untitled one, not a stored default."""
    __tablename__ = "round3_test_cases"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False)
    title = Column(String, nullable=True)
    # Autosave target for the candidate's in-progress, unsent message -
    # see candidate.py's PATCH /round/3/test-case/{id}/draft. Cleared
    # back to "" once that text is actually sent as a turn.
    draft_prompt = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    submission = relationship("Submission", back_populates="round3_test_cases")
    turns = relationship(
        "ConversationTurn", back_populates="test_case",
        order_by="ConversationTurn.turn_number",
    )


class ConversationTurn(Base):
    """Round 3 only: each back-and-forth between candidate and the LLM,
    scoped to one of the candidate's own test cases (see Round3TestCase)."""
    __tablename__ = "conversation_turns"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False)
    test_case_id = Column(Integer, ForeignKey("round3_test_cases.id"), nullable=False)
    # 1-indexed *within this test case*, not global across the submission -
    # keeps "turn cap per test case" a plain count query and each test
    # case's transcript independently orderable.
    turn_number = Column(Integer, nullable=False)
    candidate_prompt = Column(Text, nullable=False)
    # Structured, not plain text: {response_text, steps, observed_result,
    # status} - see schemas.Round3TurnResponse / llm_service.round3_respond.
    # No code field, deliberately - the candidate reasons from an
    # execution trace (plain-English steps + what was observed), never
    # from reading an implementation. Every other LLM-output column in
    # this app (reference_json, misses_json, raw_llm_response_json) is
    # JSON for the same reason - the frontend renders these as distinct
    # pieces, it shouldn't have to parse a text blob to do that.
    model_response = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    submission = relationship("Submission", back_populates="conversation_turns")
    test_case = relationship("Round3TestCase", back_populates="turns")


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
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
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
    final_passing_score = Column(Integer, nullable=False, default=210)  # out of 300 (sum of the three rounds)
    reapplication_window_months = Column(Integer, nullable=False, default=6)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
