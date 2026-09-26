"""
Practice-app engine runtime (Python). Runs a scenario description (see
docs/practice_engine/DESIGN.md). This file is embedded, unchanged, in every
generated Python practice app - so it must stay standard-library only and
self-contained. runtime.js and Runtime.java implement the same behaviour and
pass the same conformance suite.
"""
import copy
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal


class EngineError(Exception):
    """The description asked for something the engine can't do (the validator
    should have caught it) - never a candidate's mistake."""


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}


def _num(value):
    if isinstance(value, bool):
        raise EngineError("expected a number, got true/false")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            pass
    raise EngineError(f"expected a number, got {value!r}")


def _clean(value):
    """Numbers as the engine returns them: whole numbers as int, others rounded to 2 places."""
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return int(value)
        return float(Decimal(repr(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return value


def _round(value, places=2):
    q = Decimal(1).scaleb(-int(places))
    return _clean(float(Decimal(repr(_num(value))).quantize(q, rounding=ROUND_HALF_UP)))


def _to_date(value):
    if isinstance(value, str) and len(value) >= 10:
        return date(int(value[0:4]), int(value[5:7]), int(value[8:10]))
    raise EngineError(f"expected a date YYYY-MM-DD, got {value!r}")


def _to_datetime(value):
    if isinstance(value, str) and len(value) >= 16:
        return datetime(int(value[0:4]), int(value[5:7]), int(value[8:10]), int(value[11:13]), int(value[14:16]))
    if isinstance(value, str) and len(value) == 10:
        d = _to_date(value)
        return datetime(d.year, d.month, d.day)
    raise EngineError(f"expected a date-time YYYY-MM-DDTHH:MM, got {value!r}")


def _iso_date(d):
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


def _iso_datetime(dt):
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T{dt.hour:02d}:{dt.minute:02d}"


def _add_months(d, n):
    month = d.month - 1 + int(n)
    year = d.year + month // 12
    month = month % 12 + 1
    last = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(d.day, last))


def _format_date(value, fmt):
    d = _to_date(value)
    if fmt == "YYYY-MM-DD":
        return _iso_date(d)
    if fmt == "DD-MM-YYYY":
        return f"{d.day:02d}-{d.month:02d}-{d.year:04d}"
    if fmt == "DD/MM/YYYY":
        return f"{d.day:02d}/{d.month:02d}/{d.year:04d}"
    if fmt == "MM/DD/YYYY":
        return f"{d.month:02d}/{d.day:02d}/{d.year:04d}"
    if fmt == "DD-Mon-YYYY":
        return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year:04d}"
    if fmt == "DD Mon YYYY":
        return f"{d.day:02d} {_MONTHS[d.month - 1]} {d.year:04d}"
    raise EngineError(f"unknown date format {fmt!r}")


def _group(digits, indian):
    if not indian or len(digits) <= 3:
        parts = []
        while len(digits) > 3:
            parts.insert(0, digits[-3:])
            digits = digits[:-3]
        parts.insert(0, digits)
        return ",".join(parts)
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def _format_money(value, currency):
    amount = Decimal(repr(float(_num(value)))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    whole, cents = f"{abs(amount):.2f}".split(".")
    return f"{sign}{_SYMBOLS.get(currency, currency + ' ')}{_group(whole, currency == 'INR')}.{cents}"


# The regex subset every language agrees on (see DESIGN.md) - checked by the validator.
def _full_match(pattern, text):
    return re.fullmatch(pattern, "" if text is None else str(text), re.ASCII) is not None  # \d \w \s ASCII-only, as in JS and Java


def _same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b if isinstance(a, bool) and isinstance(b, bool) else False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 1e-9
    return a == b


def _compare(a, b):
    if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
        return (a > b) - (a < b)
    if isinstance(a, str) and isinstance(b, str):
        return (a > b) - (a < b)
    if a is None or b is None:
        raise EngineError("can't compare a missing value")
    return _compare(_num(a), _num(b))


class _Refused(Exception):
    def __init__(self, message, page=None):
        super().__init__(message)
        self.message = message
        self.page = page


class Engine:
    def __init__(self, spec):
        self.spec = spec
        self.entities = spec.get("entities") or {}
        self.actions = {a["name"]: a for a in spec.get("actions") or []}
        self.queries = {q["name"]: q for q in spec.get("queries") or []}
        self.faults = {f["name"]: f for f in spec.get("faults") or []}
        self.users = spec.get("users") or {}
        self.reset()

    # ---- state ------------------------------------------------------------------------------
    def reset(self):
        self.store = {name: [copy.deepcopy(r) for r in (self.spec.get("data") or {}).get(name) or []] for name in self.entities}
        self.created = set()
        self.counters = {name: len(rows) for name, rows in self.store.items()}
        self.clock = _to_datetime(self.spec.get("now") or "2025-01-15T10:00")
        self.session = {}
        self.user = None
        self.last_active = None
        self.active_faults = set()
        self.outbox = []
        self.page = self._login_page() if self.users else (self.spec.get("home_page") or (self.spec.get("pages") or ["Home"])[0])
        self.message = ""
        self.last = {}

    def _login_page(self):
        return self.users.get("page") or "Login"

    def _key(self, entity):
        return self.entities[entity].get("key", "id")

    def _field_type(self, entity, field):
        return (self.entities[entity].get("fields") or {}).get(field, "string")

    def _typed(self, entity, field, value):
        kind = self._field_type(entity, field)
        if value is None:
            return None
        if kind in ("int",):
            return int(_num(value))
        if kind in ("number", "money"):
            return _clean(float(_num(value)))
        if kind == "bool":
            return bool(value)
        return value

    # ---- expressions --------------------------------------------------------------------------
    def ev(self, expr, ctx):
        if isinstance(expr, list):
            return [self.ev(x, ctx) for x in expr]
        if not isinstance(expr, dict):
            return expr
        if len(expr) != 1:
            raise EngineError(f"an expression has exactly one operator, got {sorted(expr)}")
        op, arg = next(iter(expr.items()))
        ev = self.ev
        if op == "input":
            return ctx["inputs"].get(arg)
        if op == "field":
            alias, _, field = str(arg).partition(".")
            record = ctx["aliases"].get(alias)
            if record is None:
                raise EngineError(f"no record called {alias!r} here")
            return record.get(field)
        if op == "user":
            return None if self.user is None else self.user.get(arg)
        if op == "session":
            return self.session.get(arg)
        if op == "today":
            return _iso_date(self.clock.date())
        if op == "now":
            return _iso_datetime(self.clock)
        if op in ("add", "sub", "mul", "div"):
            a, b = (_num(ev(x, ctx)) for x in arg)
            if op == "div" and b == 0:
                raise _Refused("Cannot divide by zero")
            return _clean({"add": a + b, "sub": a - b, "mul": a * b, "div": a / b if op == "div" else 0}[op])
        if op == "round":
            value, places = (arg + [2])[:2] if isinstance(arg, list) else (arg, 2)
            return _round(ev(value, ctx), ev(places, ctx))
        if op == "add_days":
            return _iso_date(_to_date(ev(arg[0], ctx)) + timedelta(days=int(_num(ev(arg[1], ctx)))))
        if op == "add_months":
            return _iso_date(_add_months(_to_date(ev(arg[0], ctx)), _num(ev(arg[1], ctx))))
        if op == "days_between":
            return (_to_date(ev(arg[1], ctx)) - _to_date(ev(arg[0], ctx))).days
        if op == "minutes_since":
            then = ev(arg, ctx)
            return None if then is None else int((self.clock - _to_datetime(then)).total_seconds() // 60)
        if op in ("count", "sum", "exists"):
            rows = self._rows(arg["entity"], arg.get("where"), ctx)
            if op == "count":
                return len(rows)
            if op == "exists":
                return bool(rows)
            return _clean(sum(_num(r.get(arg["field"]) or 0) for r in rows))
        if op == "if":
            return ev(arg[1], ctx) if self.cond(arg[0], ctx) else ev(arg[2], ctx)
        if op == "format":
            text, values = arg[0], arg[1] if len(arg) > 1 else {}
            resolved = {k: self._text(ev(v, ctx)) for k, v in values.items()}
            return re.sub(r"\{(\w+)\}", lambda m: resolved.get(m.group(1), m.group(0)), text)
        if op == "format_date":
            return _format_date(ev(arg[0], ctx), arg[1])
        if op == "format_money":
            return _format_money(ev(arg[0], ctx), arg[1] if len(arg) > 1 else "INR")
        if op == "upper":
            return self._text(ev(arg, ctx)).upper()
        if op == "lower":
            return self._text(ev(arg, ctx)).lower()
        if op == "trim":
            return self._text(ev(arg, ctx)).strip()
        if op == "length":
            value = ev(arg, ctx)
            return len(value) if isinstance(value, list) else len(self._text(value))
        if op == "concat":
            return "".join(self._text(ev(x, ctx)) for x in arg)
        if op in ("eq", "ne", "lt", "le", "gt", "ge", "and", "or", "not", "empty", "matches", "in"):
            return self.cond(expr, ctx)
        raise EngineError(f"unknown operator {op!r}")

    def cond(self, expr, ctx):
        if isinstance(expr, bool):
            return expr
        if not isinstance(expr, dict) or len(expr) != 1:
            raise EngineError(f"not a condition: {expr!r}")
        op, arg = next(iter(expr.items()))
        if op == "and":
            return all(self.cond(c, ctx) for c in arg)
        if op == "or":
            return any(self.cond(c, ctx) for c in arg)
        if op == "not":
            return not self.cond(arg, ctx)
        if op == "empty":
            value = self.ev(arg, ctx)
            return value is None or value == "" or value == [] or (isinstance(value, str) and not value.strip())
        if op == "matches":
            return _full_match(arg[1], self.ev(arg[0], ctx))
        if op == "in":
            value, options = self.ev(arg[0], ctx), self.ev(arg[1], ctx)
            return any(_same(value, o) for o in options)
        if op in ("eq", "ne"):
            a, b = self.ev(arg[0], ctx), self.ev(arg[1], ctx)
            same = _same(a, b) if not (isinstance(a, str) and isinstance(b, str)) else a == b
            return same if op == "eq" else not same
        if op in ("lt", "le", "gt", "ge"):
            c = _compare(self.ev(arg[0], ctx), self.ev(arg[1], ctx))
            return {"lt": c < 0, "le": c <= 0, "gt": c > 0, "ge": c >= 0}[op]
        value = self.ev(expr, ctx)
        if isinstance(value, bool):
            return value
        raise EngineError(f"not a condition: {expr!r}")

    def _text(self, value):
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            value = _clean(value)
            return f"{value:.2f}" if isinstance(value, float) else str(value)
        return str(value)

    def _rows(self, entity, where, ctx):
        if entity not in self.store:
            raise EngineError(f"unknown record type {entity!r}")
        if where is None:
            return list(self.store[entity])
        found = []
        for row in self.store[entity]:
            inner = dict(ctx, aliases=dict(ctx["aliases"], row=row))
            if self.cond(where, inner):
                found.append(row)
        return found

    # ---- sessions -----------------------------------------------------------------------------
    def _messages(self):
        defaults = {"required_login": "Please enter your login", "required_password": "Please enter your password",
                    "invalid": "Invalid login or password", "blocked": "Your account is blocked",
                    "expired": "Your session has expired. Please log in again.", "need_login": "Please log in first"}
        return dict(defaults, **(self.users.get("messages") or {}))

    def _check_session(self):
        if not self.users:
            return
        if self.user is None:
            raise _Refused(self._messages()["need_login"], page=self._login_page())
        minutes = self.users.get("session_minutes")
        if minutes and self.last_active is not None and (self.clock - self.last_active) > timedelta(minutes=minutes):
            self.user = None
            self.session = {}
            raise _Refused(self._messages()["expired"], page=self._login_page())
        self.last_active = self.clock

    def login(self, login, password):
        msgs = self._messages()
        if login is None or str(login).strip() == "":
            raise _Refused(msgs["required_login"])
        if password is None or str(password) == "":
            raise _Refused(msgs["required_password"])
        entity = self.users["entity"]
        field = self.users.get("login_field", "email")
        match = next((u for u in self.store[entity] if str(u.get(field, "")).lower() == str(login).strip().lower()), None)
        if match is None or str(match.get(self.users.get("password_field", "password"))) != str(password):
            raise _Refused(msgs["invalid"])
        if self.users.get("blocked_when") is not None and self.cond(self.users["blocked_when"], {"inputs": {}, "aliases": {"user": match}}):
            raise _Refused(msgs["blocked"])
        self.user = match
        self.last_active = self.clock
        self.session = {}
        return match.get(self.users.get("name_field", field))

    # ---- inputs ----------------------------------------------------------------------------------
    def _check_inputs(self, definitions, inputs):
        for spec_input in definitions:
            value = inputs.get(spec_input["name"])
            present = not (value is None or (isinstance(value, str) and value.strip() == ""))
            for check in spec_input.get("checks") or []:
                rule, limit = check["rule"], check.get("value")
                ok = True
                if rule == "required":
                    ok = present
                elif not present:
                    continue
                elif rule == "min_length":
                    ok = len(str(value)) >= limit
                elif rule == "max_length":
                    ok = len(str(value)) <= limit
                elif rule == "pattern":
                    ok = _full_match(limit, value)
                elif rule in ("min", "max"):
                    try:
                        n = _num(value)
                    except EngineError:
                        ok = False
                    else:
                        ok = n >= limit if rule == "min" else n <= limit
                elif rule == "number":
                    try:
                        _num(value)
                    except EngineError:
                        ok = False
                elif rule == "one_of":
                    ok = any(_same(value, o) or str(value).lower() == str(o).lower() for o in limit)
                elif rule == "date":
                    try:
                        _to_date(str(value))
                    except (EngineError, ValueError):
                        ok = False
                elif rule == "not_past":
                    ok = str(value)[:10] >= _iso_date(self.clock.date())
                elif rule == "not_future":
                    ok = str(value)[:10] <= _iso_date(self.clock.date())
                else:
                    raise EngineError(f"unknown input check {rule!r}")
                if not ok:
                    raise _Refused(check["message"])

    # ---- actions ------------------------------------------------------------------------------
    def run_action(self, name, inputs):
        action = self.actions[name]
        for fault_name in self.active_faults:
            fault = self.faults[fault_name]
            if not fault.get("applies_to") or name in fault["applies_to"]:
                raise _Refused(fault["message"])
        if action.get("requires_login", bool(self.users)):
            self._check_session()
        self._check_inputs(action.get("inputs") or [], inputs)
        ctx = {"inputs": inputs, "aliases": {}}
        for load in action.get("load") or []:
            key = self.ev(load["key"], ctx)
            record = next((r for r in self.store[load["entity"]] if _same(r.get(self._key(load["entity"])), key)
                           or str(r.get(self._key(load["entity"]))).lower() == str(key).lower()), None)
            if record is None:
                raise _Refused(load["missing"])
            ctx["aliases"][load["as"]] = record
        for rule in action.get("rules") or []:
            if "unless" in rule and not self.cond(rule["unless"], ctx):
                raise _Refused(self._text(self.ev(rule["message"], ctx)))
            if "when" in rule and self.cond(rule["when"], ctx):
                raise _Refused(self._text(self.ev(rule["message"], ctx)))
        saved = (copy.deepcopy(self.store), dict(self.session), list(self.outbox), dict(self.counters), set(self.created), self.user)
        try:
            for effect in action.get("effects") or []:
                self._apply(effect, ctx)
        except Exception:
            self.store, self.session, self.outbox, self.counters, self.created, self.user = saved
            raise
        message = self._text(self.ev(action["message"], ctx)) if action.get("message") is not None else ""
        returns = {k: self.ev(v, ctx) for k, v in (action.get("returns") or {}).items()}
        return message, returns, action.get("next_page")

    def _apply(self, effect, ctx):
        (op, arg), = effect.items()
        if op == "set":
            record = ctx["aliases"][arg["record"]]
            entity = next((e for e, rows in self.store.items() if any(r is record for r in rows)), None)
            value = self.ev(arg["value"], ctx)
            record[arg["field"]] = self._typed(entity, arg["field"], value) if entity else value
        elif op == "create":
            entity = arg["entity"]
            values = {k: self._typed(entity, k, self.ev(v, ctx)) for k, v in (arg.get("values") or {}).items()}
            key = self._key(entity)
            if values.get(key) is None:
                self.counters[entity] += 1
                fmt = self.entities[entity].get("id_format")
                values[key] = self._id(fmt, self.counters[entity]) if fmt else self.counters[entity]
            record = values
            self.store[entity].append(record)
            self.created.add((entity, str(values[key])))
            if arg.get("as"):
                ctx["aliases"][arg["as"]] = record
        elif op == "delete":
            record = ctx["aliases"][arg["record"]]
            for rows in self.store.values():
                for i, r in enumerate(rows):
                    if r is record:
                        del rows[i]
                        return
        elif op == "set_session":
            self.session[arg["key"]] = self.ev(arg["value"], ctx)
        elif op == "clear_session":
            self.session.pop(arg, None)
        elif op == "send":
            self.outbox.append({k: self._text(self.ev(v, ctx)) for k, v in arg.items()})
        elif op == "logout":
            self.user = None
            self.session = {}
        else:
            raise EngineError(f"unknown effect {op!r}")

    @staticmethod
    def _id(fmt, n):
        return re.sub(r"\{n(?::0?(\d+))?\}", lambda m: str(n).zfill(int(m.group(1) or 0)), fmt)

    # ---- queries ------------------------------------------------------------------------------
    def run_query(self, name, inputs):
        query = self.queries[name]
        if query.get("requires_login", bool(self.users)):
            self._check_session()
        self._check_inputs(query.get("inputs") or [], inputs)
        ctx = {"inputs": inputs, "aliases": {}}
        entity = query["entity"]
        if query.get("key_input"):
            key = inputs.get(query["key_input"])
            record = next((r for r in self.store[entity] if _same(r.get(self._key(entity)), key)
                           or str(r.get(self._key(entity))).lower() == str(key).lower()), None)
            if record is None or (query.get("where") is not None and not self.cond(query["where"], dict(ctx, aliases={"row": record}))):
                raise _Refused(query.get("missing", "Not found"))
            return self._show(query, record), None
        match = query.get("match")
        rows = self._rows(entity, query.get("where"), ctx)
        if match:
            spec_input = match["input"]
            term = inputs.get(spec_input)
            blank = term is None or str(term).strip() == ""
            if blank:
                how = match.get("blank", "all")
                if how.startswith("error:"):
                    raise _Refused(how[len("error:"):])
                if how == "empty":
                    rows = []
            else:
                if match.get("max_length") and len(str(term)) > match["max_length"]:
                    raise _Refused(match.get("too_long", "Search text is too long"))
                needle = str(term).strip().lower()
                mode = match.get("mode", "contains")

                def hit(row):
                    for f in match["fields"]:
                        text = self._text(row.get(f)).lower()
                        if (mode == "equals" and text == needle) or (mode == "starts_with" and text.startswith(needle)) \
                                or (mode == "contains" and needle in text):
                            return True
                    return False
                rows = [r for r in rows if hit(r)]
        for order in reversed(query.get("order_by") or []):
            rows = sorted(rows, key=lambda r: (r.get(order["field"]) is None, r.get(order["field"]) if r.get(order["field"]) is not None else 0),
                          reverse=bool(order.get("desc")))
        shown = [self._show(query, r) for r in rows]
        return shown, (query.get("none_message") if not shown else None)

    @staticmethod
    def _show(query, record):
        fields = query.get("show")
        return {f: record.get(f) for f in fields} if fields else dict(record)

    # ---- the layers candidates call -------------------------------------------------------------
    def ui_open(self, page):
        if page not in (self.spec.get("pages") or []):
            self.message = f"Page not found: {page}"
            return False
        if self.users and page != self._login_page():
            try:
                self._check_session()
            except _Refused as refused:
                self.page, self.message = refused.page or self.page, refused.message
                return False
        self.page, self.message = page, ""
        return True

    def ui_login(self, login, password):
        if self.page != self._login_page():
            self.page = self._login_page()
        try:
            self.login(login, password)
        except _Refused as refused:
            self.message = refused.message
            return False
        self.page = self.spec.get("home_page") or self.page
        self.message = ""
        return True

    def ui_logout(self):
        self.user = None
        self.session = {}
        self.page = self._login_page() if self.users else self.page
        self.message = ""
        return True

    def ui_action(self, name, inputs):
        action = self.actions[name]
        if action.get("page") and self.page != action["page"]:
            self.message = f"{action.get('label', name)} is not available on this page"
            return False
        try:
            message, returns, next_page = self.run_action(name, inputs)
        except _Refused as refused:
            self.message = refused.message
            if refused.page:
                self.page = refused.page
            return False
        self.message = message
        self.last = returns
        if next_page:
            self.page = next_page
        return True

    def ui_query(self, name, inputs):
        query = self.queries[name]
        if query.get("page") and self.page != query["page"]:
            self.message = f"{query.get('label', name)} is not available on this page"
            return None if query.get("key_input") else []
        try:
            result, none_message = self.run_query(name, inputs)
        except _Refused as refused:
            self.message = refused.message
            if refused.page:
                self.page = refused.page
            return None if query.get("key_input") else []
        self.message = none_message or ""
        if query.get("next_page"):
            self.page = query["next_page"]
        return result

    def api_login(self, login, password):
        try:
            name = self.login(login, password)
        except _Refused as refused:
            return {"ok": False, "error": refused.message, "user": None}
        return {"ok": True, "error": None, "user": name}

    def api_action(self, name, inputs):
        try:
            message, returns, _ = self.run_action(name, inputs)
        except _Refused as refused:
            return {"ok": False, "error": refused.message, "message": None}
        return dict({"ok": True, "error": None, "message": message}, **returns)

    def api_query(self, name, inputs):
        try:
            result, none_message = self.run_query(name, inputs)
        except _Refused as refused:
            return {"ok": False, "error": refused.message, "result": None}
        return {"ok": True, "error": None, "result": result, "message": none_message}

    def db_get(self, entity, key):
        record = next((r for r in self.store[entity] if _same(r.get(self._key(entity)), key)
                       or str(r.get(self._key(entity))) == str(key)), None)
        return copy.deepcopy(record)

    def db_find(self, entity, field, value):
        return [copy.deepcopy(r) for r in self.store[entity] if _same(r.get(field), value) or r.get(field) == value]

    def db_count(self, entity, field=None, value=None):
        return len(self.store[entity]) if field is None else len(self.db_find(entity, field, value))

    def db_all(self, entity):
        return copy.deepcopy(self.store[entity])

    def advance_minutes(self, minutes):
        self.clock += timedelta(minutes=int(_num(minutes)))
        return _iso_datetime(self.clock)

    def simulate(self, fault):
        if fault not in self.faults:
            raise EngineError(f"unknown failure {fault!r} - see the list in this file's docstring")
        self.active_faults.add(fault)
        return True

    def teardown(self, key=None):
        if key is None:
            return
        for entity, rows in self.store.items():
            for i, r in enumerate(rows):
                if (entity, str(r.get(self._key(entity)))) in self.created and str(r.get(self._key(entity))) == str(key):
                    del rows[i]
                    return
