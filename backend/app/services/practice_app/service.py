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
import hashlib
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

VALIDATION_NOTES = (
    "The practice app was generated from the Round 1 scenario and checked automatically: every reference test "
    "case listed as working ran identically in Python, JavaScript and Java. If the candidate's own test case needs "
    "something the practice app does not model (see 'Not modelled' in the ground truth), that gap is not the "
    "candidate's fault - judge only what the app could support."
)


def validation_notes(unverified: list[dict]) -> str:
    """What the Round 2 scorer is told about the practice app - including any
    Round 1 test case that couldn't be verified against it, so a candidate who
    automates one of those isn't marked down for the app's gap."""
    if not unverified:
        return VALIDATION_NOTES
    lines = [f"- {r['title']}" + (f" ({'; '.join(r.get('details') or [])[:200]})" if r.get("details") else "") for r in unverified]
    return (VALIDATION_NOTES + "\n\nThese Round 1 test cases could NOT be verified against the practice app - it may "
            "not behave as they expect. If the candidate automates one of them and the app misbehaves, that is the "
            "app's gap, not the candidate's:\n" + "\n".join(lines))


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
    data = {"status": "building", "started_at": datetime.utcnow().isoformat(timespec="seconds"), "step": 0, "step_detail": ""}
    previous = summary(scenario)
    if previous.get("approved_round2_scenario_id"):
        data["approved_round2_scenario_id"] = previous["approved_round2_scenario_id"]
    # Kept for _reusable_build: this summary is about to replace the previous
    # one, and builds saved before "unsupported" was stored only had it here.
    data["previous_unsupported"] = [{"title": r["title"], "reason": (r.get("details") or [""])[0]}
                                    for r in previous.get("coverage") or [] if r.get("status") == "not supported"]
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

        def progress(step: int, detail: str = "") -> None:
            # Saved as the build goes, so HR's screen can show which step it's on.
            data = summary(scenario)
            data.update({"step": step, "step_detail": detail, "updated_at": datetime.utcnow().isoformat(timespec="seconds")})
            _set_summary(scenario, data)
            db.commit()

        try:
            result = generator.generate(scenario.title, scenario.description, list(scenario.reference_json), known_facts,
                                        progress=progress, reuse=_reusable_build(scenario))
        except Exception as e:  # generator.generate already catches; this is a last resort
            result = generator.PracticeAppResult(error=f"{type(e).__name__}: {e}")
        rows = result.coverage()
        # "ready" also when not every test case could be verified but at least
        # generator.APPROVE_AT were: HR sees which weren't ("unverified") and decides.
        status = "failed" if result.error else "ready" if (result.ok or result.approvable) else "not_ready"
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
            "unverified": [r for r in rows if r["status"] != "works"] if status == "ready" else [],
            "approval_problem": None if (result.error or result.ok) else result.approval_problem(),
            "log": result.log[-20:],
        })
        if result.plan:
            path = _build_file(scenario_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            saved = json.dumps({
                "built_at": data["finished_at"],
                "ok": result.ok,
                "approvable": result.ok or result.approvable,
                "plan": result.plan,
                "checklists": result.checklists,
                "unsupported": result.unsupported,
                "reference_hash": _reference_hash(scenario),
                "env_code_by_language": result.env_code_by_language,
                "ground_truth": generator.ground_truth(result.plan),
                "log": result.log,
                "coverage": rows,
            }, ensure_ascii=False)
            path.write_text(saved, encoding="utf-8")
            # Every build kept, not just the latest: the failed ones are what the
            # factory is tested against (tests/test_practice_app_real_builds.py),
            # and a Beneficiary build that had got to 24/25 was lost by overwriting.
            archive = path.parent / "history" / f"{data['finished_at'].replace(':', '')}.json"
            archive.parent.mkdir(exist_ok=True)
            archive.write_text(saved, encoding="utf-8")
        db.refresh(scenario)
        _set_summary(scenario, data)
        db.commit()
    except Exception:
        log.exception("practice app build for scenario %s crashed", scenario_id)
        db.rollback()
    finally:
        db.close()


def _reference_hash(scenario: Scenario) -> str:
    return hashlib.sha256(json.dumps(scenario.reference_json, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _reusable_build(scenario: Scenario) -> dict | None:
    """The last build's design, checklists and Python app, for the factory to
    reuse if that Python still passes (see generator.generate) - but only if
    it was built from the same Round 1 test cases. A build saved before the
    hash was recorded counts only for a published scenario, whose test cases
    can no longer change."""
    path = _build_file(scenario.id)
    if not path.exists():
        return None
    try:
        build = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    saved_hash = build.get("reference_hash")
    if saved_hash != _reference_hash(scenario) and not (saved_hash is None and scenario.status == ScenarioStatus.published):
        return None
    python = (build.get("env_code_by_language") or {}).get("python")
    if not (build.get("plan") and build.get("checklists") and python):
        return None
    unsupported = build.get("unsupported")
    if unsupported is None:  # older builds kept these only in the summary start_build replaced
        unsupported = summary(scenario).get("previous_unsupported") or []
    return {"plan": build["plan"], "checklists": build["checklists"], "unsupported": unsupported, "python": python}


def _paired_round2_facts(round1: Scenario, db: Session) -> dict | None:
    """The reference sheet an existing paired Round 2 scenario already shows
    candidates, so a rebuild keeps the same accounts and data."""
    for s in sorted(db.query(Scenario).filter(Scenario.round_number == 2).all(), key=lambda s: -s.id):  # newest first
        config = s.config_json or {}
        if config.get("paired_round1_scenario_id") == round1.id or config.get("paired_round1_title") == round1.title:
            if s.environment_json:
                return s.environment_json
    return None


def approve(round1: Scenario, db: Session) -> Scenario:
    """Turns the latest ready build into this Round 1 scenario's paired
    Round 2 scenario. Raises ValueError with an HR-readable reason."""
    not_ready = (f"Only a practice app with at least {generator.APPROVE_AT:.0%} of the test cases verified in every "
                 "language can be approved.")
    if summary(round1).get("status") != "ready":
        raise ValueError(not_ready)
    path = _build_file(round1.id)
    if not path.exists():
        raise ValueError("The build's files are missing - build it again.")
    build = json.loads(path.read_text(encoding="utf-8"))
    if not (build.get("ok") or build.get("approvable")):
        raise ValueError(not_ready)
    unverified = [r for r in build.get("coverage") or [] if r.get("status") != "works"]

    band_round2s = db.query(Scenario).filter(Scenario.round_number == 2, Scenario.experience_band == round1.experience_band).all()
    paired = [s for s in band_round2s if (s.config_json or {}).get("paired_round1_scenario_id") == round1.id]
    round2 = max(paired, key=lambda s: s.id) if paired else None
    live = next((s for s in band_round2s if s.is_live), None)
    for busy in {s.id: s for s in (round2, live) if s is not None}.values():
        # Approving switches Round 2 when this Round 1 is live - never under a
        # candidate who's mid-way through it (it used to skip the switch
        # silently while the card kept saying "ready to approve").
        if (busy is round2 or round1.is_live) and _in_progress(busy, db):
            raise ValueError("Candidates are in the middle of Round 2 right now - approve once they've finished, "
                             "so nothing changes under them.")
    if round2 is not None and _has_submissions(round2, db):
        # Candidates already took this version: their results stay tied to the
        # app they actually used. The new build becomes a new Round 2 version.
        round2 = None

    plan = build["plan"]
    sheet = plan.get("reference_sheet") or {}
    config = {
        "mode": "ai_test_automation",
        "environment_code_by_language": build["env_code_by_language"],
        "paired_round1_scenario_id": round1.id,
        "paired_round1_title": round1.title,
        "practice_app_checklists": build["checklists"],
        "practice_app_built_at": build["built_at"],
        # What Round 1 shows of this app (routers/candidate._round1_environment_view):
        # the web address and the design's main test account - nothing built
        # from the Round 1 answer key.
        "round1_sheet": _round1_sheet(plan),
    }
    reference = {"ground_truth": build["ground_truth"], "validation_notes": validation_notes(unverified)}
    environment = {"fields": {str(k): str(v) for k, v in (sheet.get("fields") or {}).items()},
                   "notes": str(sheet.get("notes") or "Only this data exists in the practice app.")}
    if round2 is None:
        from ...seed_round2_automation import INSTRUCTIONS  # the shared Round 2 candidate instructions
        template = next((s for s in band_round2s if s.is_live), None)
        version = f" (version {len(paired) + 1})" if paired else ""
        round2 = Scenario(
            round_number=2,
            title=f"AI-Assisted Test Automation - {round1.title}{version}",
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


def _round1_sheet(plan: dict) -> dict:
    sheet = {}
    if plan.get("base_url"):
        sheet["Web address"] = str(plan["base_url"])
    account = next(iter(plan.get("test_accounts") or []), None) or {}
    if account.get("login"):
        sheet["Test login"] = str(account["login"])
    if account.get("password"):
        sheet["Password"] = str(account["password"])
    return sheet


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


def _has_submissions(round2: Scenario, db: Session) -> bool:
    return db.query(Submission).filter(Submission.scenario_id == round2.id, Submission.archived.is_(False)).count() > 0


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
    versions = [s for s in db.query(Scenario).filter(Scenario.round_number == 2, Scenario.experience_band == round1.experience_band).all()
                if (s.config_json or {}).get("paired_round1_scenario_id") == round1.id and s.status == ScenarioStatus.published]
    paired = max(versions, key=lambda s: s.id) if versions else None  # the newest version (see approve)
    if paired is None or paired.is_live:
        return paired
    current = db.query(Scenario).filter(
        Scenario.round_number == 2, Scenario.experience_band == round1.experience_band, Scenario.is_live.is_(True),
    ).first()
    if current is not None and _in_progress(current, db):
        return None
    if current is not None:
        current.is_live = False
        db.flush()  # the old one off before the new one on - one live per round and band (see models.py)
    paired.is_live = True
    return paired
