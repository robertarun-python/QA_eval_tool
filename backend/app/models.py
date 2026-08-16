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


class ScenarioStatus(str, enum.Enum):
    draft = "draft"          # created, reference generated, HR still reviewing
    published = "published"   # approved and ready to be used - may or may not be the live one


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(Role), nullable=False)
    # NULL for HR users - the band only matters for candidates.
    experience_band = Column(Enum(ExperienceBand), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    submissions = relationship("Submission", back_populates="candidate")


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

    candidate = relationship("User", back_populates="submissions")
    scenario = relationship("Scenario", back_populates="submissions")
    score = relationship("Score", back_populates="submission", uselist=False)
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

    submission = relationship("Submission", back_populates="score")


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
