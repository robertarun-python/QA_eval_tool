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
_PLAN_TOKENS = 8192
_CHECKLIST_TOKENS = 16384
_CODE_TOKENS = 16384

LANGUAGE_NAMES = {"python": "Python", "javascript": "JavaScript", "java": "Java"}
_FENCES = {"python": "python", "javascript": "javascript", "java": "java"}

# Style examples: the hand-written Doctor Appointment practice app. The AI
# copies its structure, not its content.
_EXAMPLE_FILES = {lang: f"round2_automation_helpers_appointments_{lang}.txt" for lang in checker.LANGUAGES}

_LANGUAGE_RULES = {
    "python": "Standard library only.",
    "javascript": (
        "Plain Node.js script: no imports, no exports, no async, no classes needed. Declare const UI = {...}, "
        "const API = {...}, const Database = {...}, and top-level function setup() and function teardown(id = null). "
        "Return plain objects and arrays, with camelCase field names."
    ),
    "java": (
        "One file. The public class must be named Main, with every helper group as a nested static class "
        "(Main.UI, Main.API, Main.Database) holding static methods, and static void setup() and "
        "static void teardown(String id) directly on Main. Only java.util imports. Keep an empty "
        "public static void main(String[] args). Return java.util types or small static nested classes with "
        "plain fields (no getters). Use nullable types (String, Integer, Boolean) wherever the Python version can "
        "return None, and accept null for optional parameters."
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


def validate_checklists(plan: dict, checklists: list, reference_cases: list[dict]) -> tuple[list, list, list[str]]:
    """Splits the AI's checklists into runnable ones and unsupported cases,
    and lists problems: a call to a helper the plan doesn't have, a missing
    or duplicate id, or a reference case with no checklist."""
    helpers = _helper_names(plan)
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
        else:
            runnable.append(c)
    covered = {(c.get("title") or "").strip().lower() for c in runnable} | {u["title"].strip().lower() for u in unsupported}
    for case in reference_cases:
        if (case.get("title") or "").strip().lower() not in covered:
            problems.append(f"no checklist for reference test case {case.get('title')!r}")
    return runnable, unsupported, problems


def _problems_for(language: str, report: checker.InspectionReport) -> list[str]:
    lang_report = report.languages.get(language)
    if lang_report is None:
        return []
    if lang_report.error:
        return [lang_report.error]
    problems = [f"{r.id}: {f}" for r in lang_report.results for f in r.failures]
    problems += [d for d in report.differences if f" {language} gave " in d]
    return problems


def generate(title: str, description: str, reference_cases: list[dict]) -> PracticeAppResult:
    result = PracticeAppResult()
    cases_text = _format_cases(reference_cases)

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

    try:
        # 1. Plan
        plan = llm_service._parse_json_response(call(_render(
            llm_service._load_prompt("practice_app_plan.txt"),
            title=title, description=description, reference_cases=cases_text,
        ), _PLAN_TOKENS))
        if not isinstance(plan, dict) or not plan.get("helpers"):
            raise ValueError("the AI's design had no helpers")
        result.plan = plan
        plan_text = json.dumps(plan, indent=1, ensure_ascii=False)
        result.log.append(f"designed {plan.get('app_name', title)!r} with {len(plan['helpers'])} helpers")

        # 2. Checklists (one retry if they don't match the design)
        checklist_prompt = _render(llm_service._load_prompt("practice_app_checklists.txt"), plan=plan_text, reference_cases=cases_text)
        raw = llm_service._parse_json_response(call(checklist_prompt, _CHECKLIST_TOKENS))
        runnable, unsupported, problems = validate_checklists(plan, raw, reference_cases)
        if problems:
            result.log.append("checklists needed a second attempt: " + "; ".join(problems[:5]))
            retry = checklist_prompt + "\n\nYour previous attempt had these problems - fix every one:\n" + "\n".join(f"- {p}" for p in problems)
            raw = llm_service._parse_json_response(call(retry, _CHECKLIST_TOKENS))
            runnable, unsupported, problems = validate_checklists(plan, raw, reference_cases)
            for p in problems:
                result.log.append(f"checklist problem left: {p}")
        result.checklists, result.unsupported = runnable, unsupported
        if not runnable:
            raise ValueError("no runnable checklists")
        checklists_text = json.dumps(runnable, indent=1, ensure_ascii=False)
        result.log.append(f"{len(runnable)} checklists, {len(unsupported)} test case(s) not supported")

        # 3. Python, checked and fixed on its own first
        python = _code(call(_render(
            llm_service._load_prompt("practice_app_write_python.txt"),
            plan=plan_text, checklists=checklists_text, example=llm_service._load_prompt(_EXAMPLE_FILES["python"]),
        ), _CODE_TOKENS))
        for round_no in range(MAX_FIX_ROUNDS + 1):
            report = checker.inspect({"python": python}, runnable)
            problems = _problems_for("python", report)
            result.log.append(f"python: {report.languages['python'].passed}/{len(runnable)} checklists pass")
            if not problems or round_no == MAX_FIX_ROUNDS:
                break
            python = fix("python", python, problems)
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

        with ThreadPoolExecutor(max_workers=2) as pool:
            code = {"python": python, **dict(zip(("javascript", "java"), pool.map(translate, ("javascript", "java"))))}

        # 5. All three together; fix whichever translation disagrees
        for round_no in range(MAX_FIX_ROUNDS + 1):
            report = checker.inspect(code, runnable)
            result.log.append("all languages: " + report.summary())
            failing = {lang: _problems_for(lang, report) for lang in ("javascript", "java")}
            failing = {lang: p for lang, p in failing.items() if p}
            if not failing or round_no == MAX_FIX_ROUNDS:
                break
            with ThreadPoolExecutor(max_workers=2) as pool:
                fixed = dict(zip(failing, pool.map(lambda lang: fix(lang, code[lang], failing[lang]), failing)))
            code.update(fixed)

        result.env_code_by_language = code
        result.report = report
        result.ok = report.all_passed
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
