"""
HR's "Round 2 practice app" workflow for a Round 1 scenario:

  start_build  -> the factory runs in the background (paid AI calls, ~5-10 min)
  status       -> building / ready / not_ready / failed, with the coverage
                  report (one row per Round 1 test case)
  approve      -> creates (or updates) the Round 2 automation scenario PAIRED
                  with this Round 1 scenario: the practice app code for every
                  language, the scorer's ground truth, and the reference sheet
                  candidates see. It goes live whenever its Round 1 does.

Storage, no migration: a small summary lives in the Round 1 scenario's
config_json["practice_app"]; the full build (three code files, design,
checklists) is written to practice_app_builds/scenario_<id>/latest.json until
it's approved - HR's scenario list returns config_json for every scenario,
and a build is ~100 KB. Approval copies it onto the Round 2 scenario, which
is where every Round 2 code path already reads its environment from.
"""
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from ... import database
from ...config import settings
from ...models import RoundStatus, Scenario, ScenarioStatus, Submission
from . import generator

log = logging.getLogger(__name__)

BUILDS_DIR = Path(__file__).resolve().parents[3] / "practice_app_builds"
# A build normally takes 5-10 minutes; one still marked "building" after this
# was interrupted (e.g. the server restarted) and may be started again.
STALE_AFTER = timedelta(minutes=30)
ESTIMATE = "about $1 and 5-10 minutes"

VALIDATION_NOTES = (
    "The practice app was generated from the Round 1 scenario and checked automatically: every reference test "
    "case listed as working ran identically in Python, JavaScript and Java. If the candidate's own test case needs "
    "something the practice app does not model (see 'Not modelled' in the ground truth), that gap is not the "
    "candidate's fault - judge only what the app could support."
)


def _build_file(scenario_id: int) -> Path:
    return BUILDS_DIR / f"scenario_{scenario_id}" / "latest.json"


def summary(scenario: Scenario) -> dict:
    return dict((scenario.config_json or {}).get("practice_app") or {"status": "none"})


def _set_summary(scenario: Scenario, data: dict) -> None:
    config = dict(scenario.config_json or {})
    config["practice_app"] = data
    scenario.config_json = config  # reassigned, so SQLAlchemy sees the JSON change


def can_start(scenario: Scenario) -> str | None:
    """Why a build can't start now, or None."""
    if scenario.round_number != 1:
        return "Practice apps are built from Round 1 scenarios."
    if not isinstance(scenario.reference_json, list) or not scenario.reference_json:
        return "This scenario has no reference test cases yet - generate or write them first."
    if settings.llm_fake_mode:
        return "Practice apps can't be built in fake AI mode - it needs the real AI."
    current = summary(scenario)
    if current.get("status") == "building":
        started = datetime.fromisoformat(current.get("started_at", "2000-01-01T00:00:00"))
        if datetime.utcnow() - started < STALE_AFTER:
            return "A practice app is already being built for this scenario."
    return None


def start_build(scenario: Scenario, db: Session) -> dict:
    data = {"status": "building", "started_at": datetime.utcnow().isoformat(timespec="seconds")}
    previous = summary(scenario)
    if previous.get("approved_round2_scenario_id"):
        data["approved_round2_scenario_id"] = previous["approved_round2_scenario_id"]
    _set_summary(scenario, data)
    db.commit()
    return data


def run_build(scenario_id: int) -> None:
    """The background job: runs the factory and records the outcome. Never
    raises - a failure is recorded for HR to see."""
    db = database.SessionLocal()
    try:
        scenario = db.get(Scenario, scenario_id)
        if scenario is None:
            return
        known_facts = _paired_round2_facts(scenario, db)
        started = time.monotonic()
        try:
            result = generator.generate(scenario.title, scenario.description, list(scenario.reference_json), known_facts)
        except Exception as e:  # generator.generate already catches; this is a last resort
            result = generator.PracticeAppResult(error=f"{type(e).__name__}: {e}")
        rows = result.coverage()
        status = "failed" if result.error else "ready" if result.ok else "not_ready"
        data = summary(scenario)
        data.update({
            "status": status,
            "finished_at": datetime.utcnow().isoformat(timespec="seconds"),
            "minutes": round((time.monotonic() - started) / 60, 1),
            "ai_calls": result.ai_calls,
            "error": result.error,
            "working": sum(r["status"] == "works" for r in rows),
            "total": len(rows),
            "coverage": rows,
            "log": result.log[-20:],
        })
        if result.plan:
            path = _build_file(scenario_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "built_at": data["finished_at"],
                "ok": result.ok,
                "plan": result.plan,
                "checklists": result.checklists,
                "env_code_by_language": result.env_code_by_language,
                "ground_truth": generator.ground_truth(result.plan),
            }, ensure_ascii=False), encoding="utf-8")
        db.refresh(scenario)
        _set_summary(scenario, data)
        db.commit()
    except Exception:
        log.exception("practice app build for scenario %s crashed", scenario_id)
        db.rollback()
    finally:
        db.close()


