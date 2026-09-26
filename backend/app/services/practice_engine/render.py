"""
Turns a validated scenario description into the practice-app files for each
language - by template, never by the AI. Two files per language:

- the CANDIDATE FILE (main.py / main.js / Main.java): what the candidate sees
  and edits and the Round 2 assistant rewrites - the app's accounts, pages and
  every helper with what it does, one import line, and the TODO block. Short
  on purpose: the assistant returns the whole file on every turn.
- the ENGINE FILE (practice_engine.py / practice_engine.js /
  PracticeEngine.java): the engine, the description and the helpers
  (UI / API / Database / Test, setup, teardown), placed next to the candidate
  file whenever it runs. Candidates test the app through its helpers, as
  they would a real application - its rules and data aren't in their file.
"""
import json
import re
from pathlib import Path

_HERE = Path(__file__).parent


def snake(name: str) -> str:
    name = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_")
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def camel(name: str) -> str:
    head, *rest = snake(name).split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def query_params(query: dict) -> list[str]:
    first = [query["match"]["input"]] if query.get("match") else ([query["key_input"]] if query.get("key_input") else [])
    return first + [i["name"] for i in query.get("inputs") or [] if i["name"] not in first]


def action_params(action: dict) -> list[str]:
    return [i["name"] for i in action.get("inputs") or []]


def helpers(spec: dict) -> list[dict]:
    """Every helper a candidate (and a checklist) can call, language-neutral:
    {"call": "UI.borrow_book", "params": [...], "doc": "..."} with snake_case names."""
    out = [
        {"call": "UI.open", "params": ["page"], "doc": "Go to a page: " + ", ".join(spec.get("pages") or [])},
        {"call": "UI.current_page", "params": [], "doc": "The page currently shown."},
        {"call": "UI.visible_message", "params": [], "doc": "The message currently shown (success or error), or an empty string."},
        {"call": "UI.last_result", "params": [], "doc": "Details shown after the last successful action (e.g. a confirmation number)."},
    ]
    if spec.get("users"):
        out += [
            {"call": "UI.login", "params": ["login", "password"], "doc": "Fill in and submit the login form. True if signed in."},
            {"call": "UI.logout", "params": [], "doc": "Sign out."},
            {"call": "UI.signed_in_user", "params": [], "doc": "The name shown for the signed-in user, or None."},
            {"call": "API.login", "params": ["login", "password"], "doc": "Sign in through the API: {ok, error, user}."},
        ]
    for q in spec.get("queries") or []:
        params = query_params(q)
        doc = q.get("description") or q.get("label") or q["name"].replace("_", " ")
        shown = ", ".join(f["name"] if isinstance(f, dict) else f for f in q.get("show") or []) or "every field"
        what = f"the {q['entity']} record" if q.get("key_input") else f"the list of {q['entity']} records"
        where = f" (on the {q['page']} page)" if q.get("page") else ""
        out.append({"call": f"UI.{snake(q['name'])}", "params": params, "doc": f"{doc}{where}. Returns {what} shown ({shown})."})
        out.append({"call": f"API.{snake(q['name'])}", "params": params, "doc": f"{doc}: {{ok, error, result, message}}; result is {what} ({shown})."})
    for a in spec.get("actions") or []:
        params = action_params(a)
        doc = a.get("description") or a.get("label") or a["name"].replace("_", " ")
        where = f" (on the {a['page']} page)" if a.get("page") else ""
        extra = "".join(f", {k}" for k in a.get("returns") or {})
        out.append({"call": f"UI.{snake(a['name'])}", "params": params, "doc": f"{doc}{where}. True if it succeeded; see UI.visible_message()"
                    + (f" and UI.last_result() ({extra[2:]})." if extra else ".")})
        out.append({"call": f"API.{snake(a['name'])}", "params": params, "doc": f"{doc}: {{ok, error, message{extra}}}."})
    for entity in spec.get("entities") or {}:
        e = snake(entity)
        out += [
            {"call": f"Database.get_{e}", "params": ["key"], "doc": f"The stored {entity} with this key, or None."},
            {"call": f"Database.find_{e}", "params": ["field", "value"], "doc": f"Stored {entity} records where field == value."},
            {"call": f"Database.count_{e}", "params": ["field", "value"], "doc": f"How many {entity} records are stored (optionally where field == value)."},
            {"call": f"Database.all_{e}", "params": [], "doc": f"Every stored {entity} record."},
        ]
    out += [
        {"call": "Database.outbox", "params": [], "doc": "Emails, SMS and alerts the app has sent."},
        {"call": "Test.advance_minutes", "params": ["minutes"], "doc": "Move the app's clock forward (e.g. to expire an OTP or a session)."},
        {"call": "Test.simulate", "params": ["failure"], "doc": "Switch on a simulated failure: " + (", ".join(f["name"] for f in spec.get("faults") or []) or "none defined")},
        {"call": "setup", "params": [], "doc": "Reset the app to its starting state. Call at the start of every test."},
        {"call": "teardown", "params": ["id"], "doc": "Remove one record your test created (by its id)."},
    ]
    order = {"UI": 0, "API": 1, "Database": 2, "Test": 3}
    return sorted(out, key=lambda h: order.get(h["call"].split(".")[0] if "." in h["call"] else "", 4))  # grouped; stable within a group


