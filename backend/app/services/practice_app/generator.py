"""
The practice-app factory: builds the Round 2 practice app (the small pretend
version of the Round 1 application that candidates' automation runs against)
for ANY Round 1 scenario, and proves it works before anyone uses it.

    plan        AI designs the app from the scenario and its reference test
                cases: accounts, data, helpers, rules, exact messages
    checklists  AI writes one machine-checkable checklist per reference case
    python      AI writes the app in Python; the inspector runs every
                checklist; failures go back to the AI to fix (MAX_FIX_ROUNDS)
    translate   AI translates the working Python app to JavaScript and Java
    verify      the inspector runs every checklist in all three languages and
                compares them with each other; failures go back to be fixed

The result is only usable (result.ok) when every supported checklist passes
identically in all three languages. Everything else - the checklists, the
inspection report, which reference cases aren't supported - is kept so HR
can see exactly what was checked. AI calls go through llm_service._call_claude
(temperature 0, the shared system prompt, cut-off retry, usage log).
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .. import llm_service
from . import checker

MAX_FIX_ROUNDS = 2

# The steps HR sees while a build runs (see service.run_build).
STEPS = [
    "Designing the practice app",
    "Writing a checklist for each test case",
    "Building the Python version",
    "Checking the Python version",
    "Building the JavaScript and Java versions",
    "Checking all three languages",
]
_PLAN_TOKENS = 8192
_CHECKLIST_TOKENS = 16384
_CODE_TOKENS = 16384

LANGUAGE_NAMES = {"python": "Python", "javascript": "JavaScript", "java": "Java"}
_FENCES = {"python": "python", "javascript": "javascript", "java": "java"}

# Style examples: the hand-written Doctor Appointment practice app. The AI
# copies its structure, not its content.
_EXAMPLE_FILES = {lang: f"round2_automation_helpers_appointments_{lang}.txt" for lang in checker.LANGUAGES}

# The Beneficiary build's JavaScript renamed its internal fields to camelCase,
# so get_field_value("account_number") - the field name the checklist passes,
# as a string - found nothing; both fix rounds kept the rename, since the rule
# they were given asked for camelCase.
_SAME_STRING_VALUES = (
    "The camelCase/naming rule is only for the property names of returned objects. Every string VALUE - field "
    "names passed as arguments (e.g. \"account_number\"), keys looked up from them, statuses, page names, "
    "messages - must be exactly the same string as in the Python version, so internal data that is looked up "
    "by a string argument keeps the Python key."
)

_LANGUAGE_RULES = {
    "python": "Standard library only.",
    "javascript": (
        "Plain Node.js script: no imports, no exports, no async, no classes needed. Declare const UI = {...}, "
        "const API = {...}, const Database = {...}, and top-level function setup() and function teardown(id = null). "
        "Return plain objects and arrays, with camelCase field names. " + _SAME_STRING_VALUES
    ),
    "java": (
        "One file. The public class must be named Main, with every helper group as a nested static class "
        "(Main.UI, Main.API, Main.Database) holding static methods, and static void setup() and "
        "static void teardown(String id) directly on Main. Only java.util imports. Keep an empty "
        "public static void main(String[] args). Return java.util types or small static nested classes with "
        "plain fields (no getters). Use nullable types (String, Integer, Boolean) wherever the Python version can "
        "return None, and accept null for optional parameters. " + _SAME_STRING_VALUES
    ),
}


@dataclass
class PracticeAppResult:
    ok: bool = False
    plan: dict = field(default_factory=dict)
    checklists: list = field(default_factory=list)          # the ones that can be run
    unsupported: list = field(default_factory=list)         # [{"title", "reason"}] reference cases the app can't automate
    env_code_by_language: dict = field(default_factory=dict)
    report: checker.InspectionReport | None = None
    log: list = field(default_factory=list)                 # what happened, step by step, for HR/support
    ai_calls: int = 0
    error: str | None = None
    reference_titles: list = field(default_factory=list)   # every Round 1 test case, so none can go missing from the report

    def coverage(self) -> list[dict]:
        """One row per reference test case: works / fails / not supported."""
        rows = []
        results = {}
        if self.report:
            for report in self.report.languages.values():
                for r in report.results:
                    results.setdefault(r.id, []).append(r)
        for c in self.checklists:
            outcomes = results.get(c["id"], [])
            works = bool(outcomes) and all(r.passed for r in outcomes) and len(outcomes) == len(self.report.languages)
            failures = [f for r in outcomes for f in r.failures]
            rows.append({"title": c.get("title", c["id"]), "status": "works" if works else "fails", "details": failures[:3]})
        for u in self.unsupported:
            rows.append({"title": u["title"], "status": "not supported", "details": [u["reason"]]})
        listed = {r["title"].strip().lower() for r in rows}
        for title in self.reference_titles:
            if title.strip().lower() not in listed:
                rows.append({"title": title, "status": "fails", "details": ["the factory couldn't write a valid checklist for it"]})
        return rows


def _render(template: str, **values) -> str:
    for key, value in values.items():
        template = template.replace(f"<<{key.upper()}>>", value)
    return template


def _format_cases(cases: list[dict]) -> str:
    lines = []
    for n, case in enumerate(cases, start=1):
        lines.append(f"{n}. {case.get('title', '').strip()}")
        for key in ("preconditions", "steps", "test_data", "expected_result"):
            value = str(case.get(key) or "").strip()
            if value:
                lines.append(f"   {key.replace('_', ' ')}: " + value.replace("\n", "\n      "))
    return "\n".join(lines)


_CODE_BLOCK_RE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)```", re.S)


def _code(reply: str) -> str:
    blocks = _CODE_BLOCK_RE.findall(reply or "")
    return (max(blocks, key=len) if blocks else reply or "").strip() + "\n"


def _helper_names(plan: dict) -> set[str]:
    names = set()
    for h in plan.get("helpers") or []:
        layer, name = (h.get("layer") or "").strip(), (h.get("name") or "").strip()
        names.add(name if layer.lower() == "global" else f"{layer}.{name}")
    return names | {"setup", "teardown"}


def _data_changing_helpers(plan: dict) -> set[str]:
    names = set()
    for h in plan.get("helpers") or []:
        if h.get("changes_data") and (h.get("layer") or "").lower() != "global":
            names.add(f"{h.get('layer')}.{h.get('name')}")
    return names


def _true_false_helpers(plan: dict) -> set[str]:
    names = set()
    for h in plan.get("helpers") or []:
        returns = str(h.get("returns") or "").strip().lower()
        if returns.startswith(("boolean", "bool", "true/false", "true or false")):
            layer, name = (h.get("layer") or "").strip(), (h.get("name") or "").strip()
            names.add(name if layer.lower() == "global" else f"{layer}.{name}")
    return names


def _ref_problem(checklist: dict, true_false: set[str]) -> str | None:
    """A ref to nothing saved yet, or one that passes a step's true/false
    result on as if it were a value such as an id. The second can't pass in
    any app: the Library build's checklists expected Borrow to return true
    AND used what it returned as the borrowing id, so each fix of one broke
    the other."""
    saved = {}
    for step in checklist["steps"]:
        for arg in step.get("args") or []:
            if not (isinstance(arg, dict) and "ref" in arg):
                continue
            root = str(arg["ref"]).split(".")[0]
            source = saved.get(root)
            if source is None:
                return f"checklist {checklist['id']!r} uses {{\"ref\": \"{arg['ref']}\"}} but no earlier step saved {root!r}"
            if isinstance(source.get("expect"), bool) or source.get("call") in true_false:
                return (f"checklist {checklist['id']!r} passes the true/false result of {source.get('call')} ({root!r}) to "
                        f"{step.get('call')} as if it were a value such as an id - save a step that returns that value instead")
        if step.get("save_as"):
            saved[step["save_as"]] = step
    return None


def _missing_start_record_problem(checklist: dict, plan: dict, changing: set[str]) -> str | None:
    """A Database check expecting false for a value the design's starting
    data holds, when nothing earlier in the checklist was expected to change
    data successfully - the record is still there, so it can never pass. The
    Beneficiary build's checklists added the Round 1 test data's account
    numbers as "new" while the design had pre-loaded them (the test cases
    also said they "already exist"), so each fix of one broke another."""
    start_data = str(plan.get("data") or "")
    for step in checklist["steps"]:
        call = step.get("call") or ""
        if call in changing and step.get("expect") is not False:
            return None  # data may have changed from here on
        args = step.get("args") or []
        if call.startswith("Database.") and step.get("expect") is False and args:
            # The record being looked up is the first argument (later ones are
            # e.g. its owner, who does exist); matched as a whole value, not
            # inside a longer one ("12345678" within "1234567890123456").
            key = args[0]
            if (isinstance(key, str) and len(key) >= 6
                    and re.search(rf"(?<![\w@.-]){re.escape(key)}(?![\w@.-])", start_data)):
                return (f"checklist {checklist['id']!r} expects {call}({key!r}) to be false, but {key!r} is in the design's "
                        "starting data and nothing earlier removed it - use a value the starting data doesn't have")
    return None


def validate_checklists(plan: dict, checklists: list, reference_cases: list[dict]) -> tuple[list, list, list[str]]:
    """Splits the AI's checklists into runnable ones and unsupported cases,
    and lists problems: a call to a helper the plan doesn't have, a missing
    or duplicate id, a reference case with no checklist, a ref that can't
    hold the value it's used as, or a checklist that
    changes data without checking the Database (the first trial run proved
    only 3 of 28 bookings/refusals against what was really stored)."""
    helpers = _helper_names(plan)
    changing = _data_changing_helpers(plan)
    true_false = _true_false_helpers(plan)
    runnable, unsupported, problems, seen = [], [], [], set()
    for c in checklists if isinstance(checklists, list) else []:
        if not isinstance(c, dict) or not c.get("id"):
            problems.append("a checklist has no id")
            continue
        if c["id"] in seen:
            problems.append(f"checklist id {c['id']!r} is used twice")
            continue
        seen.add(c["id"])
        if c.get("unsupported"):
            unsupported.append({"title": c.get("title", c["id"]), "reason": str(c["unsupported"])})
            continue
        unknown = sorted({s.get("call", "") for s in c.get("steps") or [] if s.get("call") not in helpers})
        if not c.get("steps"):
            problems.append(f"checklist {c['id']!r} has no steps")
        elif unknown:
            problems.append(f"checklist {c['id']!r} calls helpers the design doesn't have: {', '.join(unknown)}")
        elif any(s.get("call") in changing for s in c["steps"]) and not any(
                (s.get("call") or "").startswith("Database.") for s in c["steps"]):
            problems.append(f"checklist {c['id']!r} changes data but never checks the Database layer")
        elif ref_problem := _ref_problem(c, true_false):
            problems.append(ref_problem)
        elif start_problem := _missing_start_record_problem(c, plan, changing):
            problems.append(start_problem)
        else:
            runnable.append(c)
    covered = {(c.get("title") or "").strip().lower() for c in runnable} | {u["title"].strip().lower() for u in unsupported}
    for case in reference_cases:
        if (case.get("title") or "").strip().lower() not in covered:
            problems.append(f"no checklist for reference test case {case.get('title')!r}")
    return runnable, unsupported, problems


def _title_key(item) -> str:
    return (item.get("title") or "").strip().lower() if isinstance(item, dict) else ""


def merge_checklist_retry(first: list, first_valid: list[dict], retried) -> list:
    """The first attempt's valid checklists (and its "unsupported" entries),
    plus the retry's checklists only for test cases the first attempt got
    wrong or missed. A full retry used to replace everything - the Beneficiary
    build's first attempt had one bad checklist, its retry broke six good ones
    (an invented "assert" step) and those test cases were dropped."""
    valid_ids = {c["id"] for c in first_valid}
    good = lambda c: isinstance(c, dict) and (c.get("id") in valid_ids or c.get("unsupported"))  # noqa: E731
    covered = {_title_key(c) for c in first if good(c)}
    used_ids = {c.get("id") for c in first if good(c)}
    replacements = {}
    for c in retried if isinstance(retried, list) else []:
        if isinstance(c, dict) and _title_key(c) not in covered and c.get("id") not in used_ids:
            replacements.setdefault(_title_key(c), c)
    merged = []
    for c in first:  # in the first attempt's order, each bad one swapped for its retry
        if good(c):
            merged.append(c)
        elif _title_key(c) in replacements:
            merged.append(replacements.pop(_title_key(c)))
    return merged + list(replacements.values())  # test cases the first attempt missed


def _check_counts(checklist: dict) -> tuple[int, int]:
    """(checks, Database checks) - a rewritten checklist must keep both."""
    checks = [s for s in checklist.get("steps") or [] if any(k in s for k in ("expect", "expect_includes", "expect_excludes"))]
    return len(checks), sum((s.get("call") or "").startswith("Database.") for s in checks)


def accept_repairs(plan: dict, checklists: list[dict], repaired, failing_ids: set[str]) -> tuple[list[dict], list[str]]:
    """Swaps in the AI's rewrites of failing checklists - only where the
    rewrite keeps the id and title, is valid against the design, and checks
    at least as much (and as much in the Database) as the original, so a
    checklist can't be "fixed" by dropping what it checked. Returns the
    checklists and the ids replaced."""
    by_id = {c["id"]: c for c in checklists}
    accepted = {}
    for item in repaired if isinstance(repaired, list) else []:
        original = by_id.get(item.get("id")) if isinstance(item, dict) else None
        if original is None or item["id"] not in failing_ids or item == original:
            continue
        if (item.get("title") or "") != (original.get("title") or ""):
            continue
        runnable, _, problems = validate_checklists(plan, [item], [])
        if problems or runnable != [item]:
            continue
        checks, db_checks = _check_counts(item)
        original_checks, original_db_checks = _check_counts(original)
        if checks >= original_checks and db_checks >= original_db_checks:
            accepted[item["id"]] = item
    return [accepted.get(c["id"], c) for c in checklists], sorted(accepted)


def _problems_for(language: str, report: checker.InspectionReport) -> list[str]:
    lang_report = report.languages.get(language)
    if lang_report is None:
        return []
    if lang_report.error:
        return [lang_report.error]
    problems = [f"{r.id}: {f}" for r in lang_report.results for f in r.failures]
    problems += [d for d in report.differences if f" {language} gave " in d]
    return problems


def generate(title: str, description: str, reference_cases: list[dict], known_facts: dict | None = None,
             progress=None, reuse: dict | None = None) -> PracticeAppResult:
    """progress(step_index, detail) is called as each of STEPS starts or advances.

    reuse: the last build's {"plan", "checklists", "unsupported", "python"}.
    If that Python app still passes every checklist, design, checklists and
    Python are kept and only the translations are redone - when only a
    translation failed, "Generate again" costs two AI calls, not ten. If it
    doesn't pass, reuse is ignored and the build starts from scratch."""
    result = PracticeAppResult(reference_titles=[c.get("title", "") for c in reference_cases if c.get("title")])
    cases_text = _format_cases(reference_cases)

    def step(index: int, detail: str = "") -> None:
        if progress:
            try:
                progress(index, detail)
            except Exception:  # progress reporting must never break a build
                pass

    def call(prompt: str, max_tokens: int) -> str:
        result.ai_calls += 1
        return llm_service._call_claude(prompt, max_tokens=max_tokens)

    def fix(language: str, code: str, problems: list[str]) -> str:
        prompt = _render(
            llm_service._load_prompt("practice_app_fix.txt"),
            language_name=LANGUAGE_NAMES[language], fence=_FENCES[language], plan=plan_text, code=code,
            problems="\n".join(f"- {p}" for p in problems[:40]), checklists=checklists_text,
            language_rules=_LANGUAGE_RULES[language],
        )
        return _code(call(prompt, _CODE_TOKENS))

    def repair_checklists(checklists: list[dict], report: checker.InspectionReport) -> tuple[list[dict], list[str]]:
        # The app code may be right and the checklist wrong (a record the
        # starting data already has used as "new", a result used as an id) -
        # code fixes can't solve that, so every earlier stuck build gave up.
        failed = {r.id: r.failures for r in report.languages["python"].results if not r.passed}
        cases = {(c.get("title") or "").strip().lower(): c for c in reference_cases}
        blocks = [json.dumps({"checklist": c, "round1_test_case": cases.get((c.get("title") or "").strip().lower(), {}),
                              "what_went_wrong": failed[c["id"]][:5]}, indent=1, ensure_ascii=False)
                  for c in checklists if c["id"] in failed]
        raw = llm_service._parse_json_response(call(_render(
            llm_service._load_prompt("practice_app_repair_checklists.txt"), plan=plan_text, failing="\n\n".join(blocks),
        ), _CHECKLIST_TOKENS))
        return accept_repairs(result.plan, checklists, raw, set(failed))

    try:
        reused = False
        if reuse and reuse.get("python") and reuse.get("checklists") and reuse.get("plan"):
            step(3, "re-checking the last build's Python app")
            report = checker.inspect({"python": reuse["python"]}, reuse["checklists"])
            if not _problems_for("python", report):
                reused = True
                result.plan, result.checklists = reuse["plan"], reuse["checklists"]
                result.unsupported = reuse.get("unsupported") or []
                runnable, python = result.checklists, reuse["python"]
                plan_text = json.dumps(result.plan, indent=1, ensure_ascii=False)
                checklists_text = json.dumps(runnable, indent=1, ensure_ascii=False)
                result.log.append(f"reused the last build's design, checklists and Python app ({len(runnable)} of "
                                  f"{len(runnable)} still pass) - only translating")
            else:
                result.log.append("the last build's Python app no longer passes every checklist - building from scratch")
        if not reused:
            # 1. Plan
            step(0)
            plan = llm_service._parse_json_response(call(_render(
                llm_service._load_prompt("practice_app_plan.txt"),
                title=title, description=description, reference_cases=cases_text,
                known_facts=json.dumps(known_facts, indent=1, ensure_ascii=False) if known_facts else "(none)",
            ), _PLAN_TOKENS))
            if not isinstance(plan, dict) or not plan.get("helpers"):
                raise ValueError("the AI's design had no helpers")
            result.plan = plan
            plan_text = json.dumps(plan, indent=1, ensure_ascii=False)
            result.log.append(f"designed {plan.get('app_name', title)!r} with {len(plan['helpers'])} helpers")

            # 2. Checklists (one retry if they don't match the design)
            step(1)
            checklist_prompt = _render(llm_service._load_prompt("practice_app_checklists.txt"), plan=plan_text, reference_cases=cases_text)
            raw = llm_service._parse_json_response(call(checklist_prompt, _CHECKLIST_TOKENS))
            runnable, unsupported, problems = validate_checklists(plan, raw, reference_cases)
            if problems:
                result.log.append("checklists needed a second attempt: " + "; ".join(problems[:5]))
                step(1, "second attempt")
                retry = checklist_prompt + "\n\nYour previous attempt had these problems - fix every one:\n" + "\n".join(f"- {p}" for p in problems)
                retried = llm_service._parse_json_response(call(retry, _CHECKLIST_TOKENS))
                raw = merge_checklist_retry(raw, runnable, retried)
                runnable, unsupported, problems = validate_checklists(plan, raw, reference_cases)
                for p in problems:
                    result.log.append(f"checklist problem left: {p}")
            result.checklists, result.unsupported = runnable, unsupported
            if not runnable:
                raise ValueError("no runnable checklists")
            checklists_text = json.dumps(runnable, indent=1, ensure_ascii=False)
            result.log.append(f"{len(runnable)} checklists, {len(unsupported)} test case(s) not supported")

            # 3. Python, checked and fixed on its own first
            step(2)
            python = _code(call(_render(
                llm_service._load_prompt("practice_app_write_python.txt"),
                plan=plan_text, checklists=checklists_text, example=llm_service._load_prompt(_EXAMPLE_FILES["python"]),
            ), _CODE_TOKENS))
            # The best version seen: a fix can make things worse (the Beneficiary
            # build went 18/19 -> 16/19 on its last fix), and the build must
            # never end on a worse app than it already had.
            best = {}

            def remember() -> None:
                passed = report.languages["python"].passed
                if not best or passed > best["passed"]:
                    best.update(passed=passed, python=python, report=report, problems=problems, runnable=runnable)

            for round_no in range(MAX_FIX_ROUNDS + 1):
                step(3, f"fix round {round_no} of {MAX_FIX_ROUNDS}" if round_no else "")
                report = checker.inspect({"python": python}, runnable)
                problems = _problems_for("python", report)
                remember()
                result.log.append(f"python: {report.languages['python'].passed}/{len(runnable)} checklists pass")
                if not problems or round_no == MAX_FIX_ROUNDS:
                    break
                if round_no == 1:
                    # A code fix didn't clear everything: check the checklists too.
                    step(3, "re-checking the failing checklists")
                    runnable, rewritten = repair_checklists(runnable, report)
                    result.checklists = runnable
                    checklists_text = json.dumps(runnable, indent=1, ensure_ascii=False)
                    result.log.append(f"rewrote {len(rewritten)} failing checklist(s): {', '.join(rewritten)}" if rewritten
                                      else "the failing checklists match the design - kept as they were")
                    if rewritten:
                        report = checker.inspect({"python": python}, runnable)
                        problems = _problems_for("python", report)
                        remember()
                        result.log.append(f"python: {report.languages['python'].passed}/{len(runnable)} checklists pass")
                        if not problems:
                            break
                step(3, f"{report.languages['python'].passed} of {len(runnable)} pass - fixing (round {round_no + 1} of {MAX_FIX_ROUNDS})")
                python = fix("python", python, problems)
            if problems and best["passed"] > report.languages["python"].passed:
                python, report, problems, runnable = best["python"], best["report"], best["problems"], best["runnable"]
                result.checklists = runnable
                checklists_text = json.dumps(runnable, indent=1, ensure_ascii=False)
                result.log.append(f"kept the best version ({best['passed']}/{len(runnable)} pass) - the last fix made it worse")
            if problems:
                # Translating an app that doesn't work would only pay to copy its
                # mistakes - stop here and show HR what failed.
                result.env_code_by_language, result.report = {"python": python}, report
                result.log.append("stopped: the Python app still fails after the allowed fixes - not translated")
                return result

        # 4. JavaScript and Java, translated from the checked Python
        def translate(language: str) -> str:
            return _code(call(_render(
                llm_service._load_prompt("practice_app_translate.txt"),
                language_name=LANGUAGE_NAMES[language], fence=_FENCES[language], python_code=python,
                example=llm_service._load_prompt(_EXAMPLE_FILES[language]), language_rules=_LANGUAGE_RULES[language],
            ), _CODE_TOKENS))

        step(4)
        with ThreadPoolExecutor(max_workers=2) as pool:
            code = {"python": python, **dict(zip(("javascript", "java"), pool.map(translate, ("javascript", "java"))))}

        # 5. All three together; fix whichever translation disagrees
        for round_no in range(MAX_FIX_ROUNDS + 1):
            step(5, f"fix round {round_no} of {MAX_FIX_ROUNDS}" if round_no else "")
            report = checker.inspect(code, runnable)
            result.log.append("all languages: " + report.summary())
            failing = {lang: _problems_for(lang, report) for lang in ("javascript", "java")}
            failing = {lang: p for lang, p in failing.items() if p}
            if not failing or round_no == MAX_FIX_ROUNDS:
                break
            step(5, f"fixing {' and '.join(LANGUAGE_NAMES[l] for l in failing)} (round {round_no + 1} of {MAX_FIX_ROUNDS})")
            with ThreadPoolExecutor(max_workers=2) as pool:
                fixed = dict(zip(failing, pool.map(lambda lang: fix(lang, code[lang], failing[lang]), failing)))
            code.update(fixed)

        result.env_code_by_language = code
        result.report = report
        result.ok = report.all_passed and all(row["status"] != "fails" for row in result.coverage())
    except Exception as e:  # the caller shows HR a clear message; nothing is saved
        result.error = f"{type(e).__name__}: {e}"
        result.log.append(f"stopped: {result.error}")
    return result


