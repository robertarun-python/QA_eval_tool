"""
Installs a practice-app build that was already paid for (a measurement run's
saved JSON) as a Round 1 scenario's latest build, re-checked with today's
engine in Python, JavaScript and Java. No AI call is made - nothing is paid.
HR then sees it on the scenario's card and approves it as usual.

    .venv/bin/python tools/install_recorded_build.py <run.json> <scenario-id>

Run it from the project the server uses; back up backend/qa_eval.db first.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.models import Scenario  # noqa: E402
from app.services import llm_service  # noqa: E402
from app.services.practice_app import service  # noqa: E402


def recorded_from(run: dict) -> dict:
    unsupported = [{"title": u.get("title", ""), "reason": "; ".join(u.get("details") or []) or str(u.get("reason", ""))}
                   for u in run.get("not_supported") or [] if isinstance(u, dict)]
    return {"plan": run["plan"], "checklists": run["checklists"], "unsupported": unsupported}


def main() -> int:
    run_file, scenario_id = Path(sys.argv[1]), int(sys.argv[2])
    run = json.loads(run_file.read_text(encoding="utf-8"))
    db = database.SessionLocal()
    try:
        scenario = db.get(Scenario, scenario_id)
        if scenario is None or scenario.round_number != 1:
            print(f"scenario {scenario_id} is not a Round 1 scenario")
            return 1
        if run.get("title") and run["title"].strip() != scenario.title.strip():
            print(f"this build is for {run['title']!r}, not {scenario.title!r}")
            return 1
        if (scenario.config_json or {}).get("practice_app", {}).get("status") == "building":
            print("a build is running for this scenario - try again when it ends")
            return 1
    finally:
        db.close()

    def no_ai(*args, **kwargs):
        raise RuntimeError("install_recorded_build never calls the AI")
    llm_service._send = no_ai  # belt and braces: the install path makes no AI call
    data = service.install_recorded(scenario_id, recorded_from(run))
    print(f"{scenario_id}: {data.get('status')} - {data.get('working')}/{data.get('total')} test cases work"
          + (f"; {data['error']}" if data.get("error") else ""))
    return 0 if data.get("status") == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