LANGUAGES = ("python", "javascript", "java")
ENGINE_FILE = {"python": "practice_engine.py", "javascript": "practice_engine.js", "java": "PracticeEngine.java"}
_GROUP_TITLES = {"UI": "What a user does and sees on the screens.", "API": "The same operations called directly; each returns a result.",
                 "Database": "What is really stored - for checking your test's effect.", "Test": "Test-support hooks."}


def files(spec: dict, language: str) -> tuple[str, dict[str, str]]:
    """(the candidate file, {engine file name: its code}) for one language."""
    return {"python": _python, "javascript": _javascript, "java": _java}[language](spec)


def _accounts(spec: dict) -> list[str]:
    users = spec.get("users")
    if not users:
        return []
    rows = (spec.get("data") or {}).get(users["entity"]) or []
    name = users.get("name_field")
    return [f"{r.get(users.get('login_field', 'email'))} / {r.get(users.get('password_field', 'password'))}"
            + (f" ({r.get(name)})" if name and r.get(name) else "") for r in rows]


def _signature(h: dict, language: str, java_returns: dict) -> str:
    group, _, name = h["call"].rpartition(".")
    if language == "python":
        return f"{h['call']}({', '.join(h['params'])})"
    params = [camel(p) for p in h["params"]]
    call = f"{group + '.' if group else ''}{camel(name)}"
    if language == "java":
        return f"{java_returns.get(h['call'], 'Object')} {call}({', '.join('Object ' + p for p in params)})"
    return f"{call}({', '.join(params)})"


def _doc(text: str, language: str) -> str:
    """A helper's description in the language's own naming (UI.visibleMessage(), null)."""
    if language == "python":
        return text
    text = re.sub(r"\b(UI|API|Database|Test)\.(\w+)\(", lambda m: f"{m.group(1)}.{camel(m.group(2))}(", text)
    return re.sub(r"\bNone\b", "null", text)


