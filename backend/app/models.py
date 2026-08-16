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
    Column, Integer, String, Text, DateTime, ForeignKey, JSON, Enum
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
    published = "published"   # the one live scenario candidates in this round+band see
    archived = "archived"     # superseded by a newer published scenario


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

    # Draft -> published -> archived lifecycle: HR reviews the generated
    # reference before candidates can see the scenario, and only one
    # scenario per (round_number, experience_band) is ever `published`
    # at a time (enforced in the publish endpoint, not the DB).
    status = Column(Enum(ScenarioStatus), default=ScenarioStatus.draft, nullable=False)
    reference_json = Column(JSON, nullable=True)  # HR-approved "superhuman" reference answer
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
    conversation_turns = relationship(
        "ConversationTurn", back_populates="submission",
        order_by="ConversationTurn.turn_number",
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


class ConversationTurn(Base):
    """Round 3 only: each back-and-forth between candidate and the LLM."""
    __tablename__ = "conversation_turns"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False)
    turn_number = Column(Integer, nullable=False)
    candidate_prompt = Column(Text, nullable=False)
    model_response = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    submission = relationship("Submission", back_populates="conversation_turns")
