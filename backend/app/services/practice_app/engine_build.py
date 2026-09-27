"""
The practice-app factory, engine edition: the AI writes a DESCRIPTION of the
app (data - see docs/practice_engine/DESIGN.md), never code. A proven engine
runs it, and the Python, JavaScript and Java files are built from it by
template, so the three languages can't disagree and nothing is translated.

    describe    AI writes the description; practice_engine.validate checks it
                and the problems go back to the AI (MAX_DESCRIBE_CORRECTIONS)
    checklists  AI writes one checklist per Round 1 test case against the
                helpers the description produces (same checks as before)
    build       the three files are rendered from the description - no AI
    check       every checklist runs; a failing one is first re-checked (the
                checklist may be wrong), then the DESCRIPTION is corrected
                (MAX_FIX_ROUNDS), keeping the best version seen
    confirm     every checklist runs in all three languages

Returns generator.PracticeAppResult, so service.py, the approval rule and the
coverage report are unchanged. result.plan is a "design" view of the
description (helpers, accounts, data, messages, screens) that the checklist
validators, the scorer's ground truth and the Round 2 screens already read;
the description itself is plan["engine_spec"].
"""
import json

from .. import llm_service
from ..practice_engine import render, validate
from . import checker
from .generator import (
    PracticeAppResult,
    _format_cases,
    _problems_for,
    _render,
    _shared_context,
    accept_repairs,
    merge_checklist_retry,
    validate_checklists,
)

MAX_DESCRIBE_CORRECTIONS = 2
MAX_FIX_ROUNDS = 2
_DESCRIBE_TOKENS = 16384
_CHECKLIST_TOKENS = 16384

STEPS = [
    "Describing the practice app",
    "Writing a checklist for each test case",
    "Building the app in Python, JavaScript and Java",
    "Checking every test case",
    "Correcting the description",
    "Checking all three languages",
]

_TRUE_FALSE = "true/false"


def plan_view(spec: dict, extra: dict | None = None) -> dict:
    """The design the rest of the factory reads, derived from the description
    (so it can't disagree with the app), plus the AI's presentation parts:
    summary, plain-English rules, reference sheet, screens."""
    extra = extra or {}
    action_names = {a["name"] for a in spec.get("actions") or []}
    storing = {a["name"] for a in spec.get("actions") or [] if _stores(a)}
    key_queries = {q["name"] for q in spec.get("queries") or [] if q.get("key_input")}
    list_queries = {q["name"] for q in spec.get("queries") or [] if not q.get("key_input")}
    helpers = []
    for h in render.helpers(spec):
        layer, _, name = h["call"].rpartition(".")
        if not layer:
            layer = "global"
        tf = (layer == "UI" and (name in ("open", "login", "logout") or name in action_names)) or h["call"] == "Test.simulate"
        returns = _TRUE_FALSE if tf else _returns(h["call"])
        if layer == "UI" and name in key_queries:
            returns = "the record shown (an object), or null when there is none - never true/false"
        elif layer == "UI" and name in list_queries:
            returns = "a list of the records shown ([] when there are none) - never true/false"
        elif layer == "API" and name in key_queries | list_queries:
            returns = "an object {ok, error, result, message}"
        # Only actions that change stored records must be checked in the Database - one that just
        # remembers a choice (a delivery slot in the session) has nothing there to check.
        helpers.append({"layer": layer, "name": name, "params": h["params"], "returns": returns,
                        "behaviour": h["doc"], "changes_data": layer in ("UI", "API") and name in storing})
    users = spec.get("users") or {}
    accounts = []
    if users:
        for row in (spec.get("data") or {}).get(users.get("entity"), []):
            name = row.get(users.get("name_field")) if users.get("name_field") else None
            accounts.append({"login": row.get(users.get("login_field", "email")), "password": row.get(users.get("password_field", "password")),
                             "notes": ", ".join(str(x) for x in (name, row.get("status")) if x)})
    return {
        "app_name": spec.get("app_name"),
        "summary": extra.get("summary", ""),
        "base_url": spec.get("base_url", ""),
        "test_accounts": accounts,
        "data": json.dumps(spec.get("data") or {}, ensure_ascii=False),
        "pages": spec.get("pages") or [],
        "helpers": helpers,
        "rules": [str(r) for r in extra.get("rules") or []],
        "messages": _messages(spec),
        "not_supported": [f"{n.get('title')}: {n.get('reason')}" if isinstance(n, dict) else str(n) for n in extra.get("not_supported") or []],
        "reference_sheet": extra.get("reference_sheet") or _sheet(spec, accounts),
        "screens": extra.get("screens") or [],
        "engine_spec": spec,
    }


