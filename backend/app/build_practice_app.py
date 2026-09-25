"""
Builds a Round 2 practice app for a Round 1 scenario with the practice-app
factory (services/practice_app/generator.py) and saves everything to a
review folder. It USES PAID AI CALLS (typically 5, at most about 11) and
changes nothing in the database - it's for trying the factory out and for
reviewing a result before it's used.

    cd backend && python -m app.build_practice_app --round1-scenario 1 --yes

Output folder (default practice_app_builds/<scenario id>_<time>/): the
generated app in each language, its design, the checklists, the coverage
report, and the scorer's ground truth.
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from . import database
from .models import Scenario
from .services.practice_app import generator

_EXTENSIONS = {"python": "py", "javascript": "js", "java": "java"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--round1-scenario", type=int, required=True, help="id of the Round 1 scenario")
    parser.add_argument("--out", type=Path, help="folder to save the result in")
    parser.add_argument("--yes", action="store_true", help="confirm this makes paid AI calls")
    args = parser.parse_args(argv)

    db = database.SessionLocal()
    try:
        scenario = db.get(Scenario, args.round1_scenario)
        if scenario is None or scenario.round_number != 1:
            print(f"No Round 1 scenario with id {args.round1_scenario}.")
            return 2
        cases = scenario.reference_json if isinstance(scenario.reference_json, list) else []
        title, description = scenario.title, scenario.description
    finally:
        db.close()
    if not cases:
        print("That scenario has no reference test cases yet - generate or write them first.")
        return 2

    print(f"Round 1 scenario: {title} ({len(cases)} reference test cases)")
    if not args.yes:
        print("This makes paid AI calls (typically 5, at most about 11). Re-run with --yes to go ahead.")
        return 1

    started = time.monotonic()
    result = generator.generate(title, description, cases)
    minutes = (time.monotonic() - started) / 60

    out = args.out or Path("practice_app_builds") / f"{args.round1_scenario}_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    for language, code in result.env_code_by_language.items():
        (out / f"practice_app.{_EXTENSIONS[language]}").write_text(code, encoding="utf-8")
    (out / "design.json").write_text(json.dumps(result.plan, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "checklists.json").write_text(json.dumps(result.checklists, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "coverage.json").write_text(json.dumps(result.coverage(), indent=1, ensure_ascii=False), encoding="utf-8")
    if result.plan:
        (out / "ground_truth.txt").write_text(generator.ground_truth(result.plan), encoding="utf-8")
    (out / "log.txt").write_text("\n".join(result.log), encoding="utf-8")

    rows = result.coverage()
    works = sum(r["status"] == "works" for r in rows)
    print(f"\n{'READY' if result.ok else 'NOT READY'} - {works} of {len(rows)} test cases work in every language "
          f"({result.ai_calls} AI calls, {minutes:.1f} min)")
    if result.error:
        print(f"Stopped: {result.error}")
    for row in rows:
        if row["status"] != "works":
            print(f"  [{row['status']}] {row['title']}: {'; '.join(row['details'])}")
    print(f"\nSaved to {out}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
