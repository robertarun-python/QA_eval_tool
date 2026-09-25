"""
Score calibration runner. Scores every answer in fixtures.py through the
real scoring functions (Round 3 with the pass-rate cap applied, as in
production), RUNS times each, and checks:

  ranking      strong > average > weak by at least MIN_GAP points, every round
  pass mark    strong >= PASS_MARK, weak < PASS_MARK
  consistency  the same answer's scores differ by at most MAX_SPREAD points

LIVE only on request - it calls the real, paid model (24 calls, about $1-2):
    cd backend && CALIBRATION_LIVE=1 python -m tests.calibration.run_calibration
(run from the project root with PYTHONPATH=backend:.). Without
CALIBRATION_LIVE=1 it refuses to run. tests/calibration/test_calibration_offline.py
exercises the same code with a scripted model, for free.
"""
import json
import os
import sys
from statistics import mean

from app.services import llm_service
from app.services.scoring_service import _anchor_round3_correctness
from app.seed_round2_automation import GROUND_TRUTH, VALIDATION_NOTES

from . import fixtures as f

TIERS = ("strong", "average", "weak")
RUNS = 2
PASS_MARK = 70
MIN_GAP = 10
MAX_SPREAD = 10
IN_PER_M, OUT_PER_M = 3.0, 15.0  # Claude Sonnet list prices, $ per million tokens


def score(round_name: str, tier: str) -> float:
    if round_name == "R1":
        r = llm_service.score_round1_submission(scenario_description=f.R1_SCENARIO, experience_band="0-7",
                                                reference_cases=f.R1_REFERENCE, candidate_submission=f.r1_submission(tier))
        return r["final_score"]
    if round_name == "R4":
        a = f.R4_ANSWERS[tier]
        r = llm_service.score_round2_submission(scenario_description=f.R4_SCENARIO, experience_band="0-7", reference_steps=f.R4_REFERENCE,
                                                candidate_investigation=a["investigation"], candidate_root_cause=a["root_cause"])
        return r["final_score"]
    if round_name == "R3":
        a = f.R3_ANSWERS[tier]
        r = llm_service.score_round3_coding(scenario_description=f.R3_SCENARIO, expected_approach=f.R3_APPROACH,
                                            conversation_so_far=a["conversation"], test_results=a["test_results"])
        anchor = _anchor_round3_correctness(r, a["test_results"], a["conversation"])
        return anchor["final_score"] if anchor else r["final_score"]
    if round_name == "R2":
        r = llm_service.score_round2_automation_conversation(language="python", tc_evidence=f.R2_ANSWERS[tier],
                                                             ground_truth=GROUND_TRUTH, validation_notes=VALIDATION_NOTES)
        return r["final_score"]
    raise ValueError(round_name)


def run(runs: int = RUNS) -> dict:
    """{round: {tier: [scores]}} plus a list of failed checks."""
    results = {rnd: {tier: [score(rnd, tier) for _ in range(runs)] for tier in TIERS} for rnd in ("R1", "R2", "R3", "R4")}
    failures = []
    for rnd, tiers in results.items():
        m = {t: mean(tiers[t]) for t in TIERS}
        if not (m["strong"] - m["average"] >= MIN_GAP and m["average"] - m["weak"] >= MIN_GAP):
            failures.append(f"{rnd}: ranking not clear - strong {m['strong']:.0f}, average {m['average']:.0f}, weak {m['weak']:.0f}")
        if min(tiers["strong"]) < PASS_MARK:
            failures.append(f"{rnd}: strong answer scored {min(tiers['strong'])} (below the pass mark {PASS_MARK})")
        if max(tiers["weak"]) >= PASS_MARK:
            failures.append(f"{rnd}: weak answer scored {max(tiers['weak'])} (passes the mark {PASS_MARK})")
        for t in TIERS:
            if max(tiers[t]) - min(tiers[t]) > MAX_SPREAD:
                failures.append(f"{rnd} {t}: same answer scored {tiers[t]} (spread over {MAX_SPREAD})")
    return {"scores": results, "failures": failures}


def _cost() -> float:
    calls = [c for c in llm_service.recent_calls() if c.get("input_tokens")]
    return sum(c["input_tokens"] / 1e6 * IN_PER_M + (c.get("output_tokens") or 0) / 1e6 * OUT_PER_M for c in calls)


if __name__ == "__main__":
    if os.environ.get("CALIBRATION_LIVE") != "1":
        sys.exit("Refusing: this calls the real, paid model. Set CALIBRATION_LIVE=1 once a person has approved the run.")
    out = run()
    out["cost_usd"] = round(_cost(), 4)
    print(json.dumps(out, indent=1))
    sys.exit(1 if out["failures"] else 0)
