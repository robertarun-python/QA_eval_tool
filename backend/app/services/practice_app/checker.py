"""
The practice-app inspector: runs checklists against a Round 2 practice app
(the small pretend version of the Round 1 application that candidates'
automation runs against) in every language, and reports what works.

A checklist is one Round 1 test case written as steps the inspector can
perform - language-neutral, with names in Python style:

    {"id": "book-free-slot", "title": "Book an available slot",
     "steps": [
        {"call": "setup"},
        {"call": "UI.login", "args": ["qa.patient.demo@testportal.io", "Px!7mK@2024Test"], "expect": true},
        {"call": "UI.search_doctors", "args": ["Cardiology"], "expect_includes": ["Dr. Sarah Johnson"]},
        {"call": "UI.confirmation", "save_as": "conf", "expect_includes": {"status": "confirmed"}},
        {"call": "Database.find_appointment", "args": [{"ref": "conf.id"}], "expect_includes": {"status": "confirmed"}}
     ]}

Step checks: "expect" (exactly equal), "expect_includes" (dict: these
key/values; list: these items; text: this substring), "expect_excludes"
(the opposite), "expect_error": true (the call must fail). A step with none
of these just has to run without failing. {"ref": "name.field"} passes on a
value saved earlier with "save_as".

Returned values are compared as plain data with snake_case keys, so a Java
object with doctorName and a Python dict with doctor_name look the same.

Each language runs in its own process (runner.py / runner.js /
PracticeRunner.java) - the practice app is generated code and never runs
inside the server. No AI calls anywhere in this module.
"""
import base64
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
LANGUAGES = ("python", "javascript", "java")
_TIMEOUT_SECONDS = 180


@dataclass
class ChecklistResult:
    id: str
    title: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    values: list = field(default_factory=list)  # what each step returned, for cross-language comparison


@dataclass
class LanguageReport:
    language: str
    results: list[ChecklistResult] = field(default_factory=list)
    error: str | None = None  # the app couldn't be run at all in this language

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def total(self) -> int:
        return len(self.results)


@dataclass
class InspectionReport:
    languages: dict[str, LanguageReport]
    differences: list[str]  # places where the languages behave differently

    @property
    def all_passed(self) -> bool:
        return (
            not self.differences
            and all(r.error is None and r.passed == r.total for r in self.languages.values())
        )

    def summary(self) -> str:
        lines = []
        for lang, report in self.languages.items():
            status = report.error or f"{report.passed} of {report.total} checklists pass"
            lines.append(f"{lang}: {status}")
        if self.differences:
            lines.append(f"{len(self.differences)} difference(s) between languages")
        return "; ".join(lines)


# ---- comparing values ------------------------------------------------------

