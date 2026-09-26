"""
Which scenario a candidate takes for each round - one place, used by the
candidate pages (routers/candidate.py) and by the assessment-window close
(scoring_service.close_expired_assessment_windows), so the two can't
disagree about a candidate's Round 2.
"""
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from ..models import Scenario, ScenarioStatus, Submission, User


def paired_with(round2: Scenario) -> tuple[int | None, str | None]:
    """The Round 1 scenario a Round 2 practice app was built for (by id, or by
    title for the hand-written seeded environments)."""
    config = round2.config_json or {}
    return config.get("paired_round1_scenario_id"), config.get("paired_round1_title")


def round2_for_round1(db: Session, round1: Scenario | None) -> Scenario | None:
    """The Round 2 scenario a candidate who answered this Round 1 scenario
    takes: the practice app built for it (the live version first, else the
    newest), else a live Round 2 that isn't built for any particular Round 1.
    None when the only Round 2 around was built for a different scenario -
    automating these test cases against another app would be meaningless, so
    Round 2 shows as not available until HR approves this scenario's app.
    Filtered in the database: each practice app carries ~100 KB of code, and
    this runs on every candidate page load."""
    if round1 is None:
        return None
    paired_id = func.json_extract(Scenario.config_json, "$.paired_round1_scenario_id")
    paired_title = func.json_extract(Scenario.config_json, "$.paired_round1_title")
    candidates = (
        db.query(Scenario)
        .filter(
            Scenario.round_number == 2, Scenario.experience_band == round1.experience_band,
            Scenario.status == ScenarioStatus.published,
            or_(paired_id == round1.id, and_(paired_id.is_(None), paired_title == round1.title), Scenario.is_live.is_(True)),
        )
        .all()
    )

    def built_for_this(s: Scenario) -> bool:
        pid, ptitle = paired_with(s)
        return pid == round1.id if pid else bool(ptitle) and ptitle == round1.title

    paired = [s for s in candidates if built_for_this(s)]
    if paired:
        return max(paired, key=lambda s: (s.is_live, s.id))
    live = next((s for s in candidates if s.is_live), None)
    if live is not None and paired_with(live) == (None, None):
        return live
    return None


def candidate_scenario(db: Session, candidate: User, round_number: int) -> Scenario | None:
    """The scenario this candidate takes for this round. A round they've
    already started stays on the scenario they started, even after HR makes
    another one live - otherwise switching scenarios would drop their work
    and let them take a finished round again. Round 2 is the practice app
    built for THEIR Round 1 scenario (round2_for_round1), never whichever
    Round 2 happens to be live."""
    started = (
        db.query(Submission)
        .filter(Submission.user_id == candidate.id, Submission.round_number == round_number, Submission.archived.is_(False))
        .first()
    )
    if started is not None:
        return db.get(Scenario, started.scenario_id)
    if round_number == 2:
        round1 = (
            db.query(Submission)
            .filter(Submission.user_id == candidate.id, Submission.round_number == 1, Submission.archived.is_(False))
            .first()
        )
        if round1 is not None:
            return round2_for_round1(db, db.get(Scenario, round1.scenario_id))
    return (
        db.query(Scenario)
        .filter(
            Scenario.round_number == round_number,
            Scenario.experience_band == candidate.experience_band,
            Scenario.is_live.is_(True),
        )
        .first()
    )
