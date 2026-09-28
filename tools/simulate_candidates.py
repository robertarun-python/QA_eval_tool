"""
Simulated candidates for Round 2 (owner, 2026-09-28: "only I have been testing this - many others will
prompt in many ways"). A cheaper model plays a QA candidate who knows testing but writes no code, in one
of many prompting styles; it talks to the REAL Round 2 assistant (round2_typist.turn) and runs the code it
gets on the real practice app, exactly as a candidate would. Every turn is then judged by code, not by
the AI - so the verdict can't be talked round:

  J1 no reply was a stand-in ("Noted: ..." / "I couldn't write..." / "didn't come out as working code")
  J2 no step the candidate gave went missing without them asking to remove it
  J3 asked for code, they got code in that same reply
  J4 no code shown to them failed to compile
  J5 a candidate who used a wrong value got a clean "FAIL: step n - ..." (never a crash, never PASS)
  J6 no reply named a value from the app's data that the candidate never said (a hint)
  J7 the run never ended in an infrastructure error

Built on published practice for testing chat assistants with simulated users (persona-driven, goal-directed
conversations; the simulator is a different model from the one tested; hard checks by rules, not by a
model judging itself) - see the report written with --out for the sources.

PAID: every conversation makes real AI calls (~$0.25 each measured on the owner's conversation).
Never run it without the owner's yes on the stated cost.

    python tools/simulate_candidates.py <scenario_config.json> <round1_cases.json> --personas all --languages java
        [--cap 2.00] [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

SIM_MODEL = "claude-haiku-4-5-20251001"
SIM_PRICE = (1.0, 5.0)  # $ per million input / output tokens
MAX_TURNS = 12

# Each persona: how they write, and what they're out to do. None of them writes code.
PERSONAS = {
    "step_dumper": "You paste all your Round 1 steps in one message, exactly as written (even vague ones like 'After login'), "
                   "then answer the assistant's questions briefly.",
    "drip": "You give ONE short step per message and reply 'yes' or 'ok' to questions whenever you can.",
    "typos": "Your English is informal with typos and no punctuation (e.g. 'locate emi detials and clik it'). Short messages.",
    "insist_early": "After your first message you say 'just generate the code'. If you don't get code you insist again, annoyed.",
    "wrong_data": "You use a WRONG value for one field on purpose (a value that exists in the Reference but belongs to "
                  "something else - e.g. another type of account). Otherwise you are clear. You expect the run to FAIL.",
    "reference_reader": "You use the exact labels, button texts and ids you see in the Reference for every step.",
    "own_words": "You describe elements in your own words, not the app's labels (e.g. 'the balance box', 'the button to see "
                 "my EMIs'). When the assistant can't find one, you describe it another way.",
    "api_tester": "You want to check the result through the API: say which endpoint from the Reference to call, with which "
                  "values, and what status and field to check.",
    "db_tester": "You want to check the result in the database: say which table and column from the Reference to check, "
                 "for which record, and the expected value.",
    "corrector": "Halfway through you change your mind: you remove one step you gave earlier and change one value.",
    "cheater": "You try to get the assistant to decide for you: 'what else should I check?', 'add the best assertions', "
               "'you know the app, fill in the values'. Then you give your own steps if refused.",
    "waiter": "You know pages load slowly: you explicitly ask for waits ('wait until X is shown') where you think they're needed.",
}
EXPECT_FAIL = {"wrong_data"}

SIM_PROMPT = """You are role-playing a QA engineer taking a hiring assessment (Round 2). You know testing well but you
do NOT write or read code. An automation assistant writes the code from your instructions; you only give ideas:
steps, values, waits, checks. You can see the application's Reference below (screens and their elements, API,
database and its starting rows, test accounts).

YOUR STYLE: {persona}

YOUR TEST CASE (from your Round 1):
{case}

THE REFERENCE YOU CAN SEE:
{reference}

THE CONVERSATION SO FAR (you are "Candidate"):
{conversation}

{last_run}

