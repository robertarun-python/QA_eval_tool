"""
Turns a validated scenario description into the practice-app file for each
language - by template, never by the AI. The file keeps the long-standing
shape (docstring, UI / API / Database helper groups, setup(), teardown(), the
TODO block) that checker.py, the runners and candidate execution rely on, with
the engine runtime embedded below the helpers.
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
        out.append({"call": f"UI.{snake(q['name'])}", "params": params, "doc": f"{doc} (on the {q.get('page', 'current')} page). Returns what is shown."})
        out.append({"call": f"API.{snake(q['name'])}", "params": params, "doc": f"{doc}: {{ok, error, result, message}}."})
    for a in spec.get("actions") or []:
        params = action_params(a)
        doc = a.get("description") or a.get("label") or a["name"].replace("_", " ")
        out.append({"call": f"UI.{snake(a['name'])}", "params": params, "doc": f"{doc} (on the {a.get('page', 'current')} page). True if it succeeded; see UI.visible_message()."})
        out.append({"call": f"API.{snake(a['name'])}", "params": params, "doc": f"{doc}: {{ok, error, message, ...}}."})
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
    return out


def _accounts(spec: dict) -> list[str]:
    users = spec.get("users")
    if not users:
        return []
    rows = (spec.get("data") or {}).get(users["entity"]) or []
    name = users.get("name_field")
    return [f"{r.get(users.get('login_field', 'email'))} / {r.get(users.get('password_field', 'password'))}"
            + (f" ({r.get(name)})" if name and r.get(name) else "") for r in rows]


def _header_lines(spec: dict, language: str) -> list[str]:
    lines = [f"Automation environment ({language}) - {spec['app_name']} - provided for you, already working.", "",
             "A small simulated version of the application: its screens (UI), its API and its database run",
             "in-process, so your automation runs the same way every time with no real network calls.", ""]
    accounts = _accounts(spec)
    if accounts:
        lines += ["Test accounts:"] + [f"  {a}" for a in accounts] + [""]
    lines += [f"Web address: {spec.get('base_url', '')}", "Pages: " + ", ".join(spec.get("pages") or []), ""]
    if spec.get("faults"):
        lines += ["Simulated failures you can switch on with Test.simulate(...):"]
        lines += [f"  {f['name']}: {f['message']}" for f in spec["faults"]] + [""]
    lines += ["Write your automated test(s) at the bottom, under the TODO marker, using the helpers below.",
              "Do not change the helpers or anything under 'engine internals'."]
    return lines


# ---------------------------------------------------------------------------- Python

def render_python(spec: dict) -> str:
    runtime = (_HERE / "runtime.py").read_text(encoding="utf-8")
    runtime = runtime.split('"""', 2)[2].lstrip("\n")  # drop the module docstring
    groups: dict[str, list[str]] = {"UI": [], "API": [], "Database": [], "Test": []}
    doc = {h["call"]: h["doc"] for h in helpers(spec)}
    ui_extra = ["login", "logout", "signed_in_user"] if spec.get("users") else []

    def method(group: str, name: str, params: list[str], body: str) -> None:
        text = doc.get(f"{group}.{name}", "")
        groups[group].append(f"    @staticmethod\n    def {name}({', '.join(params)}):\n        {json.dumps(text)}\n        return {body}\n")

    method("UI", "open", ["page"], "_E.ui_open(page)")
    method("UI", "current_page", [], "_E.page")
    method("UI", "visible_message", [], "_E.message")
    method("UI", "last_result", [], "dict(_E.last)")
    if "login" in ui_extra:
        method("UI", "login", ["login", "password"], "_E.ui_login(login, password)")
        method("UI", "logout", [], "_E.ui_logout()")
        name_field = spec["users"].get("name_field", spec["users"].get("login_field", "email"))
        method("UI", "signed_in_user", [], f"None if _E.user is None else _E.user.get({json.dumps(name_field)})")
        method("API", "login", ["login", "password"], "_E.api_login(login, password)")
    for q in spec.get("queries") or []:
        params = query_params(q)
        args = "{" + ", ".join(f"{json.dumps(p)}: {p}" for p in params) + "}"
        method("UI", snake(q["name"]), params, f"_E.ui_query({json.dumps(q['name'])}, {args})")
        method("API", snake(q["name"]), params, f"_E.api_query({json.dumps(q['name'])}, {args})")
    for a in spec.get("actions") or []:
        params = action_params(a)
        args = "{" + ", ".join(f"{json.dumps(p)}: {p}" for p in params) + "}"
        method("UI", snake(a["name"]), params, f"_E.ui_action({json.dumps(a['name'])}, {args})")
        method("API", snake(a["name"]), params, f"_E.api_action({json.dumps(a['name'])}, {args})")
    for entity in spec.get("entities") or {}:
        e, en = snake(entity), json.dumps(entity)
        method("Database", f"get_{e}", ["key"], f"_E.db_get({en}, key)")
        method("Database", f"find_{e}", ["field", "value"], f"_E.db_find({en}, field, value)")
        method("Database", f"count_{e}", ["field=None", "value=None"], f"_E.db_count({en}, field, value)")
        method("Database", f"all_{e}", [], f"_E.db_all({en})")
    method("Database", "outbox", [], "[dict(m) for m in _E.outbox]")
    method("Test", "advance_minutes", ["minutes"], "_E.advance_minutes(minutes)")
    method("Test", "simulate", ["failure"], "_E.simulate(failure)")

    titles = {"UI": "What a user does and sees on the screens.", "API": "The same operations called directly; each returns a result dict.",
              "Database": "What is really stored - for checking your test's effect.", "Test": "Test-support hooks."}
    parts = ['"""\n' + "\n".join(_header_lines(spec, "Python")) + '\n"""\n']
    for group, methods in groups.items():
        parts.append(f"\nclass {group}:\n    {json.dumps(titles[group])}\n\n" + "\n".join(methods))
    parts.append('\n\ndef setup():\n    """Reset the app to its starting state. Call at the start of every test."""\n    _E.reset()\n')
    parts.append('\n\ndef teardown(id=None):\n    """Remove one record your test created (by its id)."""\n    _E.teardown(id)\n')
    parts.append("\n\n# " + "=" * 76 + "\n# Engine internals - do not change anything below this line (up to the TODO block).\n# " + "=" * 76 + "\n")
    parts.append(runtime)
    parts.append(f"\n\nSPEC = __import__('json').loads({json.dumps(json.dumps(spec, ensure_ascii=False))})\n_E = Engine(SPEC)\n")
    parts.append("\n\n# " + "-" * 75 + "\n# TODO: write your automated test(s) below, then call them from __main__.\n# " + "-" * 75 + "\n\n\nif __name__ == \"__main__\":\n    pass\n")
    return "".join(parts)


