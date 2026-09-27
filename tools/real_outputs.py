"""
The real-output library: what the real AI actually produced in paid runs,
kept so every later change is replayed against it offline for free
(tests/test_real_output_replay.py). Nothing here calls the AI.

    python tools/real_outputs.py build <measured-build.json> <label>
    python tools/real_outputs.py assistant <typist_eval.json> <label>

A build entry keeps the AI's description, its checklists and, per checklist,
whether it passed when recorded; an assistant entry keeps each real reply,
what the candidate had said, and whether the reply followed the rules.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LIBRARY = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "practice_engine" / "real_outputs"


def add_build(run_file: Path, label: str) -> Path | None:
    run = json.loads(run_file.read_text())
    spec = (run.get("plan") or {}).get("engine_spec")
    if not spec:
        return None  # the build stopped before a description was accepted - its raw replies are what matter then
    failing = {str(r["title"]).strip().lower() for r in run.get("failing") or []}
    checklists = [{**c, "recorded_pass": str(c.get("title") or "").strip().lower() not in failing} for c in run.get("checklists") or []]
    entry = {"label": label, "title": run.get("title"), "recorded": f"{run.get('works')}/{run.get('total')}", "spec": spec,
             "checklists": checklists, "exchanges": run.get("exchanges") or []}
    out = LIBRARY / "builds" / f"{label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(entry, indent=1, ensure_ascii=False))
    return out


def add_assistant(eval_file: Path, label: str) -> Path:
    data = json.loads(eval_file.read_text())
    replies = [{"conversation": r["conversation"], "turn": r["turn"], "candidate": r["candidate"], "said": r.get("said"),
                "reply": r["reply"], "code": r.get("code"), "followed_rules": r["ok"], "why": r.get("why", "")}
               for r in data["results"]]
    out = LIBRARY / "assistant" / f"{label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"label": label, "replies": replies}, indent=1, ensure_ascii=False))
    return out


if __name__ == "__main__":
    kind, source, name = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    written = add_build(source, name) if kind == "build" else add_assistant(source, name)
    print(written or f"{name}: no accepted description - nothing added")