def _paired_round2_facts(round1: Scenario, db: Session) -> dict | None:
    """The reference sheet an existing paired Round 2 scenario already shows
    candidates, so a rebuild keeps the same accounts and data."""
    for s in db.query(Scenario).filter(Scenario.round_number == 2).all():
        config = s.config_json or {}
        if config.get("paired_round1_scenario_id") == round1.id or config.get("paired_round1_title") == round1.title:
            if s.environment_json:
                return s.environment_json
    return None


def approve(round1: Scenario, db: Session) -> Scenario:
    """Turns the latest ready build into this Round 1 scenario's paired
    Round 2 scenario. Raises ValueError with an HR-readable reason."""
    if summary(round1).get("status") != "ready":
        raise ValueError("Only a practice app that passed every check can be approved.")
    path = _build_file(round1.id)
    if not path.exists():
        raise ValueError("The build's files are missing - build it again.")
    build = json.loads(path.read_text(encoding="utf-8"))
    if not build.get("ok"):
        raise ValueError("Only a practice app that passed every check can be approved.")

    round2 = next(
        (s for s in db.query(Scenario).filter(Scenario.round_number == 2, Scenario.experience_band == round1.experience_band).all()
         if (s.config_json or {}).get("paired_round1_scenario_id") == round1.id),
        None,
    )
    if round2 is not None and _in_progress(round2, db):
        raise ValueError("Candidates are in the middle of Round 2 on this practice app - approve again once they've finished.")

    plan = build["plan"]
    sheet = plan.get("reference_sheet") or {}
    config = {
        "mode": "ai_test_automation",
        "environment_code_by_language": build["env_code_by_language"],
        "paired_round1_scenario_id": round1.id,
        "paired_round1_title": round1.title,
        "practice_app_checklists": build["checklists"],
        "practice_app_built_at": build["built_at"],
    }
    reference = {"ground_truth": build["ground_truth"], "validation_notes": VALIDATION_NOTES}
    environment = {"fields": {str(k): str(v) for k, v in (sheet.get("fields") or {}).items()},
                   "notes": str(sheet.get("notes") or "Only this data exists in the practice app.")}
    if round2 is None:
        from ...seed_round2_automation import INSTRUCTIONS  # the shared Round 2 candidate instructions
        template = db.query(Scenario).filter(Scenario.round_number == 2, Scenario.is_live.is_(True)).first()
        round2 = Scenario(
            round_number=2,
            title=f"AI-Assisted Test Automation - {round1.title}",
            description=INSTRUCTIONS,
            experience_band=round1.experience_band,
            created_by=round1.created_by,
            time_limit_minutes=template.time_limit_minutes if template else 45,
            status=ScenarioStatus.published,
            published_at=datetime.utcnow(),
            is_live=False,
        )
        db.add(round2)
    round2.config_json = config
    round2.reference_json = reference
    round2.environment_json = environment
    round2.ui_mockup_json = screens_for(plan)
    round2.environment_hr_edited = True  # never regenerated by the Round 1 resync
    db.flush()

    data = summary(round1)
    data["approved_round2_scenario_id"] = round2.id
    data["approved_at"] = datetime.utcnow().isoformat(timespec="seconds")
    _set_summary(round1, data)
    if round1.is_live:
        activate_paired_round2(round1, db)
    db.commit()
    return round2


def screens_for(plan: dict) -> dict | None:
    """The sample screens candidates see in Round 2, from the same design as
    the practice app. Anything malformed is dropped rather than shown."""
    from ...schemas import Round2AutomationUiMockupOut
    screens = []
    for screen in plan.get("screens") or []:
        if not isinstance(screen, dict) or not screen.get("name"):
            continue
        elements = [
            {"type": e.get("type"), "text": str(e.get("text") or "")}
            for e in screen.get("elements") or []
            if isinstance(e, dict) and e.get("type") in ("label", "input", "button", "link", "text")
        ]
        if elements:
            screens.append({"name": str(screen["name"]), "elements": elements})
    if not screens:
        return None
    try:
        return Round2AutomationUiMockupOut(screens=screens).model_dump()
    except Exception:
        return None


def _in_progress(round2: Scenario, db: Session) -> bool:
    return db.query(Submission).filter(
        Submission.scenario_id == round2.id,
        Submission.status == RoundStatus.in_progress,
        Submission.archived.is_(False),
    ).count() > 0


def activate_paired_round2(round1: Scenario, db: Session) -> Scenario | None:
    """When a Round 1 scenario goes live, its approved practice app goes live
    with it - unless candidates are mid-way through the current Round 2, in
    which case nothing changes (HR's Round 2 card warns about the mismatch).
    Doesn't commit."""
    if round1.round_number != 1:
        return None
    paired = next(
        (s for s in db.query(Scenario).filter(Scenario.round_number == 2, Scenario.experience_band == round1.experience_band).all()
         if (s.config_json or {}).get("paired_round1_scenario_id") == round1.id and s.status == ScenarioStatus.published),
        None,
    )
    if paired is None or paired.is_live:
        return paired
    current = db.query(Scenario).filter(
        Scenario.round_number == 2, Scenario.experience_band == round1.experience_band, Scenario.is_live.is_(True),
    ).first()
    if current is not None and _in_progress(current, db):
        return None
    if current is not None:
        current.is_live = False
    paired.is_live = True
    return paired
