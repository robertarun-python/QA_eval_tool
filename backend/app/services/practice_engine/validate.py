"""
Checks a scenario description before the engine runs it. Every problem comes
back as one plain sentence naming where it is, so the list can go straight
back to the AI to correct (and, if it still fails, to HR). An empty list means
the description is safe to render in every language.
"""
import re
from datetime import date, datetime

from .render import camel, snake
from .runtime import Engine, EngineError

FIELD_TYPES = {"string", "int", "number", "money", "date", "datetime", "bool"}
TOP_KEYS = {"app_name", "base_url", "now", "entities", "data", "users", "pages", "home_page", "queries", "actions", "faults", "description",
            "public_pages"}
USER_KEYS = {"entity", "login_field", "password_field", "name_field", "session_minutes", "blocked_when", "messages", "page", "lockout"}
USER_MESSAGES = {"required_login", "required_password", "invalid", "blocked", "expired", "need_login"}
QUERY_KEYS = {"name", "label", "description", "page", "entity", "match", "inputs", "key_input", "missing", "where", "order_by", "show",
              "none_message", "next_page", "requires_login", "rules", "as"}
_ITEM = "(list item)"  # the pseudo record type of a for_each over a list: its one field is "value"
MATCH_KEYS = {"input", "fields", "mode", "blank", "max_length", "too_long"}
ACTION_KEYS = {"name", "label", "description", "page", "requires_login", "inputs", "load", "rules", "effects", "message", "returns", "next_page"}
CHECK_RULES = {"required", "min_length", "max_length", "pattern", "min", "max", "number", "one_of", "date", "not_past", "not_future"}
DATE_FORMATS = {"YYYY-MM-DD", "DD-MM-YYYY", "DD/MM/YYYY", "MM/DD/YYYY", "DD-Mon-YYYY", "DD Mon YYYY"}
CURRENCIES = {"INR", "USD", "EUR", "GBP"}
# Helper names the generated file already uses, and words no language accepts as a name.
FIXED_HELPERS = {"open", "current_page", "visible_message", "last_result", "login", "logout", "signed_in_user", "outbox",
                 "advance_minutes", "simulate", "setup", "teardown"}
RESERVED = set("""abstract and as assert async await boolean break byte case catch char class const continue def default del delete do
double elif else enum eval except export extends false final finally float for from function global goto if implements import in
instanceof int interface is lambda let long native new nonlocal none not null or package pass private protected public raise return
short static strictfp super switch synchronized this throw throws transient true try typeof var void volatile while with yield
arguments object string map list main""".split())
NAME = re.compile(r"[a-z][a-z0-9_]*")
ENTITY_NAME = re.compile(r"[A-Z][A-Za-z0-9]*")
# Regex features that behave differently (or not at all) in Python, JavaScript and Java.
UNPORTABLE_REGEX = [(r"\(\?<[=!]", "look-behind"), (r"\(\?P", "named groups"), (r"\(\?<\w", "named groups"), (r"\(\?[aiLmsux]", "inline flags"),
                    (r"\\[1-9]", "back-references"), (r"[*+?}]\+", "possessive quantifiers"), (r"\\[AZzpPhHRXQE]", "special escapes"),
                    (r"\[\[:", "POSIX classes"), (r"\\u", "\\u escapes"), (r"\{,", "{,n} counts"), (r"&&", "&& in a class"),
                    (r"\[[^\]]*\[", "[ inside [...]")]
EXPR_OPS = {"input", "field", "user", "session", "today", "now", "add", "sub", "mul", "div", "round", "add_days", "add_months",
            "days_between", "minutes_since", "count", "sum", "exists", "if", "format", "format_date", "format_money", "upper", "lower",
            "trim", "length", "concat", "minutes_between", "time", "weekday", "working_days", "split", "occurrences", "add_minutes"}
COND_OPS = {"and", "or", "not", "empty", "matches", "in", "eq", "ne", "lt", "le", "gt", "ge"}
PAIR_OPS = {"add", "sub", "mul", "div", "add_days", "add_months", "days_between", "eq", "ne", "lt", "le", "gt", "ge", "in", "matches",
            "minutes_between", "working_days", "occurrences", "add_minutes"}