def _header_lines(spec: dict, language: str) -> list[str]:
    names = {"python": "Python", "javascript": "JavaScript", "java": "Java"}
    lines = [f"Automation environment ({names[language]}) - {spec['app_name']} - provided for you, already working.", "",
             "The application runs in-process (its screens, API and database), so your automation runs the same way",
             "every time, with no real network calls. Test it through the helpers below, as you would a real application.", ""]
    accounts = _accounts(spec)
    if accounts:
        lines += ["Test accounts (login / password):"] + [f"  {a}" for a in accounts] + [""]
    lines += [f"Web address: {spec.get('base_url', '')}", "Pages: " + ", ".join(spec.get("pages") or []), ""]
    if spec.get("faults"):
        lines += ["Simulated failures you can switch on with Test.simulate(...):"]
        lines += [f"  {f['name']}" for f in spec["faults"]] + [""]
    lines += ["Records (for Database helpers):"]
    for entity, e in (spec.get("entities") or {}).items():
        lines += [f"  {entity} (key: {e.get('key', 'id')}): " + ", ".join(e.get("fields") or {})]
    lines += [""]
    java_returns = _java_returns(spec) if language == "java" else {}
    lines += ["Helpers:"]
    current = None
    for h in helpers(spec):
        group = h["call"].split(".")[0] if "." in h["call"] else "(test lifecycle)"
        if group != current:
            current = group
            lines += ["", f"  {group}" + (f" - {_GROUP_TITLES[group]}" if group in _GROUP_TITLES else "")]
        lines += [f"    {_signature(h, language, java_returns)}", f"        {_doc(h['doc'], language)}"]
    if language != "python":
        lines += ["", "Returned records use the field names shown by Database helpers (snake_case, e.g. available_copies)."]
    lines += ["", "Start every test with setup(). Write your automated test(s) at the bottom, under the TODO marker."]
    return lines


# ---------------------------------------------------------------------------- Python

def _python(spec: dict) -> tuple[str, dict[str, str]]:
    runtime = (_HERE / "runtime.py").read_text(encoding="utf-8")
    runtime = runtime.split('"""', 2)[2].lstrip("\n")  # drop the module docstring
    groups: dict[str, list[str]] = {"UI": [], "API": [], "Database": [], "Test": []}
    doc = {h["call"]: h["doc"] for h in helpers(spec)}

    def method(group: str, name: str, params: list[str], body: str) -> None:
        groups[group].append(f"    @staticmethod\n    def {name}({', '.join(params)}):\n        {json.dumps(doc.get(f'{group}.{name}', ''))}\n"
                             f"        return {body}\n")

    method("UI", "open", ["page"], "_E.ui_open(page)")
    method("UI", "current_page", [], "_E.page")
    method("UI", "visible_message", [], "_E.message")
    method("UI", "last_result", [], "dict(_E.last)")
    if spec.get("users"):
        name_field = spec["users"].get("name_field", spec["users"].get("login_field", "email"))
        method("UI", "login", ["login", "password"], "_E.ui_login(login, password)")
        method("UI", "logout", [], "_E.ui_logout()")
        method("UI", "signed_in_user", [], f"None if _E.user is None else _E.user.get({json.dumps(name_field)})")
        method("API", "login", ["login", "password"], "_E.api_login(login, password)")
    for kind, ui, api, params_of in (("queries", "ui_query", "api_query", query_params), ("actions", "ui_action", "api_action", action_params)):
        for item in spec.get(kind) or []:
            params = params_of(item)
            args = "{" + ", ".join(f"{json.dumps(p)}: {p}" for p in params) + "}"
            method("UI", snake(item["name"]), params, f"_E.{ui}({json.dumps(item['name'])}, {args})")
            method("API", snake(item["name"]), params, f"_E.{api}({json.dumps(item['name'])}, {args})")
    for entity in spec.get("entities") or {}:
        e, en = snake(entity), json.dumps(entity)
        method("Database", f"get_{e}", ["key"], f"_E.db_get({en}, key)")
        method("Database", f"find_{e}", ["field", "value"], f"_E.db_find({en}, field, value)")
        method("Database", f"count_{e}", ["field=None", "value=None"], f"_E.db_count({en}, field, value)")
        method("Database", f"all_{e}", [], f"_E.db_all({en})")
    method("Database", "outbox", [], "[dict(m) for m in _E.outbox]")
    method("Test", "advance_minutes", ["minutes"], "_E.advance_minutes(minutes)")
    method("Test", "simulate", ["failure"], "_E.simulate(failure)")

    engine = ['"""The practice app (engine, data and helpers) - provided; not part of your test code."""\n', runtime]
    engine.append(f"\n\nSPEC = __import__('json').loads({json.dumps(json.dumps(spec, ensure_ascii=False))})\n_E = Engine(SPEC)\n")
    for group, methods in groups.items():
        engine.append(f"\n\nclass {group}:\n    {json.dumps(_GROUP_TITLES[group])}\n\n" + "\n".join(methods))
    engine.append('\n\ndef setup():\n    """Reset the app to its starting state. Call at the start of every test."""\n    _E.reset()\n')
    engine.append('\n\ndef teardown(id=None):\n    """Remove one record your test created (by its id)."""\n    _E.teardown(id)\n')

    candidate = ('"""\n' + "\n".join(_header_lines(spec, "python")) + '\n"""\n'
                 "from practice_engine import API, UI, Database, Test, setup, teardown  # the practice app - provided, already working\n"
                 "\n\n# " + "-" * 75 + "\n# TODO: write your automated test(s) below, then call them from __main__.\n# " + "-" * 75
                 + "\n\n\nif __name__ == \"__main__\":\n    pass\n")
    return candidate, {ENGINE_FILE["python"]: "".join(engine)}