def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b if isinstance(a, bool) and isinstance(b, bool) else False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _includes(actual, expected) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _includes(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and all(any(_includes(a, e) for a in actual) for e in expected)
    if isinstance(expected, str) and isinstance(actual, str):
        return expected in actual
    return _same(actual, expected)


def _excludes(actual, expected) -> bool:
    if isinstance(expected, list) and isinstance(actual, list):
        return not any(any(_includes(a, e) for a in actual) for e in expected)
    if isinstance(expected, dict):
        return not (isinstance(actual, dict) and any(k in actual and _includes(actual[k], v) for k, v in expected.items()))
    if isinstance(expected, str) and isinstance(actual, str):
        return expected not in actual
    return not _same(actual, expected)


def _short(value) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 160 else text[:157] + "..."


class _Unresolved(Exception):
    pass


def _lookup(path: str, saved: dict):
    """A saved value by "name.field.0.field" path (plain data, as the runners return it)."""
    first, *rest = str(path).split(".")
    if first not in saved:
        raise _Unresolved(f"{{\"ref\": \"{path}\"}} - no earlier step saved {first!r}")
    value = saved[first]
    for part in rest:
        if isinstance(value, list) and part.isdigit():
            value = value[int(part)] if int(part) < len(value) else None
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            value = None
    return value


def _with_refs(expected, saved: dict):
    """An expectation with every {"ref": ...} replaced by the saved value it names -
    e.g. "the count now equals the count saved before"."""
    if isinstance(expected, dict):
        if set(expected) == {"ref"}:
            return _lookup(expected["ref"], saved)
        return {k: _with_refs(v, saved) for k, v in expected.items()}
    if isinstance(expected, list):
        return [_with_refs(v, saved) for v in expected]
    return expected


def for_runner(checklist: dict) -> dict:
    """What the language runners run: only the steps that call a helper -
    "check" steps (a look at a value saved earlier) are judged here. A name a
    check step saved is rewritten, in later arguments, into the path it points
    to, so every runner can resolve it (measured: a check-saved id passed to
    the app crashed Python's runner and became null in JavaScript's)."""
    aliases: dict[str, str] = {}

    def unalias(path: str) -> str:
        first, _, rest = str(path).partition(".")
        if first in aliases:
            return unalias(aliases[first] + ("." + rest if rest else ""))
        return str(path)

    def rewrite(value):
        if isinstance(value, dict):
            if set(value) == {"ref"}:
                return {"ref": unalias(value["ref"])}
            return {k: rewrite(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        return value

    steps = []
    for step in checklist["steps"]:
        if "check" in step:
            ref = step["check"].get("ref") if isinstance(step["check"], dict) else None
            if step.get("save_as") and ref is not None:
                aliases[step["save_as"]] = unalias(ref)
            continue
        steps.append({**step, "args": rewrite(step.get("args") or [])} if step.get("args") else step)
    return {**checklist, "steps": steps}


def _judge(checklist: dict, raw: dict) -> ChecklistResult:
    result = ChecklistResult(id=checklist["id"], title=checklist.get("title", checklist["id"]), passed=True)
    if raw.get("load_error"):
        result.passed = False
        result.failures.append(f"the app failed to load: {raw['load_error']}")
        return result
    outcomes = raw.get("steps", [])
    saved: dict = {}
    ran = 0  # helper calls consumed from the runner's outcomes
    for n, step in enumerate(checklist["steps"], start=1):
        where = f"step {n} ({step.get('call') or 'check'})"
        if "check" in step:
            try:
                value = _with_refs(step["check"], saved)
            except _Unresolved as missing:
                result.passed = False
                result.failures.append(f"{where} refers to {missing}")
                break
        else:
            if ran >= len(outcomes):
                result.passed = False
                result.failures.append(f"{where} never ran - an earlier step failed")
                break
            outcome = outcomes[ran]
            ran += 1
            if "error" in outcome:
                result.values.append({"error": True})
                if not step.get("expect_error"):
                    result.passed = False
                    result.failures.append(f"{where} failed: {outcome['error']}")
                continue
            value = outcome.get("value")
        result.values.append(value)
        if step.get("save_as"):
            saved[step["save_as"]] = value
        try:
            step = {**step, **{k: _with_refs(step[k], saved) for k in ("expect", "expect_includes", "expect_excludes") if k in step}}
        except _Unresolved as missing:
            result.passed = False
            result.failures.append(f"{where} expects {missing}")
            continue
        if step.get("expect_error"):
            result.passed = False
            result.failures.append(f"{where} should have failed, but returned {_short(value)}")
        if "expect" in step and not _same(value, step["expect"]):
            result.passed = False
            result.failures.append(f"{where} returned {_short(value)}, expected {_short(step['expect'])}")
        if "expect_includes" in step and not _includes(value, step["expect_includes"]):
            result.passed = False
            result.failures.append(f"{where} returned {_short(value)}, which should include {_short(step['expect_includes'])}")
        if "expect_excludes" in step and not _excludes(value, step["expect_excludes"]):
            result.passed = False
            result.failures.append(f"{where} returned {_short(value)}, which should not include {_short(step['expect_excludes'])}")
    return result


# ---- running each language ---------------------------------------------------

def _run(cmd: list[str], stdin: str | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, cwd=cwd)


def _write_support(folder: Path, support: dict[str, str] | None) -> list[str]:
    """The app's engine file(s), next to the candidate file - as when a candidate's code runs."""
    for name, code in (support or {}).items():
        (folder / name).write_text(code, encoding="utf-8")
    return sorted(support or {})


def _raw_python(env_code: str, checklists: list[dict], tmp: Path, support: dict[str, str] | None = None) -> list[dict]:
    env = tmp / "practice_app.py"
    env.write_text(env_code, encoding="utf-8")
    modules = [Path(n).stem for n in _write_support(tmp, support)]
    proc = _run([sys.executable, str(HERE / "runner.py")],
                stdin=json.dumps({"env_path": str(env), "checklists": checklists, "fresh_modules": modules}))
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-600:] or "the Python runner failed")
    return json.loads(proc.stdout)


def _raw_javascript(env_code: str, checklists: list[dict], tmp: Path, support: dict[str, str] | None = None) -> list[dict]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is not installed on this server")
    env = tmp / "practice_app.js"
    env.write_text(env_code, encoding="utf-8")
    fresh = [str(tmp / n) for n in _write_support(tmp, support)]
    proc = _run([node, str(HERE / "runner.js")], stdin=json.dumps({"env_path": str(env), "checklists": checklists, "fresh_modules": fresh}))
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-600:] or "the JavaScript runner failed")
    return json.loads(proc.stdout)