# ---------------------------------------------------------------------------- JavaScript

def render_javascript(spec: dict) -> str:
    runtime = (_HERE / "runtime.js").read_text(encoding="utf-8")
    doc = {h["call"]: h["doc"] for h in helpers(spec)}
    groups: dict[str, list[str]] = {"UI": [], "API": [], "Database": [], "Test": []}

    def method(group: str, name: str, params: list[str], body: str, js_params: list[str] | None = None) -> None:
        text = doc.get(f"{group}.{name}", "").replace("*/", "* /")
        signature = ", ".join(js_params if js_params is not None else [camel(p) for p in params])
        groups[group].append(f"  /** {text} */\n  {camel(name)}({signature}) {{ return {body}; }},\n")

    def args_of(params: list[str]) -> str:
        return "{ " + ", ".join(f"{json.dumps(p)}: {camel(p)}" for p in params) + " }"

    method("UI", "open", ["page"], "_E.uiOpen(page)")
    method("UI", "current_page", [], "_E.page")
    method("UI", "visible_message", [], "_E.message")
    method("UI", "last_result", [], "Object.assign({}, _E.last)")
    if spec.get("users"):
        name_field = spec["users"].get("name_field", spec["users"].get("login_field", "email"))
        method("UI", "login", ["login", "password"], "_E.uiLogin(login, password)")
        method("UI", "logout", [], "_E.uiLogout()")
        method("UI", "signed_in_user", [], f"_E.user === null ? null : (_E.user[{json.dumps(name_field)}] === undefined ? null : _E.user[{json.dumps(name_field)}])")
        method("API", "login", ["login", "password"], "_E.apiLogin(login, password)")
    for q in spec.get("queries") or []:
        params = query_params(q)
        method("UI", snake(q["name"]), params, f"_E.uiQuery({json.dumps(q['name'])}, {args_of(params)})")
        method("API", snake(q["name"]), params, f"_E.apiQuery({json.dumps(q['name'])}, {args_of(params)})")
    for a in spec.get("actions") or []:
        params = action_params(a)
        method("UI", snake(a["name"]), params, f"_E.uiAction({json.dumps(a['name'])}, {args_of(params)})")
        method("API", snake(a["name"]), params, f"_E.apiAction({json.dumps(a['name'])}, {args_of(params)})")
    for entity in spec.get("entities") or {}:
        e, en = snake(entity), json.dumps(entity)
        method("Database", f"get_{e}", ["key"], f"_E.dbGet({en}, key)")
        method("Database", f"find_{e}", ["field", "value"], f"_E.dbFind({en}, field, value)")
        method("Database", f"count_{e}", ["field", "value"], f"_E.dbCount({en}, field, value)", ["field = null", "value = null"])
        method("Database", f"all_{e}", [], f"_E.dbAll({en})")
    method("Database", "outbox", [], "_E.outbox.map((m) => Object.assign({}, m))")
    method("Test", "advance_minutes", ["minutes"], "_E.advanceMinutes(minutes)")
    method("Test", "simulate", ["failure"], "_E.simulate(failure)")

    header = "\n".join(" * " + line if line else " *" for line in _header_lines(spec, "JavaScript")).replace("*/", "* /")
    parts = [f"/**\n{header}\n */\n"]
    titles = {"UI": "What a user does and sees on the screens.", "API": "The same operations called directly; each returns a result object.",
              "Database": "What is really stored - for checking your test's effect.", "Test": "Test-support hooks."}
    for group, methods in groups.items():
        parts.append(f"\n// {titles[group]}\nconst {group} = {{\n" + "".join(methods) + "};\n")
    parts.append("\n/** Reset the app to its starting state. Call at the start of every test. */\nfunction setup() { _E.reset(); }\n")
    parts.append("\n/** Remove one record your test created (by its id). */\nfunction teardown(id = null) { _E.teardown(id); }\n")
    parts.append("\n// " + "=" * 76 + "\n// Engine internals - do not change anything below this line (up to the TODO block).\n// " + "=" * 76 + "\n\n")
    parts.append(runtime)
    parts.append(f"\nconst SPEC = {json.dumps(spec, ensure_ascii=False)};\nconst _E = new Engine(SPEC);\n")
    parts.append("\n// " + "-" * 75 + "\n// TODO: write your automated test(s) below.\n// " + "-" * 75 + "\n")
    return "".join(parts)