# ---------------------------------------------------------------------------- JavaScript

def _javascript(spec: dict) -> tuple[str, dict[str, str]]:
    runtime = (_HERE / "runtime.js").read_text(encoding="utf-8")
    doc = {h["call"]: h["doc"] for h in helpers(spec)}
    groups: dict[str, list[str]] = {"UI": [], "API": [], "Database": [], "Test": []}

    def method(group: str, name: str, params: list[str], body: str, js_params: list[str] | None = None) -> None:
        text = _doc(doc.get(f"{group}.{name}", ""), "javascript").replace("*/", "* /")
        signature = ", ".join(js_params if js_params is not None else [camel(p) for p in params])
        groups[group].append(f"  /** {text} */\n  {camel(name)}({signature}) {{ return {body}; }},\n")

    def args_of(params: list[str]) -> str:
        return "{ " + ", ".join(f"{json.dumps(p)}: {camel(p)}" for p in params) + " }"

    method("UI", "open", ["page"], "_E.uiOpen(page)")
    method("UI", "current_page", [], "_E.page")
    method("UI", "visible_message", [], "_E.message")
    method("UI", "last_result", [], "Object.assign({}, _E.last)")
    if spec.get("users"):
        name_field = json.dumps(spec["users"].get("name_field", spec["users"].get("login_field", "email")))
        method("UI", "login", ["login", "password"], "_E.uiLogin(login, password)")
        method("UI", "logout", [], "_E.uiLogout()")
        method("UI", "signed_in_user", [], f"_E.user === null ? null : (_E.user[{name_field}] === undefined ? null : _E.user[{name_field}])")
        method("API", "login", ["login", "password"], "_E.apiLogin(login, password)")
    for kind, ui, api, params_of in (("queries", "uiQuery", "apiQuery", query_params), ("actions", "uiAction", "apiAction", action_params)):
        for item in spec.get(kind) or []:
            params = params_of(item)
            method("UI", snake(item["name"]), params, f"_E.{ui}({json.dumps(item['name'])}, {args_of(params)})")
            method("API", snake(item["name"]), params, f"_E.{api}({json.dumps(item['name'])}, {args_of(params)})")
    for entity in spec.get("entities") or {}:
        e, en = snake(entity), json.dumps(entity)
        method("Database", f"get_{e}", ["key"], f"_E.dbGet({en}, key)")
        method("Database", f"find_{e}", ["field", "value"], f"_E.dbFind({en}, field, value)")
        method("Database", f"count_{e}", ["field", "value"], f"_E.dbCount({en}, field, value)", ["field = null", "value = null"])
        method("Database", f"all_{e}", [], f"_E.dbAll({en})")
    method("Database", "outbox", [], "_E.outbox.map((m) => Object.assign({}, m))")
    method("Test", "advance_minutes", ["minutes"], "_E.advanceMinutes(minutes)")
    method("Test", "simulate", ["failure"], "_E.simulate(failure)")

    engine = ["// The practice app (engine, data and helpers) - provided; not part of your test code.\n", runtime,
              f"\nconst SPEC = {json.dumps(spec, ensure_ascii=False)};\nconst _E = new Engine(SPEC);\n"]
    for group, methods in groups.items():
        engine.append(f"\n// {_GROUP_TITLES[group]}\nconst {group} = {{\n" + "".join(methods) + "};\n")
    engine.append("\nfunction setup() { _E.reset(); }\nfunction teardown(id = null) { _E.teardown(id); }\n"
                  "\nmodule.exports = { UI, API, Database, Test, setup, teardown };\n")

    header = "\n".join(" * " + line if line else " *" for line in _header_lines(spec, "javascript")).replace("*/", "* /")
    candidate = (f"/**\n{header}\n */\n"
                 'const { UI, API, Database, Test, setup, teardown } = require("./practice_engine.js");  // the practice app - provided, already working\n'
                 "\n// " + "-" * 75 + "\n// TODO: write your automated test(s) below.\n// " + "-" * 75 + "\n")
    return candidate, {ENGINE_FILE["javascript"]: "".join(engine)}


