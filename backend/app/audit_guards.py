"""
Replays the Round 2 anti-cheating checks over every real candidate turn
stored in the database and lists each one they would have flagged. Real
turns come from genuine candidates, so every flag is a false alarm to look
at before shipping a change to round2_automation_policy. Read-only.

Run from backend/: python -m app.audit_guards
"""
import json
import sqlite3

from .database import sqlite_db_path
from .services import round2_automation_policy as policy

_DESIGN_KEYS = ("index", "title", "preconditions", "steps", "test_data", "expected_result", "refinements")


def audit() -> tuple[int, int, list[str]]:
    """(candidate messages, code turns, false alarms)."""
    con = sqlite3.connect(f"file:{sqlite_db_path()}?mode=ro", uri=True)
    messages = code_turns = 0
    alarms: list[str] = []
    try:
        rows = con.execute("""select s.id, s.content, sc.config_json from submissions s join scenarios sc on sc.id = s.scenario_id
                              where s.round_number = 2 and s.content like '%ai_test_automation%'""").fetchall()
    finally:
        con.close()
    for sid, raw, cfg in rows:
        content = json.loads(raw)
        env = "\n".join(((json.loads(cfg) if cfg else {}) or {}).get("environment_code_by_language", {}).values())
        language = content.get("language") or "python"
        for row in content.get("selected") or []:
            design = [{k: row.get(k) for k in _DESIGN_KEYS}]
            turns, before = row.get("turns") or [], ""
            for i, turn in enumerate(turns):
                messages += 1
                prompt = turn.get("candidate_prompt") or ""
                if policy.is_prohibited(prompt):
                    alarms.append(f"submission {sid}: message refused before the AI: {prompt[:100]!r}")
                code = turn.get("code_after")
                if turn.get("response_kind") != "code_edit" or not code:
                    continue
                code_turns += 1
                args = (design, turns[:i], prompt)
                for label, found in (("invented content", policy.invented_content(language, before, code, *args, env)),
                                     ("changed values", policy.changed_candidate_values(code, *args, env))):
                    if found:
                        alarms.append(f"submission {sid} turn {i + 1}: {label}: {found[:2]}")
                before = code
    return messages, code_turns, alarms


if __name__ == "__main__":
    messages, code_turns, alarms = audit()
    print(f"Checked {messages} real candidate messages and {code_turns} code turns.")
    for alarm in alarms:
        print("  " + alarm)
    print("No false alarms." if not alarms else f"{len(alarms)} possible false alarm(s) - review before shipping.")
    raise SystemExit(1 if alarms else 0)