def _stores(node) -> bool:
    """Whether an action (anywhere in its effects, rules' "then", if / for_each) changes stored records."""
    if isinstance(node, dict):
        if any(k in node for k in ("set", "create", "delete")):
            return True
        return any(_stores(v) for v in node.values())
    if isinstance(node, list):
        return any(_stores(v) for v in node)
    return False


def _returns(call: str) -> str:
    if call.startswith("API."):
        return "an object {ok, error, ...}"
    if call.startswith("Database.count"):
        return "a number"
    if call.startswith(("Database.find", "Database.all", "Database.outbox")):
        return "a list of records"
    if call.startswith("Database.get"):
        return "the record, or null"
    return "see behaviour"


def _messages(spec: dict) -> list[dict]:
    out = []
    users = spec.get("users") or {}
    for key, text in (users.get("messages") or {}).items():
        out.append({"when": f"sign-in: {key.replace('_', ' ')}", "text": text})
    for kind in ("queries", "actions"):
        for item in spec.get(kind) or []:
            label = item.get("label") or item["name"]
            for i in item.get("inputs") or []:
                for c in i.get("checks") or []:
                    out.append({"when": f"{label}: {i['name']} fails {c.get('rule')}", "text": c.get("message")})
            for load in item.get("load") or []:
                out.append({"when": f"{label}: {load.get('as')} not found", "text": load.get("missing")})
            for r in item.get("rules") or []:
                if isinstance(r.get("message"), str):
                    out.append({"when": f"{label}: a rule refuses it", "text": r["message"]})
            if isinstance(item.get("message"), str):
                out.append({"when": f"{label} succeeds", "text": item["message"]})
            for key in ("missing", "none_message"):
                if item.get(key):
                    out.append({"when": f"{label}: {key.replace('_', ' ')}", "text": item[key]})
    for f in spec.get("faults") or []:
        out.append({"when": f"failure {f['name']} is switched on", "text": f.get("message")})
    return out


def _sheet(spec: dict, accounts: list[dict]) -> dict:
    fields = {"Web address": spec.get("base_url", "")}
    if accounts:
        fields.update({"Test login": accounts[0]["login"], "Password": accounts[0]["password"]})
    return {"fields": fields, "notes": "Only this data exists in the practice app."}


def _normalized(spec):
    """The AI writes "" for "no value" in date, number and true/false fields;
    the engine means None. Harmless, so fixed here rather than sent back."""
    if not isinstance(spec, dict) or not isinstance(spec.get("data"), dict) or not isinstance(spec.get("entities"), dict):
        return spec
    for entity, rows in spec["data"].items():
        e = spec["entities"].get(entity)
        fields = (e.get("fields") or {}) if isinstance(e, dict) else {}
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                for f, v in list(row.items()):
                    if v == "" and fields.get(f) in ("date", "datetime", "int", "number", "money", "bool"):
                        row[f] = None
    return spec


def _files(spec: dict, languages=render.LANGUAGES) -> tuple[dict, dict]:
    """({language: candidate file}, {language: {engine file name: code}})."""
    built = {lang: render.files(spec, lang) for lang in languages}
    return {lang: b[0] for lang, b in built.items()}, {lang: b[1] for lang, b in built.items()}