# ---------------------------------------------------------------------------- Java

def _java_string(text: str) -> str:
    out = []
    for ch in text:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ord(ch) < 0x20 or ord(ch) > 0x7E:
            b = ch.encode("utf-16-be")
            out.append("".join("\\u%04x" % int.from_bytes(b[i:i + 2], "big") for i in range(0, len(b), 2)))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


_ROW, _ROWS = "Map<String, Object>", "List<Map<String, Object>>"


def _java_returns(spec: dict) -> dict[str, str]:
    out = {"UI.open": "boolean", "UI.current_page": "String", "UI.visible_message": "String", "UI.last_result": _ROW,
           "UI.login": "boolean", "UI.logout": "boolean", "UI.signed_in_user": "Object", "API.login": _ROW,
           "Database.outbox": _ROWS, "Test.advance_minutes": "String", "Test.simulate": "boolean", "setup": "void", "teardown": "void"}
    for q in spec.get("queries") or []:
        out[f"UI.{snake(q['name'])}"] = _ROW if q.get("key_input") else _ROWS
        out[f"API.{snake(q['name'])}"] = _ROW
    for a in spec.get("actions") or []:
        out[f"UI.{snake(a['name'])}"] = "boolean"
        out[f"API.{snake(a['name'])}"] = _ROW
    for entity in spec.get("entities") or {}:
        e = snake(entity)
        out.update({f"Database.get_{e}": _ROW, f"Database.find_{e}": _ROWS, f"Database.count_{e}": "long", f"Database.all_{e}": _ROWS})
    return out