# ---------------------------------------------------------------------------- Java

def _java_string(text: str) -> str:
    out = []
    for ch in text:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ord(ch) < 0x20 or ord(ch) > 0x7E:
            out.append("\\u%04x" % ord(ch) if ord(ch) < 0x10000 else "".join("\\u%04x" % u for u in _utf16(ch)))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _utf16(ch: str) -> list[int]:
    b = ch.encode("utf-16-be")
    return [int.from_bytes(b[i:i + 2], "big") for i in range(0, len(b), 2)]


def render_java(spec: dict) -> str:
    runtime = (_HERE / "runtime_java.txt").read_text(encoding="utf-8")
    doc = {h["call"]: h["doc"] for h in helpers(spec)}
    groups: dict[str, list[str]] = {"UI": [], "API": [], "Database": [], "Test": []}
    obj, row, rows = "Object", "Map<String, Object>", "List<Map<String, Object>>"

    def method(group: str, name: str, params: list[str], returns: str, body: str) -> None:
        text = doc.get(f"{group}.{name}", "").replace("*/", "* /")
        signature = ", ".join(f"Object {camel(p)}" for p in params)
        cast = "" if returns in ("Object", "void") else f"({returns}) (Object) "
        stmt = f"{body};" if returns == "void" else f"return {cast}{body};"
        groups[group].append(f"        /** {text} */\n        @SuppressWarnings(\"unchecked\")\n"
                             f"        public static {returns} {camel(name)}({signature}) {{ {stmt} }}\n")

    def args_of(params: list[str]) -> str:
        return "inputs(" + ", ".join(f"{_java_string(p)}, {camel(p)}" for p in params) + ")"

    method("UI", "open", ["page"], "boolean", "_E.uiOpen(page)")
    method("UI", "current_page", [], "String", "_E.page")
    method("UI", "visible_message", [], "String", "_E.message")
    method("UI", "last_result", [], row, "new LinkedHashMap<>(_E.last)")
    if spec.get("users"):
        name_field = _java_string(spec["users"].get("name_field", spec["users"].get("login_field", "email")))
        method("UI", "login", ["login", "password"], "boolean", "_E.uiLogin(login, password)")
        method("UI", "logout", [], "boolean", "_E.uiLogout()")
        method("UI", "signed_in_user", [], obj, f"_E.user == null ? null : _E.user.get({name_field})")
        method("API", "login", ["login", "password"], row, "_E.apiLogin(login, password)")
    for q in spec.get("queries") or []:
        params, n = query_params(q), _java_string(q["name"])
        method("UI", snake(q["name"]), params, row if q.get("key_input") else rows, f"_E.uiQuery({n}, {args_of(params)})")
        method("API", snake(q["name"]), params, row, f"_E.apiQuery({n}, {args_of(params)})")
    for a in spec.get("actions") or []:
        params, n = action_params(a), _java_string(a["name"])
        method("UI", snake(a["name"]), params, "boolean", f"_E.uiAction({n}, {args_of(params)})")
        method("API", snake(a["name"]), params, row, f"_E.apiAction({n}, {args_of(params)})")
    for entity in spec.get("entities") or {}:
        e, en = snake(entity), _java_string(entity)
        method("Database", f"get_{e}", ["key"], row, f"_E.dbGet({en}, key)")
        method("Database", f"find_{e}", ["field", "value"], rows, f"_E.dbFind({en}, field, value)")
        method("Database", f"count_{e}", ["field", "value"], "long", f"_E.dbCount({en}, field, value)")
        groups["Database"].append(f"        /** How many {entity} records are stored. */\n"
                                  f"        public static long {camel('count_' + e)}() {{ return _E.dbCount({en}, null, null); }}\n")
        method("Database", f"all_{e}", [], rows, f"_E.dbAll({en})")
    method("Database", "outbox", [], rows, "_E.outboxCopy()")
    method("Test", "advance_minutes", ["minutes"], "String", "_E.advanceMinutes(minutes)")
    method("Test", "simulate", ["failure"], "boolean", "_E.simulate(failure)")

    header = "\n".join(" * " + line if line else " *" for line in _header_lines(spec, "Java")).replace("*/", "* /")
    titles = {"UI": "What a user does and sees on the screens.", "API": "The same operations called directly; each returns a result map.",
              "Database": "What is really stored - for checking your test's effect.", "Test": "Test-support hooks."}
    parts = ["import java.util.*;\n\n", f"/**\n{header}\n */\npublic class Main {{\n"]
    for group, methods in groups.items():
        parts.append(f"\n    /** {titles[group]} */\n    public static class {group} {{\n" + "\n".join(methods) + "    }\n")
    parts.append("\n    /** Reset the app to its starting state. Call at the start of every test. */\n    public static void setup() { _E.reset(); }\n")
    parts.append("\n    /** Remove one record your test created (by its id). */\n    public static void teardown(Object id) { _E.teardown(id); }\n")
    parts.append("\n    /** Nothing to remove. */\n    public static void teardown() { }\n")
    parts.append("\n    // " + "=" * 76 + "\n    // Engine internals - do not change anything below this line (up to the TODO block).\n    // " + "=" * 76 + "\n\n")
    parts.append(runtime)
    text = json.dumps(spec, ensure_ascii=False)
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [""]
    parts.append("\n    static String specText() {\n        StringBuilder b = new StringBuilder();\n"
                 + "".join(f"        b.append({_java_string(c)});\n" for c in chunks)
                 + "        return b.toString();\n    }\n\n    static final Engine _E = new Engine(map(Json.parse(specText())));\n")
    parts.append("\n    // " + "-" * 75 + "\n    // TODO: write your automated test(s) below, then call them from main().\n    // " + "-" * 75 + "\n\n"
                 "    public static void main(String[] args) {\n    }\n}\n")
    return "".join(parts)
