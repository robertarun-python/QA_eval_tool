"""
ROUND 5 -- PROGRESSIVE ENGINEERING (POC, Phase 1 of this round's own
build: data model + migration only - see migrate_progressive_coding.py).

Self-contained vertical slice, deliberately separate from models.py
rather than appended to it: nothing in Rounds 1-4 references this
module, and nothing here is wired into the outer ROUND_SEQUENCE gate in
routers/candidate.py - this round's own internal stage progression
(Stage 1 -> 2 -> 3 -> ...) is a distinct, not-yet-built concern from
that outer round-to-round gating. See this session's Round 5
architecture-discovery notes for the full design.

GENERIC BY DESIGN: nothing below is CSV-specific. input_spec_json on
ProgressiveProblem is a domain-neutral JSON blob precisely so the same
tables describe a CSV problem, a JSON-processing problem, a log-analysis
problem, or an API-response problem without any schema change.

Reuses two existing enums from models.py by import (ExperienceBand,
RoundStatus) - not a modification of that file, just picking up an
already-established vocabulary (the same "0-7"/"7+" bands and
in_progress/submitted/scored/scoring_failed lifecycle every other round
already uses) rather than inventing a second, divergent one. Everything
else in this module is new and self-contained.

FUTURE-CONTENT ISOLATION (the security requirement from the
architecture discovery): ProgressiveAiTurn has no field for anything
beyond the CURRENT stage's own turn - there is nowhere on this table (or
ProgressiveAttempt) to even accidentally store a future requirement's
text, a hidden test, or a reference solution. Those live ONLY on
ProgressiveRequirement, one row per stage, queried by whatever future
service code exists to build a prompt - never duplicated onto a
candidate-facing record. This phase adds no such service code (no
routes, no LLM calls) - the isolation guarantee here is structural (the
schema has nowhere to put that content), enforced properly by future
phases' query logic, not by this phase's existence alone.
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint, Enum as SAEnum,
)
from sqlalchemy.orm import relationship

from .database import Base
from .models import ExperienceBand, RoundStatus


class ProgressiveProblemStatus(str, enum.Enum):
    draft = "draft"          # HR still authoring/reviewing requirements
    published = "published"   # approved - a candidate may be given an attempt


class ProgressiveProblem(Base):
    """One progressive-engineering problem: an initial framing plus a
    domain-neutral input description. The actual progression lives in
    ProgressiveRequirement, one row per stage - a problem is never
    single-stage on its own."""
    __tablename__ = "progressive_problems"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    # Domain-neutral by design (see module docstring) - e.g. for a CSV
    # problem: {"kind": "csv", "filename": "...", "columns": [...]}; for
    # a JSON-processing problem: {"kind": "json", "schema": {...}}; for a
    # log-analysis problem: {"kind": "log", "format": "...", ...}. No
    # kind-specific columns on this table on purpose - see "Generic input
    # design" in the architecture discovery notes.
    input_spec_json = Column(JSON, nullable=False, default=dict)
    language = Column(String, nullable=False)
    starter_code = Column(Text, nullable=True)
    # Free text, not an enum: difficulty vocabulary isn't settled yet and
    # nothing gates on it in this phase - avoids locking in a taxonomy
    # this POC doesn't need to enforce.
    difficulty = Column(String, nullable=True)
    time_limit_minutes = Column(Integer, nullable=False, default=30)
    experience_band = Column(SAEnum(ExperienceBand), nullable=True, default=ExperienceBand.both)
    status = Column(SAEnum(ProgressiveProblemStatus), nullable=False, default=ProgressiveProblemStatus.draft)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True)

    requirements = relationship(
        "ProgressiveRequirement", back_populates="problem",
        order_by="ProgressiveRequirement.stage_order",
    )
    attempts = relationship("ProgressiveAttempt", back_populates="problem")


class ProgressiveRequirement(Base):
    """One stage's requirement for a ProgressiveProblem. hidden_tests_json
    and reference_solution are server/HR-side only - see module docstring
    on future-content isolation; nothing anywhere in this schema copies
    them onto a candidate-facing record. `frozen` is the integrity gate
    (see freeze rules in the architecture discussion): once a problem is
    published, or any attempt exists against it, every requirement row
    for that problem must be frozen before anything about it can be
    considered safe to hand to a candidate - this phase defines the
    column only; enforcing WHEN it flips is a service-layer concern for
    a later phase (no service code exists yet in this phase)."""
    __tablename__ = "progressive_requirements"
    __table_args__ = (
        UniqueConstraint("problem_id", "stage_order", name="uq_progressive_requirement_problem_stage"),
        CheckConstraint("stage_order > 0", name="ck_progressive_requirement_stage_order_positive"),
    )

    id = Column(Integer, primary_key=True)
    problem_id = Column(Integer, ForeignKey("progressive_problems.id"), nullable=False, index=True)
    stage_order = Column(Integer, nullable=False)
    requirement_text = Column(Text, nullable=False)
    # Nullable, same reasoning as Scenario.reference_json: filled in
    # during HR's draft/review pass, required before the requirement can
    # be frozen/published - not required at row-creation time.
    expected_behavior = Column(Text, nullable=True)
    hidden_tests_json = Column(JSON, nullable=True)
    reference_solution = Column(Text, nullable=True)
    frozen = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True)

    problem = relationship("ProgressiveProblem", back_populates="requirements")


class ProgressiveAttempt(Base):
    """One candidate's attempt at a ProgressiveProblem - the multi-stage
    analogue of Submission. `current_stage` is the trusted-state pointer
    a future Policy Core (not built yet) would read to decide what the
    candidate is even allowed to see or do next. `archived`, same
    soft-reset convention as Submission.archived - a reset attempt is
    never hard-deleted."""
    __tablename__ = "progressive_attempts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    problem_id = Column(Integer, ForeignKey("progressive_problems.id"), nullable=False, index=True)
    current_stage = Column(Integer, nullable=False, default=1)
    status = Column(SAEnum(RoundStatus), nullable=False, default=RoundStatus.in_progress)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    archived = Column(Boolean, nullable=False, default=False)

    problem = relationship("ProgressiveProblem", back_populates="attempts")
    stage_results = relationship(
        "ProgressiveStageResult", back_populates="attempt",
        order_by="ProgressiveStageResult.stage_order",
    )
    ai_turns = relationship(
        "ProgressiveAiTurn", back_populates="attempt",
        order_by="ProgressiveAiTurn.id",
    )


class ProgressiveStageResult(Base):
    """One stage's immutable outcome for one attempt - a full code
    snapshot (same tradeoff as Round3Turn.code_after: a diff-of-diffs
    isn't worth the complexity at this scale), plus whatever test run
    eventually produced test_results_json/passed (no scoring service
    exists yet in this phase - those two columns are written by nothing
    right now, only exercised directly by this phase's own tests).

    The unique(attempt_id, stage_order) constraint is the actual
    immutability guarantee, not just a naming convention: a stage can
    only ever have ONE result row per attempt. A future resubmission
    mechanism, if ever needed, would have to be an explicit, auditable
    action (e.g. archiving the attempt and starting a new one, the same
    pattern Submission.archived already uses) - never an UPDATE to an
    existing stage result."""
    __tablename__ = "progressive_stage_results"
    __table_args__ = (
        UniqueConstraint("attempt_id", "stage_order", name="uq_progressive_stage_result_attempt_stage"),
    )

    id = Column(Integer, primary_key=True)
    attempt_id = Column(Integer, ForeignKey("progressive_attempts.id"), nullable=False, index=True)
    stage_order = Column(Integer, nullable=False)
    code_snapshot = Column(Text, nullable=False)
    test_results_json = Column(JSON, nullable=True)
    passed = Column(Boolean, nullable=True)
    submitted_at = Column(DateTime, default=datetime.utcnow)

    attempt = relationship("ProgressiveAttempt", back_populates="stage_results")


class ProgressiveAiTurn(Base):
    """One AI interaction within one stage of one attempt - the
    progressive-round analogue of Round3Turn, but carrying the Phase
    1/2 POC audit fields from the start rather than bolted on later (see
    poc_ai_output_detector.DetectorResult / poc_ai_judge.JudgeResult,
    which this table's detector_*/judge_* columns mirror the shape of -
    this table does not import either POC module; it just has columns
    shaped to hold their output, kept as plain strings/JSON so this
    model has zero import-time dependency on either POC component).

    Deliberately has NO field for anything beyond this stage - see the
    module docstring's Future-content isolation note. There is nowhere
    on this row to put a future requirement's text, a hidden test, or a
    reference solution, even by mistake."""
    __tablename__ = "progressive_ai_turns"

    id = Column(Integer, primary_key=True)
    attempt_id = Column(Integer, ForeignKey("progressive_attempts.id"), nullable=False, index=True)
    stage_order = Column(Integer, nullable=False)
    candidate_prompt = Column(Text, nullable=False)
    # Set only when this turn answers a prior clarifying question - see
    # poc_ai_judge.JudgeInput.effective_candidate_instruction, which this
    # mirrors. Optional: most turns aren't a clarify-answer.
    effective_candidate_instruction = Column(Text, nullable=True)
    generated_response = Column(Text, nullable=False)
    detector_status = Column(String, nullable=True)
    detector_reason_codes = Column(JSON, nullable=True)
    judge_verdict = Column(String, nullable=True)
    judge_reason_codes = Column(JSON, nullable=True)
    judge_severity = Column(String, nullable=True)
    # The GENERATOR's true raw output - audit ground truth, kept even
    # when the pipeline (Phase 5) ends up rejecting it. Never shown to
    # the candidate on its own; see candidate_facing_response below for
    # what they actually saw.
    code_after = Column(Text, nullable=True)
    # What the candidate was ACTUALLY shown for this turn - identical to
    # response_message/code_after on a Judge PASS, but a safe canned
    # message (with code_after left None) on a policy refusal or a Judge
    # FAIL/UNCERTAIN. Kept separate from generated_response so an audit
    # view can show HR both "what really happened" and "what the
    # candidate saw" without conflating the two - added in Phase 5 (the
    # pipeline), where a real generator output can legitimately differ
    # from what's safe to release.
    candidate_facing_response = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    attempt = relationship("ProgressiveAttempt", back_populates="ai_turns")