def _java_arg(arg) -> str:
    if arg is None:
        return "n"
    if isinstance(arg, dict) and "ref" in arg:
        return "r:" + base64.b64encode(arg["ref"].encode()).decode()
    if isinstance(arg, bool):
        return f"b:{str(arg).lower()}"
    if isinstance(arg, int):
        return f"i:{arg}"
    if isinstance(arg, float):
        return f"d:{arg}"
    return "s:" + base64.b64encode(str(arg).encode("utf-8")).decode()


def _raw_java(env_code: str, checklists: list[dict], tmp: Path, support: dict[str, str] | None = None) -> list[dict]:
    javac, java = shutil.which("javac"), shutil.which("java")
    if not (javac and java):
        raise RuntimeError("Java is not installed on this server")
    app_dir, runner_dir = tmp / "app", tmp / "runner"
    app_dir.mkdir()
    runner_dir.mkdir()
    (app_dir / "Main.java").write_text(env_code, encoding="utf-8")
    sources = [str(app_dir / "Main.java")] + [str(app_dir / n) for n in _write_support(app_dir, support) if n.endswith(".java")]
    for cmd in ([javac, "-nowarn", "-d", str(app_dir)] + sources,
                [javac, "-nowarn", "-d", str(runner_dir), str(HERE / "PracticeRunner.java")]):
        proc = _run(cmd)
        if proc.returncode != 0:
            raise RuntimeError("Java compile failed: " + proc.stderr.strip()[-600:])
    lines = []
    for checklist in checklists:
        lines.append(f"S\t{checklist['id']}")
        for step in checklist["steps"]:
            args = [_java_arg(a) for a in step.get("args", [])]
            lines.append("\t".join(["C", step["call"], step.get("save_as", "")] + args))
    job = tmp / "job.txt"
    job.write_text("\n".join(lines) + "\n", encoding="utf-8")
    proc = _run([java, "-cp", str(runner_dir), "PracticeRunner", str(app_dir), str(job)])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-600:] or "the Java runner failed")
    return json.loads(proc.stdout)


_RAW = {"python": _raw_python, "javascript": _raw_javascript, "java": _raw_java}


def inspect_language(language: str, env_code: str, checklists: list[dict], support: dict[str, str] | None = None) -> LanguageReport:
    report = LanguageReport(language=language)
    with tempfile.TemporaryDirectory(prefix=f"practice_{language}_") as tmp:
        try:
            raw = {r["id"]: r for r in _RAW[language](env_code, [for_runner(c) for c in checklists], Path(tmp), support)}
        except (RuntimeError, subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:
            report.error = f"couldn't run the {language} app: {e}"
            return report
    report.results = [_judge(c, raw.get(c["id"], {"steps": []})) for c in checklists]
    return report


def inspect(env_code_by_language: dict[str, str], checklists: list[dict],
            support_by_language: dict[str, dict[str, str]] | None = None) -> InspectionReport:
    """Runs every checklist in every language provided, and lists anywhere
    the languages disagree with each other - even where no checklist says
    what the right answer is, a candidate must get the same behaviour
    whichever language they chose. support_by_language: files placed next to
    each language's file (an engine-built app's engine file)."""
    reports = {lang: inspect_language(lang, code, checklists, (support_by_language or {}).get(lang))
               for lang, code in env_code_by_language.items() if lang in _RAW}
    differences = []
    runnable = [r for r in reports.values() if r.error is None]
    for index, checklist in enumerate(checklists):
        per_lang = {r.language: r.results[index].values for r in runnable}
        if len(per_lang) < 2:
            continue
        for n, step in enumerate(checklist["steps"]):
            values = {lang: vals[n] for lang, vals in per_lang.items() if n < len(vals)}
            if len(values) < 2:
                continue
            first_lang, first = next(iter(values.items()))
            for lang, value in values.items():
                if not _same(value, first):
                    differences.append(
                        f"{checklist['id']} step {n + 1} ({step.get('call') or 'check'}): {first_lang} gave {_short(first)}, "
                        f"{lang} gave {_short(value)}"
                    )
                    break
    return InspectionReport(languages=reports, differences=differences)
