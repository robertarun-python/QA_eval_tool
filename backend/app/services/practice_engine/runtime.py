"""
Practice-app engine runtime (Python). Runs a scenario description (see
docs/practice_engine/DESIGN.md). This file is embedded, unchanged, in every
generated Python practice app - so it must stay standard-library only and
self-contained. runtime.js and Runtime.java implement the same behaviour and
pass the same conformance suite.
"""
import copy
import math
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal


class EngineError(Exception):
    """The description asked for something the engine can't do (the validator
    should have caught it) - never a candidate's mistake."""


UNEXPECTED = "Something went wrong. Please check your input and try again."
TOO_LARGE = "That number is too large"
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}


def _num(value):
    if isinstance(value, bool):
        raise EngineError("expected a number, got true/false")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)", value.strip(), re.ASCII):
        return float(value) if "." in value else int(value)
    raise EngineError(f"expected a number, got {value!r}")


def _raw(value):
    """A candidate's input as text - the same characters in Python, JavaScript
    and Java (each language's own str() differs: 1e20, -0.0, 2.0, True)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and math.isfinite(value):
        return str(int(value)) if value == int(value) else format(Decimal(repr(value)), "f")
    return str(value)


def _clean(value):
    """Numbers as the engine returns them: whole numbers as int, others to 10
    places (which removes binary noise such as 0.1 x 3 = 0.30000000000000004
    but keeps a monthly rate like 10.5 / 12 / 100 = 0.00875 exact). Money is
    rounded to 2 places only where it is stored, shown, or rounded on purpose."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _Refused(TOO_LARGE)
        if abs(value) >= 1e15:
            return value  # no binary noise to remove at this size; Decimal would run out of digits
        if value == int(value):
            return int(value)
        value = float(Decimal(repr(value)).quantize(Decimal("1e-10"), rounding=ROUND_HALF_UP))
        return int(value) if value == int(value) else value
    return value


def _round(value, places=2):
    if abs(_num(value)) >= 1e15:
        return _clean(float(_num(value)))
    q = Decimal(1).scaleb(-int(places))
    return _clean(float(Decimal(repr(_clean(float(_num(value))))).quantize(q, rounding=ROUND_HALF_UP)))


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


_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _working_days(start, end):
    """Monday-Friday dates from start to end, both included (0 if end is before start)."""
    d, count = start, 0
    while d <= end:
        count += d.weekday() < 5
        d += timedelta(days=1)
    return count