def ground_truth(plan: dict) -> str:
    """The scorer's hidden description of how the practice app behaves,
    written from the same design the app was built from - so the two can't
    disagree."""
    lines = [f"The provided practice app ({plan.get('app_name', 'the application')}) behaves as follows, and this is "
             "the truth the candidate's automation should be judged against:"]
    for account in plan.get("test_accounts") or []:
        lines.append(f"- Test account: {account.get('login')} / {account.get('password')}"
                     + (f" ({account['notes']})" if account.get("notes") else ""))
    if plan.get("data"):
        lines.append(f"- Starting data: {plan['data']}")
    for rule in plan.get("rules") or []:
        lines.append(f"- {rule}")
    for m in plan.get("messages") or []:
        lines.append(f"- When {m.get('when')}: the visible message is exactly {m.get('text')!r}.")
    for h in plan.get("helpers") or []:
        name = h.get("name") if (h.get("layer") or "").lower() == "global" else f"{h.get('layer')}.{h.get('name')}"
        lines.append(f"- {name}({', '.join(h.get('params') or [])}): {h.get('behaviour')} Returns {h.get('returns')}.")
    for gap in plan.get("not_supported") or []:
        lines.append(f"- Not modelled: {gap}")
    lines.append("- The Database layer is the only real proof of what was stored; UI messages and API ok flags are "
                 "the system reporting on itself. Each run starts from a fresh process with the starting data.")
    return "\n".join(lines)