def problems(spec) -> list[str]:
    """Every problem in the description, as plain sentences; [] if it is valid."""
    return _Checker(spec).run()


def regex_problem(pattern) -> str | None:
    if not isinstance(pattern, str) or not pattern:
        return "must be a non-empty regular expression"
    for bad, what in UNPORTABLE_REGEX:
        if re.search(bad, pattern):
            return f"uses {what}, which don't work the same in every language"
    try:
        re.compile(pattern, re.ASCII)
    except re.error as e:
        return f"is not a valid regular expression ({e})"
    return None


def _is_date(v) -> bool:
    try:
        return isinstance(v, str) and len(v) == 10 and date.fromisoformat(v) is not None
    except ValueError:
        return False


def _is_datetime(v) -> bool:
    try:
        return isinstance(v, str) and len(v) == 16 and v[10] == "T" and datetime.fromisoformat(v) is not None
    except ValueError:
        return False


def _type_ok(kind: str, v) -> bool:
    if v is None:
        return True
    if kind == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if kind in ("number", "money"):
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if kind == "bool":
        return isinstance(v, bool)
    if kind == "date":
        return _is_date(v)
    if kind == "datetime":
        return _is_datetime(v)
    return isinstance(v, str)


class _Checker:
    def __init__(self, spec):
        self.spec = spec
        self.out: list[str] = []

    def err(self, where: str, text: str) -> None:
        self.out.append(f"{where}: {text}")

    # ---- structure ----------------------------------------------------------------------------
    def run(self) -> list[str]:
        spec = self.spec
        if not isinstance(spec, dict):
            return ["The description must be a JSON object."]
        for k in sorted(set(spec) - TOP_KEYS):
            self.err("description", f"unknown key {k!r}")
        if not isinstance(spec.get("app_name"), str) or not spec.get("app_name", "").strip():
            self.err("app_name", "is required")
        if "now" in spec and not _is_datetime(spec["now"]):
            self.err("now", "must look like 2024-02-10T10:00")
        pages = spec.get("pages")
        if not isinstance(pages, list) or not pages or not all(isinstance(p, str) and p.strip() for p in pages):
            self.err("pages", "must be a non-empty list of page names")
            pages = []
        elif len(set(pages)) != len(pages):
            self.err("pages", "has the same page twice")
        self.pages = set(pages)
        for p in spec.get("public_pages") or []:
            if p not in self.pages:
                self.err("public_pages", f"{p!r} is not one of the pages")
        if "home_page" in spec and spec["home_page"] not in self.pages:
            self.err("home_page", f"{spec['home_page']!r} is not one of the pages")
        self.entities = spec.get("entities") if isinstance(spec.get("entities"), dict) else {}
        if not self.entities:
            self.err("entities", "must describe at least one kind of record")
        for name, entity in self.entities.items():
            self.entity(name, entity)
        self.data()
        self.users()
        self.helper_names()
        for i, q in enumerate(self._list("queries")):
            self.query(q, i)
        for i, a in enumerate(self._list("actions")):
            self.action(a, i)
        self.faults()
        if not self.out:
            self.dry_run()
        return self.out

    def _list(self, key: str) -> list:
        value = self.spec.get(key, [])
        if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
            self.err(key, "must be a list of objects")
            return []
        return value

    def fields(self, entity) -> dict:
        if entity == _ITEM:
            return {"value": "string"}
        e = self.entities.get(entity)
        return e.get("fields") if isinstance(e, dict) and isinstance(e.get("fields"), dict) else {}

    def entity(self, name, entity) -> None:
        where = f"entity {name}"
        if not ENTITY_NAME.fullmatch(str(name)):
            self.err(where, "name must start with a capital letter and use only letters and digits (e.g. Loan, CardTransaction)")
        if not isinstance(entity, dict):
            self.err(where, "must be an object with key and fields")
            return
        for k in sorted(set(entity) - {"key", "fields", "id_format", "description"}):
            self.err(where, f"unknown key {k!r}")
        fields = entity.get("fields")
        if not isinstance(fields, dict) or not fields:
            self.err(where, "needs fields")
            return
        for f, kind in fields.items():
            if not NAME.fullmatch(f):
                self.err(where, f"field {f!r} must be lower_snake_case")
            if kind not in FIELD_TYPES:
                self.err(where, f"field {f!r} has type {kind!r}; use one of {', '.join(sorted(FIELD_TYPES))}")
        key = entity.get("key", "id")
        if key not in fields:
            self.err(where, f"key {key!r} is not one of its fields")
        fmt = entity.get("id_format")
        if fmt is not None:
            if not isinstance(fmt, str) or not re.search(r"\{n(?::0?\d+)?\}", fmt):
                self.err(where, "id_format must contain {n} or {n:03}, e.g. LN-{n:03}")
            elif fields.get(key) != "string":
                self.err(where, "id_format needs a string key field")

    def data(self) -> None:
        data = self.spec.get("data", {})
        if not isinstance(data, dict):
            self.err("data", "must map each record type to a list of records")
            return
        for name, rows in data.items():
            if name not in self.entities:
                self.err(f"data {name}", "is not one of the entities")
                continue
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                self.err(f"data {name}", "must be a list of records")
                continue
            fields, key = self.fields(name), self.entities[name].get("key", "id")
            seen = set()
            for i, row in enumerate(rows):
                where = f"data {name}[{i}]"
                for f in sorted(set(row) - set(fields)):
                    self.err(where, f"field {f!r} is not declared for {name}")
                for f, v in row.items():
                    if f in fields and not _type_ok(fields[f], v):
                        self.err(where, f"{f} = {v!r} is not a valid {fields[f]}")
                k = str(row.get(key)).lower()
                if row.get(key) is None:
                    self.err(where, f"has no {key}")
                elif k in seen:
                    self.err(where, f"{key} {row.get(key)!r} is used twice")
                seen.add(k)
            fmt = self.entities[name].get("id_format")
            if isinstance(fmt, str) and re.search(r"\{n", fmt):
                upcoming = {Engine._id(fmt, n).lower() for n in range(len(rows) + 1, len(rows) + 201)}
                clash = sorted(seen & upcoming)
                if clash:
                    self.err(f"data {name}", f"starting record {clash[0]!r} clashes with an id the app will create; number the starting records from 1")

    def users(self) -> None:
        users = self.spec.get("users")
        self.user_entity = None
        if users is None:
            return
        if not isinstance(users, dict):
            self.err("users", "must be an object")
            return
        for k in sorted(set(users) - USER_KEYS):
            self.err("users", f"unknown key {k!r}")
        entity = users.get("entity")
        if entity not in self.entities:
            self.err("users", f"entity {entity!r} is not one of the entities")
            return
        self.user_entity = entity
        fields = self.fields(entity)
        for k, default in (("login_field", "email"), ("password_field", "password"), ("name_field", None)):
            f = users.get(k, default)
            if f is not None and f not in fields:
                self.err("users", f"{k} {f!r} is not a field of {entity}")
        if users.get("page", "Login") not in self.pages:
            self.err("users", f"sign-in page {users.get('page', 'Login')!r} is not one of the pages")
        minutes = users.get("session_minutes")
        if minutes is not None and (not isinstance(minutes, int) or isinstance(minutes, bool) or minutes < 0):
            self.err("users", "session_minutes must be a whole number of minutes")
        messages = users.get("messages", {})
        if not isinstance(messages, dict):
            self.err("users", "messages must be an object")
        else:
            for k in sorted(set(messages) - USER_MESSAGES):
                self.err("users", f"unknown message {k!r}; use {', '.join(sorted(USER_MESSAGES))}")
        lockout = users.get("lockout")
        if lockout is not None:
            if not isinstance(lockout, dict) or not isinstance(lockout.get("attempts"), int) or isinstance(lockout.get("attempts"), bool) \
                    or lockout["attempts"] < 1 or not (lockout.get("minutes") is None or (isinstance(lockout["minutes"], int) and lockout["minutes"] >= 0)):
                self.err("users", "lockout needs attempts (a whole number, at least 1), minutes (whole number, or omit for locked until reset) and message")
            else:
                self.message(lockout.get("message"), "users lockout")
        if "blocked_when" in users:
            self.cond(users["blocked_when"], "users.blocked_when", {"inputs": set(), "aliases": {"user": entity}})
        if not (self.spec.get("data") or {}).get(entity):
            self.err("users", f"there are no {entity} records to sign in with")

    def helper_names(self) -> None:
        seen: dict[str, str] = {}
        for kind in ("queries", "actions"):
            for item in self._list(kind):
                name = item.get("name")
                where = f"{kind[:-1]} {name!r}"
                if not isinstance(name, str) or not NAME.fullmatch(name):
                    self.err(where, "name must be lower_snake_case, e.g. search_books")
                    continue
                if name in FIXED_HELPERS or name in RESERVED or camel(name) in RESERVED:
                    self.err(where, "name is already used by the app's built-in helpers or is a reserved word; choose another")
                if camel(name) in seen:
                    self.err(where, f"name clashes with {seen[camel(name)]}")
                seen[camel(name)] = where

    def params(self, names: list, where: str) -> None:
        camels = set()
        for n in names:
            if not isinstance(n, str) or not NAME.fullmatch(n):
                self.err(where, f"input name {n!r} must be lower_snake_case")
            elif n in RESERVED or camel(n) in RESERVED or snake(n) != n:
                self.err(where, f"input name {n!r} is a reserved word in Python, JavaScript or Java; choose another")
            elif camel(n) in camels:
                self.err(where, f"input {n!r} is given twice")
            else:
                camels.add(camel(n))

    def page_ref(self, value, where: str, what: str) -> None:
        if value is not None and value not in self.pages:
            self.err(where, f"{what} {value!r} is not one of the pages")

    def message(self, value, where: str) -> None:
        if not isinstance(value, str) or not value.strip():
            self.err(where, "needs a message the user will see")

    def input_checks(self, inputs, where: str) -> list[str]:
        if inputs is None:
            return []
        if not isinstance(inputs, list):
            self.err(where, "inputs must be a list")
            return []
        names = []
        for d in inputs:
            if not isinstance(d, dict) or "name" not in d:
                self.err(where, "every input needs a name")
                continue
            names.append(d["name"])
            for k in sorted(set(d) - {"name", "checks", "description", "type"}):
                self.err(where, f"input {d['name']!r} has unknown key {k!r} (checks go in a 'checks' list)")
            for c in d.get("checks") or []:
                w = f"{where}, input {d['name']!r}"
                if not isinstance(c, dict) or c.get("rule") not in CHECK_RULES:
                    self.err(w, f"check rule must be one of {', '.join(sorted(CHECK_RULES))}")
                    continue
                self.message(c.get("message"), f"{w} {c['rule']} check")
                rule, v = c["rule"], c.get("value")
                if rule in ("min_length", "max_length") and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
                    self.err(w, f"{rule} needs a whole-number value")
                if rule in ("min", "max") and (not isinstance(v, (int, float)) or isinstance(v, bool)):
                    self.err(w, f"{rule} needs a number value")
                if rule == "one_of" and (not isinstance(v, list) or not v):
                    self.err(w, "one_of needs a list of allowed values")
                if rule == "pattern":
                    p = regex_problem(v)
                    if p:
                        self.err(w, f"pattern {p}")
        return names

    # ---- queries and actions ------------------------------------------------------------------
    def query(self, q: dict, i: int) -> None:
        where = f"query {q.get('name', i)!r}"
        for k in sorted(set(q) - QUERY_KEYS):
            self.err(where, f"unknown key {k!r}")
        entity = q.get("entity")
        if entity not in self.entities:
            self.err(where, f"entity {entity!r} is not one of the entities")
            return
        fields = self.fields(entity)
        self.page_ref(q.get("page"), where, "page")
        self.page_ref(q.get("next_page"), where, "next_page")
        extra = self.input_checks(q.get("inputs"), where)
        match, key_input = q.get("match"), q.get("key_input")
        if match is not None and key_input is not None:
            self.err(where, "use either match (a search) or key_input (one record), not both")
        first: list[str] = []
        if isinstance(match, dict):
            for k in sorted(set(match) - MATCH_KEYS):
                self.err(where, f"match has unknown key {k!r}")
            first = [match.get("input")]
            if not match.get("fields") or not isinstance(match.get("fields"), list):
                self.err(where, "match needs the fields to search")
            for f in match.get("fields") or []:
                if f not in fields:
                    self.err(where, f"match field {f!r} is not a field of {entity}")
            if match.get("mode", "contains") not in ("contains", "equals", "starts_with"):
                self.err(where, "match mode must be contains, equals or starts_with")
            blank = match.get("blank", "all")
            if blank not in ("all", "empty") and not (isinstance(blank, str) and blank.startswith("error:") and blank[6:].strip()):
                self.err(where, "match blank must be all, empty or error:<message>")
            if "max_length" in match and (not isinstance(match["max_length"], int) or match["max_length"] < 1):
                self.err(where, "match max_length must be a whole number")
        elif match is not None:
            self.err(where, "match must be an object")
        if key_input is not None:
            first = [key_input]
            self.message(q.get("missing"), f"{where} missing")
        self.params(first + [n for n in extra if n not in first], where)
        self.rules(q.get("rules"), where, {"inputs": set(first + extra), "aliases": {}})
        scope = {"inputs": set(first + extra), "aliases": {"row": entity}}
        if q.get("as") is not None:
            if not isinstance(q["as"], str) or not NAME.fullmatch(q["as"]) or q["as"] == "row":
                self.err(where, "as must be a lower_snake_case name for each record (not row)")
            else:
                scope["aliases"][q["as"]] = entity
        if "where" in q:
            self.cond(q["where"], f"{where} where", scope)
        for f in q.get("show") or []:
            if isinstance(f, dict):
                if not isinstance(f.get("name"), str) or not NAME.fullmatch(f["name"]) or "value" not in f or set(f) - {"name", "value"}:
                    self.err(where, 'a calculated show item is {"name": "lower_snake_case", "value": <expression>}')
                else:
                    self.expr(f["value"], f"{where} show {f['name']}", scope)
            elif f not in fields:
                self.err(where, f"show field {f!r} is not a field of {entity}")
        for o in q.get("order_by") or []:
            if not isinstance(o, dict) or o.get("field") not in fields:
                self.err(where, f"order_by {o!r} must name a field of {entity}")

    def action(self, a: dict, i: int) -> None:
        where = f"action {a.get('name', i)!r}"
        for k in sorted(set(a) - ACTION_KEYS):
            self.err(where, f"unknown key {k!r}")
        self.page_ref(a.get("page"), where, "page")
        self.page_ref(a.get("next_page"), where, "next_page")
        names = self.input_checks(a.get("inputs"), where)
        self.params(names, where)
        scope = {"inputs": set(names), "aliases": {}}
        for j, load in enumerate(a.get("load") or []):
            w = f"{where} load[{j}]"
            if not isinstance(load, dict) or load.get("entity") not in self.entities or not isinstance(load.get("as"), str):
                self.err(w, "needs as, an entity that exists, key and missing")
                continue
            self.expr(load.get("key"), f"{w} key", scope)
            self.message(load.get("missing"), f"{w} missing")
            scope["aliases"][load["as"]] = load["entity"]
        self.rules(a.get("rules"), where, scope)
        for j, effect in enumerate(a.get("effects") or []):
            self.effect(effect, f"{where} effect {j + 1}", scope)
        if a.get("message") is not None:
            self.expr(a["message"], f"{where} message", scope)
        for k, v in (a.get("returns") or {}).items():
            if not NAME.fullmatch(k) or k in ("ok", "error", "message"):
                self.err(where, f"returns name {k!r} must be lower_snake_case and not ok/error/message")
            self.expr(v, f"{where} returns {k}", scope)

    def for_each(self, each, where: str, scope: dict) -> dict | None:
        """The scope inside a for_each ({"entity", "where"} or {"list"}, and "as"), or None."""
        if not isinstance(each, dict) or not isinstance(each.get("as"), str) or not NAME.fullmatch(each["as"]):
            self.err(where, 'for_each needs "as" (a lower_snake_case name) and either "entity" (+ optional "where") or "list"')
            return None
        if "entity" in each:
            if each["entity"] not in self.entities:
                self.err(where, f"for_each entity {each['entity']!r} is not one of the entities")
                return None
            if "where" in each:
                self.cond(each["where"], where, {"inputs": scope["inputs"], "aliases": {**scope["aliases"], "row": each["entity"]}})
            kind = each["entity"]
        elif "list" in each:
            self.expr(each["list"], where, scope)
            kind = _ITEM
        else:
            self.err(where, 'for_each needs "entity" or "list"')
            return None
        return {"inputs": scope["inputs"], "aliases": {**scope["aliases"], each["as"]: kind}}

    def rules(self, rules, where: str, scope: dict) -> None:
        if rules is None:
            return
        if not isinstance(rules, list):
            self.err(where, "rules must be a list")
            return
        for j, rule in enumerate(rules):
            w = f"{where} rule {j + 1}"
            if not isinstance(rule, dict) or len({"unless", "when"} & set(rule)) != 1 or set(rule) - {"unless", "when", "message", "then", "for_each", "status"}:
                self.err(w, "needs exactly one of unless / when, a message, and optionally then (effects kept when it refuses) and for_each")
                continue
            if "status" in rule and rule["status"] not in (400, 403, 404, 409, 422, 429):
                self.err(w, "status must be one of 400, 403, 404, 409, 422, 429")
            inner = self.for_each(rule["for_each"], w, scope) if "for_each" in rule else scope
            if inner is None:
                continue
            self.cond(rule.get("unless", rule.get("when")), w, inner)
            if isinstance(rule.get("message"), str):
                self.message(rule["message"], w)
            else:
                self.expr(rule.get("message"), f"{w} message", inner)
            for k, effect in enumerate(rule.get("then") or []):
                self.effect(effect, f"{w} then {k + 1}", {"inputs": inner["inputs"], "aliases": dict(inner["aliases"])})

    def effect(self, effect, where: str, scope: dict) -> None:
        if not isinstance(effect, dict) or len(effect) != 1:
            self.err(where, "an effect has exactly one operator")
            return
        (op, arg), = effect.items()
        aliases = scope["aliases"]
        if op == "set":
            if not isinstance(arg, dict) or arg.get("record") not in aliases or aliases[arg["record"]] == _ITEM:
                self.err(where, f"set needs a stored record loaded earlier (one of {sorted(a for a, k in aliases.items() if k != _ITEM)})")
                return
            if arg.get("field") not in self.fields(aliases[arg["record"]]):
                self.err(where, f"{arg.get('field')!r} is not a field of {aliases[arg['record']]}")
            self.expr(arg.get("value"), where, scope)
        elif op == "create":
            if not isinstance(arg, dict) or arg.get("entity") not in self.entities:
                self.err(where, "create needs an entity that exists")
                return
            fields = self.fields(arg["entity"])
            for k, v in (arg.get("values") or {}).items():
                if k not in fields:
                    self.err(where, f"{k!r} is not a field of {arg['entity']}")
                self.expr(v, f"{where} {k}", scope)
            key = self.entities[arg["entity"]].get("key", "id")
            if key not in (arg.get("values") or {}) and not self.entities[arg["entity"]].get("id_format") and fields.get(key) != "int":
                self.err(where, f"new {arg['entity']} records need a {key}: give a value or an id_format")
            if arg.get("as"):
                aliases[arg["as"]] = arg["entity"]
        elif op == "delete":
            if not isinstance(arg, dict) or arg.get("record") not in aliases or aliases[arg["record"]] == _ITEM:
                self.err(where, "delete needs a stored record loaded earlier")
        elif op == "set_session":
            if not isinstance(arg, dict) or not isinstance(arg.get("key"), str):
                self.err(where, "set_session needs key and value")
                return
            self.expr(arg.get("value"), where, scope)
        elif op == "clear_session":
            if not isinstance(arg, str):
                self.err(where, "clear_session needs the session key")
        elif op == "send":
            if not isinstance(arg, dict) or not arg:
                self.err(where, "send needs fields such as channel, to, subject")
                return
            for k, v in arg.items():
                self.expr(v, f"{where} {k}", scope)
        elif op == "logout":
            pass
        elif op == "if":
            if not isinstance(arg, list) or len(arg) not in (2, 3) or not all(isinstance(b, list) for b in arg[1:]):
                self.err(where, "if needs [condition, [effects if true], [effects if false]]")
                return
            self.cond(arg[0], where, scope)
            for branch in arg[1:]:
                for k, inner in enumerate(branch):
                    self.effect(inner, f"{where}.{k + 1}", {"inputs": scope["inputs"], "aliases": dict(scope["aliases"])})
        elif op == "for_each":
            inner = self.for_each(arg, where, scope)
            if inner is None:
                return
            if not isinstance(arg.get("effects"), list) or not arg["effects"]:
                self.err(where, "for_each needs a list of effects")
                return
            for k, e in enumerate(arg["effects"]):
                self.effect(e, f"{where}.{k + 1}", inner)
        else:
            self.err(where, f"unknown effect {op!r}; use set, create, delete, set_session, clear_session, send, logout, if or for_each")

    # ---- expressions --------------------------------------------------------------------------
    def expr(self, e, where: str, scope: dict) -> None:
        if isinstance(e, list):
            for x in e:
                self.expr(x, where, scope)
            return
        if not isinstance(e, dict):
            return
        if len(e) != 1:
            self.err(where, f"an expression has exactly one operator, got {sorted(e)}")
            return
        (op, arg), = e.items()
        if op in COND_OPS:
            self.cond(e, where, scope)
            return
        if op not in EXPR_OPS:
            self.err(where, f"unknown operator {op!r}")
            return
        if op in PAIR_OPS and (not isinstance(arg, list) or len(arg) != 2):
            self.err(where, f"{op} needs a list of two values")
            return
        if op == "input":
            if arg not in scope["inputs"]:
                self.err(where, f"input {arg!r} is not one of this action's inputs")
        elif op == "field":
            alias, _, field = str(arg).partition(".")
            if alias not in scope["aliases"]:
                self.err(where, f"{arg!r}: no record called {alias!r} here (available: {', '.join(sorted(scope['aliases'])) or 'none'})")
            elif field not in self.fields(scope["aliases"][alias]):
                self.err(where, f"{arg!r}: {field!r} is not a field of {scope['aliases'][alias]}")
        elif op == "user":
            if not self.user_entity:
                self.err(where, "uses the signed-in user but the app has no users")
            elif arg not in self.fields(self.user_entity):
                self.err(where, f"user field {arg!r} is not a field of {self.user_entity}")
        elif op == "session":
            if not isinstance(arg, str):
                self.err(where, "session needs a key name")
        elif op in ("today", "now"):
            pass
        elif op == "split":
            if not isinstance(arg, list) or not 1 <= len(arg) <= 2 or (len(arg) == 2 and not (isinstance(arg[1], str) and arg[1])):
                self.err(where, 'split needs [text, "separator"]')
                return
            self.expr(arg[0], where, scope)
        elif op in ("count", "sum", "exists"):
            if not isinstance(arg, dict) or arg.get("entity") not in self.entities:
                self.err(where, f"{op} needs an entity that exists")
                return
            inner = {"inputs": scope["inputs"], "aliases": {**scope["aliases"], "row": arg["entity"]}}
            if op == "sum" and self.fields(arg["entity"]).get(arg.get("field")) not in ("int", "number", "money"):
                self.err(where, f"sum needs a number field of {arg['entity']}")
            if "where" in arg:
                self.cond(arg["where"], where, inner)
        elif op == "if":
            if not isinstance(arg, list) or len(arg) != 3:
                self.err(where, "if needs [condition, value if true, value if false]")
                return
            self.cond(arg[0], where, scope)
            self.expr(arg[1:], where, scope)
        elif op == "round":
            self.expr(arg, where, scope)
        elif op == "format":
            if not isinstance(arg, list) or not isinstance(arg[0] if arg else None, str) or len(arg) > 2 or (len(arg) == 2 and not isinstance(arg[1], dict)):
                self.err(where, 'format needs ["text with {name}", {"name": value}]')
                return
            values = arg[1] if len(arg) == 2 else {}
            for k in re.findall(r"\{(\w+)\}", arg[0]):
                if k not in values:
                    self.err(where, f"format placeholder {{{k}}} has no value")
            for v in values.values():
                self.expr(v, where, scope)
        elif op == "format_date":
            if not isinstance(arg, list) or len(arg) != 2 or arg[1] not in DATE_FORMATS:
                self.err(where, f"format_date needs [date, format] with a format from {', '.join(sorted(DATE_FORMATS))}")
                return
            self.expr(arg[0], where, scope)
        elif op == "format_money":
            if not isinstance(arg, list) or not 1 <= len(arg) <= 2 or (len(arg) == 2 and arg[1] not in CURRENCIES):
                self.err(where, f"format_money needs [amount, currency] with a currency from {', '.join(sorted(CURRENCIES))}")
                return
            self.expr(arg[0], where, scope)
        elif op == "concat":
            if not isinstance(arg, list):
                self.err(where, "concat needs a list")
            self.expr(arg, where, scope)
        else:
            self.expr(arg, where, scope)

    def cond(self, c, where: str, scope: dict) -> None:
        if isinstance(c, bool):
            return
        if not isinstance(c, dict) or len(c) != 1:
            self.err(where, f"not a condition: {c!r}")
            return
        (op, arg), = c.items()
        if op in ("and", "or"):
            if not isinstance(arg, list) or not arg:
                self.err(where, f"{op} needs a list of conditions")
                return
            for x in arg:
                self.cond(x, where, scope)
        elif op == "not":
            self.cond(arg, where, scope)
        elif op == "empty":
            self.expr(arg, where, scope)
        elif op in PAIR_OPS:
            if not isinstance(arg, list) or len(arg) != 2:
                self.err(where, f"{op} needs a list of two values")
                return
            self.expr(arg[0], where, scope)
            if op == "matches":
                if not isinstance(arg[1], str):
                    self.err(where, 'matches needs [value, "fixed regular expression text"] - the pattern must be written out, not an expression')
                    return
                p = regex_problem(arg[1])
                if p:
                    self.err(where, f"pattern {p}")
            else:
                self.expr(arg[1], where, scope)
        elif op == "exists":
            self.expr(c, where, scope)
        else:
            self.err(where, f"{op!r} is not a condition; use {', '.join(sorted(COND_OPS | {'exists'}))}")

    def faults(self) -> None:
        actions = {a.get("name") for a in self._list("actions")}
        names = set()
        for f in self._list("faults"):
            where = f"fault {f.get('name')!r}"
            if not isinstance(f.get("name"), str) or not NAME.fullmatch(f["name"]):
                self.err(where, "name must be lower_snake_case")
            elif f["name"] in names:
                self.err(where, "is defined twice")
            names.add(f.get("name"))
            self.message(f.get("message"), where)
            if "status" in f and f["status"] not in (500, 502, 503, 504):
                self.err(where, "status must be one of 500, 502, 503, 504")
            for a in f.get("applies_to") or []:
                if a not in actions:
                    self.err(where, f"applies_to {a!r} is not one of the actions")

    def dry_run(self) -> None:
        try:
            engine = Engine(self.spec)
            engine.reset()
        except (EngineError, KeyError, TypeError, ValueError) as e:
            self.err("description", f"the engine could not load it ({e})")