def _inspect(spec: dict, checklists: list[dict], languages=("python",)) -> checker.InspectionReport:
    code, support = _files(spec, languages)
    return checker.inspect(code, checklists, support)


def generate(title: str, description: str, reference_cases: list[dict], known_facts: dict | None = None,
             progress=None, reuse: dict | None = None, ai_allowed: bool = True) -> PracticeAppResult:
    """Same contract as generator.generate. reuse: the last build's
    {"plan", "checklists", "unsupported"} - when it was an engine build, its
    description and checklists are kept and only the correction rounds run,
    so "Generate again" continues from where the last build got to.
    ai_allowed=False: only re-check `reuse` with today's engine - no AI call,
    nothing paid (service.install_recorded)."""
    result = PracticeAppResult(
        reference_titles=[c.get("title", "") for c in reference_cases if c.get("title")],
        high_priority_titles={(c.get("title") or "").strip().lower() for c in reference_cases if c.get("priority") == "High"},
        reference_expected={(c.get("title") or "").strip().lower(): str(c.get("expected_result") or "") for c in reference_cases},
    )
    cases_text = _format_cases(reference_cases)
    by_title = {(c.get("title") or "").strip().lower(): c for c in reference_cases}

    def step(index: int, detail: str = "") -> None:
        if progress:
            try:
                progress(index, detail)
            except Exception:  # progress reporting must never break a build
                pass

    def call(prompt: str, max_tokens: int) -> str:
        result.ai_calls += 1
        reply = llm_service._call_claude(prompt, max_tokens=max_tokens)
        # Kept raw - also the replies that fail to parse, which the parsed results never showed
        # (tests/test_real_output_replay.py replays saved ones).
        result.exchanges.append({"call": result.ai_calls, "asked": prompt.strip().splitlines()[0][:120] if prompt.strip() else "",
                                 "reply": reply})
        return reply

    def ask_json(prompt: str, max_tokens: int):
        """A JSON reply, with one corrective retry: a single typo in a large reply
        used to end the whole build (measured: 2 of 9 builds, 2026-09-27)."""
        reply = call(prompt, max_tokens)
        try:
            return llm_service._parse_json_response(reply)
        except ValueError as error:
            result.log.append(f"an AI reply was not valid JSON ({error}) - asked again")
            return llm_service._parse_json_response(call(prompt + "\n\n" + llm_service._JSON_RETRY_NOTE.format(error=error), max_tokens))

    def describe() -> tuple[dict, dict]:
        prompt = _render(llm_service._load_prompt("practice_engine_describe.txt"), title=title, description=description,
                         reference_cases=cases_text,
                         known_facts=json.dumps(known_facts, indent=1, ensure_ascii=False) if known_facts else "(none)")
        reply = call(prompt, _DESCRIBE_TOKENS)
        for attempt in range(MAX_DESCRIBE_CORRECTIONS + 1):
            try:
                raw = llm_service._parse_json_response(reply)
                spec = _normalized(raw.get("spec")) if isinstance(raw, dict) else None
                problems = validate.problems(spec) if isinstance(spec, dict) else ['the reply must be {"spec": {...}, ...}']
            except ValueError as e:
                raw, spec, problems = None, None, [f"the reply was not valid JSON ({e})"]
            if not problems:
                return spec, raw
            result.log.append(f"description needed correcting ({len(problems)} problem(s)): " + "; ".join(problems[:5]))
            if attempt == MAX_DESCRIBE_CORRECTIONS:
                raise ValueError("the AI's description still had problems: " + "; ".join(problems[:8]))
            step(0, f"correcting the description ({attempt + 1} of {MAX_DESCRIBE_CORRECTIONS})")
            reply = call(prompt + "\n\nYOUR PREVIOUS REPLY:\n" + reply + "\n\nIt has these problems - fix every one and reply with the "
                         "complete corrected JSON object:\n" + "\n".join(f"- {p}" for p in problems[:40]), _DESCRIBE_TOKENS)
        raise AssertionError("unreachable")

    def write_checklists(plan: dict, plan_text: str) -> tuple[list, list]:
        prompt = _shared_context(plan_text) + _render(llm_service._load_prompt("practice_app_checklists.txt"), reference_cases=cases_text)
        raw = ask_json(prompt, _CHECKLIST_TOKENS)
        runnable, unsupported, problems = validate_checklists(plan, raw, reference_cases)
        if problems:
            result.log.append("checklists needed a second attempt: " + "; ".join(problems[:5]))
            step(1, "second attempt")
            retry = prompt + "\n\nYour previous attempt had these problems - fix every one:\n" + "\n".join(f"- {p}" for p in problems)
            raw = merge_checklist_retry(raw, runnable, ask_json(retry, _CHECKLIST_TOKENS))
            runnable, unsupported, problems = validate_checklists(plan, raw, reference_cases)
            for p in problems:
                result.log.append(f"checklist problem left: {p}")
        return runnable, unsupported

    def failing_blocks(checklists: list[dict], report: checker.InspectionReport) -> tuple[dict, list[str]]:
        failed = {r.id: r.failures for r in report.languages["python"].results if not r.passed}
        blocks = [json.dumps({"checklist": c, "round1_test_case": by_title.get((c.get("title") or "").strip().lower(), {}),
                              "what_went_wrong": failed[c["id"]][:5]}, indent=1, ensure_ascii=False)
                  for c in checklists if c["id"] in failed]
        return failed, blocks

    try:
        extra: dict = {}
        reused = False
        if reuse and isinstance((reuse.get("plan") or {}).get("engine_spec"), dict) and reuse.get("checklists") \
                and not validate.problems(reuse["plan"]["engine_spec"]):
            spec = reuse["plan"]["engine_spec"]
            extra = {k: reuse["plan"].get(k) for k in ("summary", "rules", "reference_sheet", "screens")}
            extra["not_supported"] = reuse["plan"].get("not_supported")
            plan = plan_view(spec, extra)
            runnable, _, problems = validate_checklists(plan, reuse["checklists"], [])
            if not problems:
                reused = True
                unsupported = reuse.get("unsupported") or []
                result.log.append(f"kept the last build's description and {len(runnable)} checklists - continuing from there")
        if not reused and not ai_allowed:
            raise ValueError("the recorded build's description or checklists no longer pass today's checks")
        if not reused:
            step(0)
            spec, raw = describe()
            extra = {k: raw.get(k) for k in ("summary", "rules", "not_supported", "reference_sheet", "screens")}
            plan = plan_view(spec, extra)
            result.log.append(f"described {spec.get('app_name', title)!r}: {len(spec.get('entities') or {})} record types, "
                              f"{len(spec.get('queries') or [])} lookups, {len(spec.get('actions') or [])} actions")
            step(1)
            runnable, unsupported = write_checklists(plan, json.dumps(plan, indent=1, ensure_ascii=False))
        result.plan, result.checklists, result.unsupported = plan, runnable, unsupported
        if not runnable:
            raise ValueError("no runnable checklists")
        result.log.append(f"{len(runnable)} checklists, {len(unsupported)} test case(s) not supported")

        step(2)
        step(3)
        report = _inspect(spec, runnable)
        best = {"passed": report.languages["python"].passed, "spec": spec, "plan": plan, "runnable": runnable}
        result.log.append(f"python: {best['passed']}/{len(runnable)} checklists pass")

        if ai_allowed and _problems_for("python", report):
            # The checklist may be what's wrong (a record the starting data
            # already has used as "new"...) - re-checked first, as before.
            step(3, "re-checking the failing checklists")
            failed, blocks = failing_blocks(runnable, report)
            plan_text = json.dumps(plan, indent=1, ensure_ascii=False)
            try:
                repaired = ask_json(_shared_context(plan_text) + _render(
                    llm_service._load_prompt("practice_app_repair_checklists.txt"), failing="\n\n".join(blocks)), _CHECKLIST_TOKENS)
                runnable, rewritten = accept_repairs(plan, runnable, repaired, set(failed))
            except llm_service.AIBudgetReached:
                raise
            except Exception as e:  # an unusable reply is not a reason to lose the build
                rewritten = []
                result.log.append(f"couldn't re-check the failing checklists ({type(e).__name__})")
            if rewritten:
                result.log.append(f"rewrote {len(rewritten)} failing checklist(s): {', '.join(rewritten)}")
                report = _inspect(spec, runnable)
                result.log.append(f"python: {report.languages['python'].passed}/{len(runnable)} checklists pass")
                if report.languages["python"].passed > best["passed"]:
                    best.update(passed=report.languages["python"].passed, runnable=runnable)

        for fix_no in range(1, MAX_FIX_ROUNDS + 1 if ai_allowed else 1):
            if not _problems_for("python", report):
                break
            step(4, f"{report.languages['python'].passed} of {len(runnable)} pass - correcting (round {fix_no} of {MAX_FIX_ROUNDS})")
            failed, blocks = failing_blocks(runnable, report)
            plan_text, checklists_text = json.dumps(plan, indent=1, ensure_ascii=False), json.dumps(runnable, indent=1, ensure_ascii=False)
            try:
                raw = ask_json(_shared_context(plan_text, checklists_text) + _render(
                    llm_service._load_prompt("practice_engine_fix.txt"), failing="\n\n".join(blocks)), _DESCRIBE_TOKENS)
                if isinstance(raw, dict) and raw.get("checklist_problem") and not raw.get("spec"):
                    result.log.append(f"the remaining failures are the checklists' fault: {str(raw['checklist_problem'])[:200]} - "
                                      "no more corrections paid for")
                    break
                new_spec = _normalized(raw.get("spec")) if isinstance(raw, dict) else None
                problems = validate.problems(new_spec) if isinstance(new_spec, dict) else ["no description in the reply"]
            except ValueError as e:
                problems = [f"the reply was not valid JSON ({e})"]
            if problems:
                result.log.append(f"correction {fix_no} was not usable: " + "; ".join(problems[:5]))
                continue
            new_plan = plan_view(new_spec, extra)
            _, _, checklist_problems = validate_checklists(new_plan, runnable, [])
            if checklist_problems:
                result.log.append(f"correction {fix_no} renamed what the checklists use - not kept: " + "; ".join(checklist_problems[:3]))
                continue
            spec, plan = new_spec, new_plan
            report = _inspect(spec, runnable)
            passed = report.languages["python"].passed
            result.log.append(f"after correction {fix_no}: {passed}/{len(runnable)} checklists pass")
            if passed > best["passed"]:
                best.update(passed=passed, spec=spec, plan=plan, runnable=runnable)

        # The best version seen - a correction can make things worse.
        spec, plan, runnable = best["spec"], best["plan"], best["runnable"]
        result.plan, result.checklists = plan, runnable

        step(5)
        code, support = _files(spec)
        report = checker.inspect(code, runnable, support)
        result.log.append("all languages: " + report.summary())
        for d in report.differences[:5]:
            result.log.append(f"languages disagree (engine fault - please report): {d}")
        result.env_code_by_language, result.support_by_language, result.report = code, support, report
        result.ok = report.all_passed and all(row["status"] == "works" for row in result.coverage())
        result.approvable = result.ok or result.approval_problem() is None
    except Exception as e:  # the caller shows HR a clear message; nothing is saved
        result.error = str(e) if isinstance(e, llm_service.AIBudgetReached) else f"{type(e).__name__}: {e}"
        result.log.append(f"stopped: {result.error}")
    return result