def _split(text, sep):
    return [part.strip() for part in ("" if text is None else _raw(text)).split(sep) if part.strip()]


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
    return re.fullmatch(pattern, "" if text is None else _raw(text), re.ASCII) is not None  # \d \w \s ASCII-only, as in JS and Java


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
    """A refusal the user sees. status: the HTTP status the practice server's
    API answers with (400 bad input, 401 not signed in, 403 blocked, 404 not
    found, 503 simulated outage, 500 unexpected; a rule may set its own)."""
    def __init__(self, message, page=None, status=400):
        super().__init__(message)
        self.message = message
        self.page = page
        self.status = status


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
        self.failed_logins, self.locked_until = {}, {}
        self.page = self._login_page() if self.users else (self.spec.get("home_page") or (self.spec.get("pages") or ["Home"])[0])
        self.message = ""
        self.last = {}

    def _login_page(self):
        return self.users.get("page") or "Login"

    def _key(self, entity):
        return self.entities[entity].get("key", "id")

    def _by_key(self, entity, key):
        """The record a user means by this key: case and stray spaces ignored."""
        wanted = _raw(key).strip().lower()
        return next((r for r in self.store[entity] if _same(r.get(self._key(entity)), key)
                     or _raw(r.get(self._key(entity))).strip().lower() == wanted), None)

    def _field_type(self, entity, field):
        return (self.entities[entity].get("fields") or {}).get(field, "string")

    def _typed(self, entity, field, value):
        kind = self._field_type(entity, field)
        if value is None:
            return None
        if kind in ("int",):
            return int(_num(value))
        if kind == "money":
            return _round(value, 2)
        if kind == "number":
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
        if op == "var":
            return (ctx["aliases"].get("$vars") or {}).get(arg)
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
        if op in ("min", "max"):
            a, b = (_num(ev(x, ctx)) for x in arg)
            return _clean(float(min(a, b) if op == "min" else max(a, b)))
        if op == "mod":
            a, b = (_num(ev(x, ctx)) for x in arg)
            if b == 0:
                raise _Refused("Cannot divide by zero")
            return _clean(float(a - b * math.floor(a / b)))
        if op == "pow":
            base, times = _num(ev(arg[0], ctx)), _num(ev(arg[1], ctx))
            if times != int(times) or not 0 <= times <= 1200:
                raise EngineError(f"pow needs a whole power from 0 to 1200, got {times!r}")
            result = 1
            for _ in range(int(times)):  # step by step, cleaned like "mul", so all three languages agree to the last place
                result = _clean(float(result * base))
            return result
        if op in ("abs", "floor", "ceil"):
            value = _num(ev(arg, ctx))
            return _clean(float({"abs": abs, "floor": math.floor, "ceil": math.ceil}[op](value)))
        if op == "slice":
            chars = list(self._text(ev(arg[0], ctx)))
            start = int(_num(ev(arg[1], ctx)))
            end = int(_num(ev(arg[2], ctx))) if len(arg) > 2 else len(chars)
            return "".join(chars[start:end])
        if op == "years_between":
            a, b = _to_date(ev(arg[0], ctx)), _to_date(ev(arg[1], ctx))
            return b.year - a.year - ((b.month, b.day) < (a.month, a.day))
        if op == "date":
            return _iso_date(_to_datetime(ev(arg, ctx)))
        if op == "round":
            value, places = (arg + [2])[:2] if isinstance(arg, list) else (arg, 2)
            return _round(ev(value, ctx), ev(places, ctx))
        if op == "add_days":
            return _iso_date(_to_date(ev(arg[0], ctx)) + timedelta(days=int(_num(ev(arg[1], ctx)))))
        if op == "add_months":
            return _iso_date(_add_months(_to_date(ev(arg[0], ctx)), _num(ev(arg[1], ctx))))
        if op == "days_between":
            return (_to_date(ev(arg[1], ctx)) - _to_date(ev(arg[0], ctx))).days
        if op == "add_minutes":
            return _iso_datetime(_to_datetime(ev(arg[0], ctx)) + timedelta(minutes=int(_num(ev(arg[1], ctx)))))
        if op == "minutes_between":
            a, b = _to_datetime(ev(arg[0], ctx)), _to_datetime(ev(arg[1], ctx))
            return int((b - a).total_seconds() // 60)
        if op == "time":
            return _iso_datetime(_to_datetime(ev(arg, ctx)))[11:16]
        if op == "weekday":
            return _WEEKDAYS[_to_date(ev(arg, ctx)).weekday()]
        if op == "working_days":
            return _working_days(_to_date(ev(arg[0], ctx)), _to_date(ev(arg[1], ctx)))
        if op == "split":
            return _split(ev(arg[0], ctx), arg[1] if len(arg) > 1 else ",")
        if op == "occurrences":
            items, value = ev(arg[0], ctx), ev(arg[1], ctx)
            return sum(1 for i in items or [] if _same(i, value) or (isinstance(i, str) and isinstance(value, str) and i.lower() == value.lower()))
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
        if op in ("eq", "ne", "lt", "le", "gt", "ge", "and", "or", "not", "empty", "matches", "in", "contains", "starts_with", "ends_with"):
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
            a, b = self.ev(arg[0], ctx), self.ev(arg[1], ctx)
            if a is None or b is None:  # compared with a missing value: false, never an error
                return False
            c = _compare(a, b)
            return {"lt": c < 0, "le": c <= 0, "gt": c > 0, "ge": c >= 0}[op]
        if op in ("contains", "starts_with", "ends_with"):
            whole, part = self.ev(arg[0], ctx), self.ev(arg[1], ctx)
            if isinstance(whole, list):
                return op == "contains" and any(_same(item, part) for item in whole)
            whole, part = self._text(whole), self._text(part)
            return part in whole if op == "contains" else whole.startswith(part) if op == "starts_with" else whole.endswith(part)
        value = self.ev(expr, ctx)
        if isinstance(value, bool):
            return value
        raise EngineError(f"not a condition: {expr!r}")

    def _text(self, value):
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            # the same text in all three languages: whole numbers as plain digits however large, others with 2 decimals
            value = _round(value, 2)
            return str(int(value)) if value == int(value) else f"{value:.2f}"
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
            raise _Refused(self._messages()["need_login"], page=self._login_page(), status=401)
        minutes = self.users.get("session_minutes")
        if minutes and self.last_active is not None and (self.clock - self.last_active) > timedelta(minutes=minutes):
            self.user = None
            self.session = {}
            raise _Refused(self._messages()["expired"], page=self._login_page(), status=401)
        self.last_active = self.clock

    def login(self, login, password):
        msgs = self._messages()
        if login is None or _raw(login).strip() == "":
            raise _Refused(msgs["required_login"])
        if password is None or _raw(password) == "":
            raise _Refused(msgs["required_password"])
        entity = self.users["entity"]
        field = self.users.get("login_field", "email")
        match = next((u for u in self.store[entity] if _raw(u.get(field, "")).lower() == _raw(login).strip().lower()), None)
        lockout, who = self.users.get("lockout"), _raw(login).strip().lower()
        if match is not None and lockout:
            until = self.locked_until.get(who)
            if until is not None and (until is True or self.clock < until):
                raise _Refused(lockout["message"], status=403)
        if match is None or _raw(match.get(self.users.get("password_field", "password"))) != _raw(password):
            if match is not None and lockout:
                self.failed_logins[who] = self.failed_logins.get(who, 0) + 1
                if self.failed_logins[who] >= lockout["attempts"]:
                    self.failed_logins[who] = 0
                    minutes = lockout.get("minutes")
                    self.locked_until[who] = self.clock + timedelta(minutes=minutes) if minutes else True
                    raise _Refused(lockout["message"], status=403)
            raise _Refused(msgs["invalid"], status=401)
        self.failed_logins.pop(who, None)
        try:
            blocked = self.users.get("blocked_when") is not None and self.cond(self.users["blocked_when"], {"inputs": {}, "aliases": {"user": match}})
        except EngineError:  # a description mistake must never crash signing in
            blocked = False
        if blocked:
            raise _Refused(msgs["blocked"], status=403)
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
                    ok = len(_raw(value)) >= limit
                elif rule == "max_length":
                    ok = len(_raw(value)) <= limit
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
                    ok = any(_same(value, o) or _raw(value).lower() == _raw(o).lower() for o in limit)
                elif rule == "date":
                    try:
                        _to_date(_raw(value))
                    except (EngineError, ValueError):
                        ok = False
                elif rule == "not_past":
                    ok = _raw(value)[:10] >= _iso_date(self.clock.date())
                elif rule == "not_future":
                    ok = _raw(value)[:10] <= _iso_date(self.clock.date())
                else:
                    raise EngineError(f"unknown input check {rule!r}")
                if not ok:
                    raise _Refused(check["message"])

    # ---- actions ------------------------------------------------------------------------------
    def run_action(self, name, inputs):
        """An unexpected problem (e.g. text where a number is needed and the
        description has no check for it) is refused like any error message a
        real application shows - never a crash in the candidate's test."""
        try:
            return self._run_action(name, inputs)
        except _Refused:
            raise
        except Exception:
            raise _Refused(UNEXPECTED, status=500)

    def run_query(self, name, inputs):
        try:
            return self._run_query(name, inputs)
        except _Refused:
            raise
        except Exception:
            raise _Refused(UNEXPECTED, status=500)

    def _run_action(self, name, inputs):
        action = self.actions[name]
        for fault_name in self.active_faults:
            fault = self.faults[fault_name]
            if not fault.get("applies_to") or name in fault["applies_to"]:
                raise _Refused(fault["message"], status=fault.get("status", 503))
        if action.get("requires_login", bool(self.users)):
            self._check_session()
        self._check_inputs(action.get("inputs") or [], inputs)
        ctx = {"inputs": inputs, "aliases": {}}
        for load in action.get("load") or []:
            record = self._by_key(load["entity"], self.ev(load["key"], ctx))
            if record is None:
                raise _Refused(load["missing"], status=404)
            ctx["aliases"][load["as"]] = record
        self._compute(action.get("compute"), ctx)
        self._check_rules(action.get("rules") or [], ctx)
        self._transaction(action.get("effects") or [], ctx)
        message = self._text(self.ev(action["message"], ctx)) if action.get("message") is not None else ""
        returns = {k: self.ev(v, ctx) for k, v in (action.get("returns") or {}).items()}
        return message, returns, action.get("next_page")

    def _compute(self, computed, ctx):
        """Named values, in order - each may use the ones before it: {"var": "name"}."""
        values = ctx["aliases"].setdefault("$vars", {})
        for item in computed or []:
            values[item["name"]] = self.ev(item["value"], ctx)

    def _items(self, source, ctx):
        """What a for_each goes through: records of an entity (matching "where"),
        or the values of a list, each as a record {"value": item}."""
        if "entity" in source:
            return list(self._rows(source["entity"], source.get("where"), ctx))
        return [{"value": v} for v in self.ev(source["list"], ctx) or []]

    def _check_rules(self, rules, ctx):
        """In order; the first that fails refuses. A rule's "then" effects are
        kept even though it refuses (e.g. counting a wrong PIN)."""
        for rule in rules:
            if "for_each" in rule:
                each = rule["for_each"]
                for item in self._items(each, ctx):
                    self._check_rules([{k: v for k, v in rule.items() if k != "for_each"}],
                                      dict(ctx, aliases=dict(ctx["aliases"], **{each["as"]: item})))
                continue
            refused = ("unless" in rule and not self.cond(rule["unless"], ctx)) or ("when" in rule and self.cond(rule["when"], ctx))
            if refused:
                if rule.get("then"):
                    self._transaction(rule["then"], ctx)
                raise _Refused(self._text(self.ev(rule["message"], ctx)), status=rule.get("status", 400))

    def _transaction(self, effects, ctx):
        """All the effects, or - if one fails - none of them."""
        field = self.users.get("login_field", "email")
        who = None if self.user is None else self.user.get(field)
        saved = (copy.deepcopy(self.store), copy.deepcopy(self.session), copy.deepcopy(self.outbox), dict(self.counters), set(self.created))
        try:
            for effect in effects:
                self._apply(effect, ctx)
        except Exception:
            self.store, self.session, self.outbox, self.counters, self.created = saved
            # signed in as before, as the restored copy of their record
            self.user = None if who is None else next((u for u in self.store[self.users["entity"]] if u.get(field) == who), None)
            raise

    def _apply(self, effect, ctx):
        (op, arg), = effect.items()
        if op == "if":
            branch = arg[1] if self.cond(arg[0], ctx) else (arg[2] if len(arg) > 2 else [])
            for inner in branch:
                self._apply(inner, ctx)
            return
        if op == "for_each":
            for item in self._items(arg, ctx):
                inner_ctx = dict(ctx, aliases=dict(ctx["aliases"], **{arg["as"]: item}))
                for inner in arg["effects"]:
                    self._apply(inner, inner_ctx)
            return
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
                fmt = self.entities[entity].get("id_format")
                taken = {_raw(r.get(key)).lower() for r in self.store[entity]}
                while True:  # never an id already stored (e.g. a record a test inserted into the database itself)
                    self.counters[entity] += 1
                    values[key] = self._id(fmt, self.counters[entity]) if fmt else self.counters[entity]
                    if _raw(values[key]).lower() not in taken:
                        break
            record = values
            self.store[entity].append(record)
            self.created.add((entity, _raw(values[key])))
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
    def _run_query(self, name, inputs):
        query = self.queries[name]
        if query.get("requires_login", bool(self.users)):
            self._check_session()
        self._check_inputs(query.get("inputs") or [], inputs)
        ctx = {"inputs": inputs, "aliases": {}}
        for load in query.get("load") or []:  # related records, as for actions
            record = self._by_key(load["entity"], self.ev(load["key"], ctx))
            if record is None:
                raise _Refused(load["missing"], status=404)
            ctx["aliases"][load["as"]] = record
        self._compute(query.get("compute"), ctx)
        self._check_rules(query.get("rules") or [], ctx)
        entity = query["entity"]
        if query.get("key_input"):
            record = self._by_key(entity, inputs.get(query["key_input"]))
            if record is None or (query.get("where") is not None and not self.cond(query["where"], dict(ctx, aliases={"row": record}))):
                raise _Refused(query.get("missing", "Not found"), status=404)
            return self._show(query, record, ctx), None
        match = query.get("match")
        rows = self._rows(entity, query.get("where"), ctx)
        if match:
            spec_input = match["input"]
            term = inputs.get(spec_input)
            blank = term is None or _raw(term).strip() == ""
            if blank:
                how = match.get("blank", "all")
                if how.startswith("error:"):
                    raise _Refused(how[len("error:"):])
                if how == "empty":
                    rows = []
            else:
                if match.get("max_length") and len(_raw(term)) > match["max_length"]:
                    raise _Refused(match.get("too_long", "Search text is too long"))
                needle = _raw(term).strip().lower()
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
        shown = [self._show(query, r, ctx) for r in rows]
        return shown, (query.get("none_message") if not shown else None)

    def _show(self, query, record, ctx):
        """The fields shown for a record; a shown item can also be computed:
        {"name": "grade", "value": <expression on row.<field>>}."""
        fields = query.get("show")
        if not fields:
            return copy.deepcopy(record)
        inner = dict(ctx, aliases=dict(ctx["aliases"], row=record, **({query["as"]: record} if query.get("as") else {})))
        return {f["name"] if isinstance(f, dict) else f: self.ev(f["value"], inner) if isinstance(f, dict) else copy.deepcopy(record.get(f))
                for f in fields}

    # ---- the layers candidates call -------------------------------------------------------------
    def ui_open(self, page):
        if page not in (self.spec.get("pages") or []):
            self.message = f"Page not found: {page}"
            return False
        if self.users and page != self._login_page() and page not in (self.spec.get("public_pages") or []):
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
                       or _raw(r.get(self._key(entity))) == _raw(key)), None)
        return copy.deepcopy(record)

    def db_find(self, entity, field, value):
        return [copy.deepcopy(r) for r in self.store[entity] if _same(r.get(field), value) or r.get(field) == value]

    def db_count(self, entity, field=None, value=None):
        return len(self.store[entity]) if field is None else len(self.db_find(entity, field, value))

    def db_all(self, entity):
        return copy.deepcopy(self.store[entity])

    def advance_minutes(self, minutes):
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 5256000:
            raise EngineError("Test.advance_minutes needs a whole number of minutes from 0 to 5256000 (10 years)")
        self.clock += timedelta(minutes=minutes)
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
                if (entity, _raw(r.get(self._key(entity)))) in self.created and _raw(r.get(self._key(entity))) == _raw(key):
                    del rows[i]
                    return