def _java(spec: dict) -> tuple[str, dict[str, str]]:
    runtime = (_HERE / "runtime_java.txt").read_text(encoding="utf-8")
    doc = {h["call"]: h["doc"] for h in helpers(spec)}
    returns = _java_returns(spec)
    groups: dict[str, list[str]] = {"UI": [], "API": [], "Database": [], "Test": []}

    def method(group: str, name: str, params: list[str], body: str) -> None:
        text = _doc(doc.get(f"{group}.{name}", ""), "java").replace("*/", "* /")
        kind = returns[f"{group}.{name}"]
        cast = "" if kind == "Object" else f"({kind}) (Object) "
        groups[group].append(f"    /** {text} */\n    @SuppressWarnings(\"unchecked\")\n"
                             f"    public static {kind} {camel(name)}({', '.join(f'Object {camel(p)}' for p in params)}) {{ return {cast}{body}; }}\n")

    def args_of(params: list[str]) -> str:
        return "PracticeEngine.inputs(" + ", ".join(f"{_java_string(p)}, {camel(p)}" for p in params) + ")"

    e_ = "PracticeEngine._E"
    method("UI", "open", ["page"], f"{e_}.uiOpen(page)")
    method("UI", "current_page", [], f"{e_}.page")
    method("UI", "visible_message", [], f"{e_}.message")
    method("UI", "last_result", [], f"new LinkedHashMap<>({e_}.last)")
    if spec.get("users"):
        name_field = _java_string(spec["users"].get("name_field", spec["users"].get("login_field", "email")))
        method("UI", "login", ["login", "password"], f"{e_}.uiLogin(login, password)")
        method("UI", "logout", [], f"{e_}.uiLogout()")
        method("UI", "signed_in_user", [], f"{e_}.user == null ? null : {e_}.user.get({name_field})")
        method("API", "login", ["login", "password"], f"{e_}.apiLogin(login, password)")
    for kind, ui, api, params_of in (("queries", "uiQuery", "apiQuery", query_params), ("actions", "uiAction", "apiAction", action_params)):
        for item in spec.get(kind) or []:
            params, n = params_of(item), _java_string(item["name"])
            method("UI", snake(item["name"]), params, f"{e_}.{ui}({n}, {args_of(params)})")
            method("API", snake(item["name"]), params, f"{e_}.{api}({n}, {args_of(params)})")
    for entity in spec.get("entities") or {}:
        e, en = snake(entity), _java_string(entity)
        method("Database", f"get_{e}", ["key"], f"{e_}.dbGet({en}, key)")
        method("Database", f"find_{e}", ["field", "value"], f"{e_}.dbFind({en}, field, value)")
        method("Database", f"count_{e}", ["field", "value"], f"{e_}.dbCount({en}, field, value)")
        groups["Database"].append(f"    /** How many {entity} records are stored. */\n"
                                  f"    public static long {camel('count_' + e)}() {{ return {e_}.dbCount({en}, null, null); }}\n")
        method("Database", f"all_{e}", [], f"{e_}.dbAll({en})")
    method("Database", "outbox", [], f"{e_}.outboxCopy()")
    method("Test", "advance_minutes", ["minutes"], f"{e_}.advanceMinutes(minutes)")
    method("Test", "simulate", ["failure"], f"{e_}.simulate(failure)")

    text = json.dumps(spec, ensure_ascii=False)
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [""]
    engine = ["// The practice app (engine, data and helpers) - provided; not part of your test code.\n",
              "import java.util.*;\n\nfinal class PracticeEngine {\n", runtime,
              "\n    static String specText() {\n        StringBuilder b = new StringBuilder();\n",
              "".join(f"        b.append({_java_string(c)});\n" for c in chunks),
              "        return b.toString();\n    }\n\n    static final Engine _E = new Engine(map(Json.parse(specText())));\n}\n"]
    for group, methods in groups.items():
        engine.append(f"\n/** {_GROUP_TITLES[group]} */\nfinal class {group} {{\n" + "\n".join(methods) + "}\n")
    engine.append("\n/** Test lifecycle. */\nfinal class PracticeApp {\n"
                  "    static void setup() { PracticeEngine._E.reset(); }\n"
                  "    static void teardown(Object id) { PracticeEngine._E.teardown(id); }\n}\n")

    header = "\n".join(" * " + line if line else " *" for line in _header_lines(spec, "java")).replace("*/", "* /")
    candidate = ("import java.util.*;\n\n"
                 f"/**\n{header}\n */\npublic class Main {{\n"
                 "    /** Reset the app to its starting state. Call at the start of every test. */\n"
                 "    public static void setup() { PracticeApp.setup(); }\n\n"
                 "    /** Remove one record your test created (by its id). */\n"
                 "    public static void teardown(Object id) { PracticeApp.teardown(id); }\n\n"
                 "    // " + "-" * 71 + "\n    // TODO: write your automated test(s) below, then call them from main().\n    // " + "-" * 71 + "\n\n"
                 "    public static void main(String[] args) {\n    }\n}\n")
    return candidate, {ENGINE_FILE["java"]: "".join(engine)}