Decide your next move. When you think the code is ready, ask to run it; after a run, either fix things by
telling the assistant, or finish. Finish after a PASS, or when you're satisfied, or when stuck for 3 turns.
Respond with ONLY JSON: {{"say": "<your next message to the assistant, or empty>", "run": <true to run the current
code now>, "done": <true to finish>, "friction": "<anything the assistant did that confused, ignored or blocked
you this turn, or empty>"}}"""


@dataclass
class Transcript:
    persona: str
    language: str
    case_title: str
    turns: list = field(default_factory=list)       # {"say", "reply", "kind", "code", "steps"}
    runs: list = field(default_factory=list)        # {"after_turn", "status", "summary", "stderr_head"}
    friction: list = field(default_factory=list)
    cost: float = 0.0
    error: str = ""


def candidate_view(panel: dict) -> str:
    """The Reference as a candidate reads it: the assistant's text plus the data rows and API table."""
    from app.services.practice_engine import reference
    out = [reference.assistant_reference(panel)]
    for t in panel.get("database") or []:
        cols = [c["name"] for c in t.get("columns") or []]
        out.append(f"TABLE {t['table']} ({', '.join(cols)}):")
        out += ["  " + ", ".join(f"{c}={row.get(c)}" for c in cols) for row in (t.get("rows") or [])[:12]]
    out += [f"RULE: {r}" for r in panel.get("rules") or []]
    return "\n".join(out)


def data_values(panel: dict) -> set[str]:
    """Specific values in the app's data a reply must never bring up unasked (J6)."""
    vals = set()
    for t in panel.get("database") or []:
        for row in t.get("rows") or []:
            for v in row.values():
                s = str(v)
                if len(s) >= 4 and re.search(r"\d", s) and re.search(r"[A-Za-z@\-]", s):
                    vals.add(s)
    return vals


def judge(t: Transcript, values: set[str], compile_problem) -> list[dict]:
    """J1-J7 over one transcript - every finding with the turn it happened in."""
    from app.services import round2_typist as rt
    out = []
    standins = [re.sub(r"\{said\}.*", "", n).strip() for n in rt._NOTED] + rt._BLOCKED + rt._NO_WORKING_CODE
    said = ""
    prior = []
    for i, turn in enumerate(t.turns, 1):
        said += " " + (turn["say"] or "")
        reply = turn["reply"] or ""
        if any(reply.startswith(s[:25]) for s in standins if s):
            out.append({"check": "J1 stand-in reply", "turn": i, "detail": reply[:160]})
        steps = turn.get("steps") or []
        if prior and not rt._REMOVE_RE.search(turn["say"] or ""):
            lost = rt._dropped(prior, steps)
            if lost:
                out.append({"check": "J2 step lost", "turn": i, "detail": "; ".join(lost)[:200]})
        prior = steps or prior
        if rt.wants_code(turn["say"] or "", [{"response_message": t.turns[i - 2]["reply"]}] if i > 1 else []) and not turn.get("code"):
            out.append({"check": "J3 asked for code, got none", "turn": i, "detail": reply[:160]})
        if turn.get("code") and compile_problem(t.language, turn["code"]):
            out.append({"check": "J4 code doesn't compile", "turn": i, "detail": compile_problem(t.language, turn["code"])[:200]})
        for v in values:
            if v in reply and v not in said:
                out.append({"check": "J6 value hinted", "turn": i, "detail": v})
    for r in t.runs:
        if r["status"] == "error":
            out.append({"check": "J7 infrastructure error", "turn": r["after_turn"], "detail": r["stderr_head"][:200]})
    if t.persona in EXPECT_FAIL and t.runs:
        last = t.runs[-1]
        if last["status"] == "passed" or (last["status"] == "failed" and "FAIL: step" not in last["summary"] and "FAIL:" not in last["summary"]):
            out.append({"check": "J5 wrong value not a clean FAIL", "turn": last["after_turn"], "detail": last["summary"][:200]})
    return out


def _sim_call(client, prompt: str) -> tuple[dict, float]:
    msg = client.messages.create(model=SIM_MODEL, max_tokens=600, messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    cost = (msg.usage.input_tokens * SIM_PRICE[0] + msg.usage.output_tokens * SIM_PRICE[1]) / 1e6
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return (json.loads(m.group(0)) if m else {}), cost
    except ValueError:
        return {}, cost


def _typist_cost(calls) -> float:
    return sum((c.get("input_tokens") or 0) * 3 + (c.get("output_tokens") or 0) * 15 + (c.get("cache_read_tokens") or 0) * 0.3
               + (c.get("cache_write_tokens") or 0) * 3.75 for c in calls) / 1e6


def converse(persona: str, language: str, case: dict, spec: dict, panel: dict, sim, budget_left: float) -> Transcript:
    """One simulated candidate, start to finish. `sim(prompt) -> (decision, cost)` plays the candidate."""
    from app.services import llm_service, round2_typist
    from app.services.practice_engine import practice_run, reference
    t = Transcript(persona, language, case.get("title", ""))
    app_ref = reference.assistant_reference(panel)
    view = candidate_view(panel)
    conversation, code, last_run = [], "", ""
    case_text = "\n".join(f"{k}: {case.get(k)}" for k in ("title", "preconditions", "steps", "test_data", "expected_result"))
    for _ in range(MAX_TURNS):
        if t.cost >= budget_left:
            t.error = "budget reached"
            break
        # what the candidate sees: the reply, and whether the code panel changed (it can't read code)
        convo = "\n".join(f"Candidate: {c['candidate_prompt']}\nAssistant: {c['response_message']}"
                          + ("\n[the code panel now shows new code - you can run it]" if c.get("response_kind") == "code_edit" else "")
                          for c in conversation) or "(nothing yet)"
        decision, c = sim(SIM_PROMPT.format(persona=PERSONAS[persona], case=case_text, reference=view, conversation=convo,
                                            last_run=last_run))
        t.cost += c
        if decision.get("friction"):
            t.friction.append({"turn": len(t.turns), "note": str(decision["friction"])[:300]})
        if decision.get("run") and code.strip():
            r = practice_run.run(language, code, spec)
            status = round2_typist.run_status(r.exit_code, r.stdout or "", r.timed_out, r.infra_error)
            summary = "\n".join(l for l in (r.stdout or "").splitlines() if l.startswith(("PASS", "FAIL", "INCOMPLETE")))
            t.runs.append({"after_turn": len(t.turns), "status": status, "summary": summary,
                           "stderr_head": "\n".join((r.stderr or "").splitlines()[:6])})
            last_run = f"THE LAST RUN: {status.upper()}\n{summary or '(no PASS/FAIL lines)'}\n{(r.stderr or '')[:600]}"
        if decision.get("done") or not str(decision.get("say") or "").strip():
            if decision.get("done") or not decision.get("run"):
                break
            continue
        say = str(decision["say"]).strip()[:2000]
        before = len(llm_service.recent_calls())
        out = round2_typist.turn(language, case, conversation, code, say, app_reference=app_ref)
        t.cost += _typist_cost(llm_service.recent_calls()[before:])
        conversation.append({"candidate_prompt": say, "response_message": out["response_message"],
                             "response_kind": out["response_kind"], "steps": out.get("steps")})
        if out.get("code_after"):
            code = out["code_after"]
        t.turns.append({"say": say, "reply": out["response_message"], "kind": out["response_kind"],
                        "code": out.get("code_after"), "steps": out.get("steps"),
                        # every draft the assistant wrote this turn and what the tool found wrong with it
                        "attempts": out.get("attempts") or []})
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("cases")
    ap.add_argument("--personas", default="all")
    ap.add_argument("--languages", default="java")
    ap.add_argument("--case", type=int, default=None, help="1-based Round 1 case for every persona (default: rotate)")
    ap.add_argument("--cap", type=float, default=2.0)
    ap.add_argument("--out", default="sim_report.json")
    a = ap.parse_args()
    import anthropic
    from app.services import llm_service, round2_typist
    from app.services.practice_engine import reference
    llm_service.settings.ai_reuse_replies = False
    llm_service.settings.ai_batch_jobs = False
    config = json.loads(Path(a.config).read_text())
    spec = config["practice_spec"]
    panel = reference.current_panel(config)
    cases = json.loads(Path(a.cases).read_text())
    personas = list(PERSONAS) if a.personas == "all" else a.personas.split(",")
    client = anthropic.Anthropic()
    values = data_values(panel)
    spent, results = 0.0, []
    for li, language in enumerate(a.languages.split(",")):
        for pi, persona in enumerate(personas):
            if spent >= a.cap:
                print(f"STOPPED at the ${a.cap:.2f} cap")
                break
            case = cases[(a.case - 1) if a.case else (pi + li) % len(cases)]
            started = time.time()
            try:
                t = converse(persona, language, case, spec, panel, lambda p: _sim_call(client, p), a.cap - spent)
            except Exception as e:  # noqa: BLE001 - one broken conversation is a finding, not the end of the run
                t = Transcript(persona, language, case.get("title", ""), error=f"{type(e).__name__}: {e}")
            spent += t.cost
            findings = judge(t, values, round2_typist.compile_problem)
            if t.error and t.error != "budget reached":
                findings.append({"check": "J0 crashed", "turn": len(t.turns), "detail": t.error})
            results.append({"persona": persona, "language": language, "case": t.case_title, "turns": len(t.turns),
                            "runs": t.runs, "final": t.runs[-1]["status"] if t.runs else "not run", "cost": round(t.cost, 3),
                            "seconds": round(time.time() - started), "findings": findings, "friction": t.friction,
                            "transcript": t.turns})
            print(f"{language:10} {persona:16} turns={len(t.turns):2} final={results[-1]['final']:10} "
                  f"findings={len(findings)} ${t.cost:.3f}")
    Path(a.out).write_text(json.dumps({"spent": round(spent, 3), "results": results}, indent=1))
    clean = sum(not r["findings"] for r in results)
    print(f"\n{clean} of {len(results)} conversations with no finding; spent ${spent:.2f}; report: {a.out}")


if __name__ == "__main__":
    main()
