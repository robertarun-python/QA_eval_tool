// Practice-app engine runtime (JavaScript). Same behaviour as runtime.py -
// see docs/practice_engine/DESIGN.md; the conformance tests run the same
// descriptions and checklists in Python, JavaScript and Java.

class EngineError extends Error {}
class Refused extends Error {
  constructor(message, page) { super(message); this.message = message; this.page = page || null; }
}

const UNEXPECTED = "Something went wrong. Please check your input and try again.";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const SYMBOLS = { INR: "₹", USD: "$", EUR: "€", GBP: "£" };
const pad = (n, w) => String(n).padStart(w, "0");
const clone = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));

function num(value) {
  if (typeof value === "boolean") throw new EngineError("expected a number, got true/false");
  if (typeof value === "number") return value;
  if (typeof value === "string" && /^[+-]?(\d+\.?\d*|\.\d+)$/.test(value.trim())) return Number(value.trim());
  throw new EngineError(`expected a number, got ${JSON.stringify(value)}`);
}

// Half-up rounding on the shortest decimal form of the number - the same
// digits Python's repr and Java's Double.toString give, so all three agree.
function roundHalfUp(value, places) {
  const x = num(value);
  const s = Math.abs(x).toString();
  if (s.includes("e")) return Math.round(x * 10 ** places) / 10 ** places;
  const [whole, frac = ""] = s.split(".");
  if (frac.length <= places) return x;
  let digits = whole + frac.slice(0, places);
  const up = Number(frac[places]) >= 5;
  let n = BigInt(digits) + (up ? 1n : 0n);
  let str = n.toString().padStart(places + 1, "0");
  const result = Number(str.slice(0, str.length - places) + (places ? "." + str.slice(str.length - places) : ""));
  return x < 0 ? -result : result;
}

// Whole numbers stay whole; others to 10 places (removes binary noise, keeps
// a rate like 0.00875 exact). Money is rounded to 2 places where stored or shown.
function clean(value) {
  if (typeof value === "number" && !Number.isInteger(value)) return roundHalfUp(value, 10);
  return value;
}
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function workingDays(start, end) {
  let count = 0;
  for (const d = new Date(start.getTime()); d <= end; d.setUTCDate(d.getUTCDate() + 1)) if (d.getUTCDay() % 6 !== 0) count += 1;
  return count;
}
const split = (value, sep) => (value === null || value === undefined ? "" : String(value)).split(sep).map((p) => p.trim()).filter((p) => p);

function toDate(value) {
  if (typeof value === "string" && value.length >= 10) return new Date(Date.UTC(+value.slice(0, 4), +value.slice(5, 7) - 1, +value.slice(8, 10)));
  throw new EngineError(`expected a date YYYY-MM-DD, got ${JSON.stringify(value)}`);
}
function toDateTime(value) {
  if (typeof value === "string" && value.length >= 16)
    return new Date(Date.UTC(+value.slice(0, 4), +value.slice(5, 7) - 1, +value.slice(8, 10), +value.slice(11, 13), +value.slice(14, 16)));
  if (typeof value === "string" && value.length === 10) return toDate(value);
  throw new EngineError(`expected a date-time YYYY-MM-DDTHH:MM, got ${JSON.stringify(value)}`);
}
const isoDate = (d) => `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1, 2)}-${pad(d.getUTCDate(), 2)}`;
const isoDateTime = (d) => `${isoDate(d)}T${pad(d.getUTCHours(), 2)}:${pad(d.getUTCMinutes(), 2)}`;
function addMonths(d, n) {
  const total = d.getUTCMonth() + Number(n);
  const year = d.getUTCFullYear() + Math.floor(total / 12);
  const month = ((total % 12) + 12) % 12;
  const last = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  return new Date(Date.UTC(year, month, Math.min(d.getUTCDate(), last)));
}
function formatDate(value, fmt) {
  const d = toDate(value);
  const dd = pad(d.getUTCDate(), 2), mm = pad(d.getUTCMonth() + 1, 2), yyyy = pad(d.getUTCFullYear(), 4), mon = MONTHS[d.getUTCMonth()];
  const out = { "YYYY-MM-DD": `${yyyy}-${mm}-${dd}`, "DD-MM-YYYY": `${dd}-${mm}-${yyyy}`, "DD/MM/YYYY": `${dd}/${mm}/${yyyy}`,
    "MM/DD/YYYY": `${mm}/${dd}/${yyyy}`, "DD-Mon-YYYY": `${dd}-${mon}-${yyyy}`, "DD Mon YYYY": `${dd} ${mon} ${yyyy}` }[fmt];
  if (out === undefined) throw new EngineError(`unknown date format ${JSON.stringify(fmt)}`);
  return out;
}
function group(digits, indian) {
  if (!indian || digits.length <= 3) {
    const parts = [];
    while (digits.length > 3) { parts.unshift(digits.slice(-3)); digits = digits.slice(0, -3); }
    parts.unshift(digits);
    return parts.join(",");
  }
  let head = digits.slice(0, -3);
  const tail = digits.slice(-3);
  const parts = [];
  while (head.length > 2) { parts.unshift(head.slice(-2)); head = head.slice(0, -2); }
  if (head) parts.unshift(head);
  return parts.concat([tail]).join(",");
}
function formatMoney(value, currency) {
  const amount = roundHalfUp(num(value), 2);
  const sign = amount < 0 ? "-" : "";
  const [whole, cents] = Math.abs(amount).toFixed(2).split(".");
  return `${sign}${SYMBOLS[currency] !== undefined ? SYMBOLS[currency] : currency + " "}${group(whole, currency === "INR")}.${cents}`;
}
const chars = (s) => Array.from(String(s)).length;  // characters, not UTF-16 units - as in Python
const fullMatch = (pattern, text) => new RegExp(`^(?:${pattern})$`).test(text === null || text === undefined ? "" : String(text));
function same(a, b) {
  if (typeof a === "boolean" || typeof b === "boolean") return a === b;
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) < 1e-9;
  return a === b || (a === undefined && b === null) || (a === null && b === undefined);
}
function compare(a, b) {
  if (typeof a === "number" && typeof b === "number") return a > b ? 1 : a < b ? -1 : 0;
  if (typeof a === "string" && typeof b === "string") return a > b ? 1 : a < b ? -1 : 0;
  if (a === null || a === undefined || b === null || b === undefined) throw new EngineError("can't compare a missing value");
  return compare(num(a), num(b));
}
function text(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") { const v = roundHalfUp(clean(value), 2); return Number.isInteger(v) ? String(v) : v.toFixed(2); }
  return String(value);
}
const one = (expr) => { const keys = Object.keys(expr); if (keys.length !== 1) throw new EngineError("an expression has exactly one operator"); return [keys[0], expr[keys[0]]]; };
const isPlain = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

class Engine {
  constructor(spec) {
    this.spec = spec;
    this.entities = spec.entities || {};
    this.actions = Object.fromEntries((spec.actions || []).map((a) => [a.name, a]));
    this.queries = Object.fromEntries((spec.queries || []).map((q) => [q.name, q]));
    this.faults = Object.fromEntries((spec.faults || []).map((f) => [f.name, f]));
    this.users = spec.users || null;
    this.reset();
  }
  reset() {
    this.store = {};
    this.counters = {};
    for (const name of Object.keys(this.entities)) {
      this.store[name] = clone(((this.spec.data || {})[name]) || []);
      this.counters[name] = this.store[name].length;
    }
    this.created = new Set();
    this.clock = toDateTime(this.spec.now || "2025-01-15T10:00");
    this.session = {};
    this.user = null;
    this.lastActive = null;
    this.activeFaults = new Set();
    this.outbox = [];
    this.failedLogins = {}; this.lockedUntil = {};
    this.page = this.users ? this.loginPage() : (this.spec.home_page || (this.spec.pages || ["Home"])[0]);
    this.message = "";
    this.last = {};
  }
  loginPage() { return (this.users && this.users.page) || "Login"; }
  key(entity) { return this.entities[entity].key || "id"; }
  typed(entity, field, value) {
    const kind = ((this.entities[entity] || {}).fields || {})[field] || "string";
    if (value === null || value === undefined) return null;
    if (kind === "int") return Math.trunc(num(value));
    if (kind === "money") return roundHalfUp(clean(num(value)), 2);
    if (kind === "number") return clean(num(value));
    if (kind === "bool") return Boolean(value);
    return value;
  }

  ev(expr, ctx) {
    if (Array.isArray(expr)) return expr.map((x) => this.ev(x, ctx));
    if (!isPlain(expr)) return expr;
    const [op, arg] = one(expr);
    switch (op) {
      case "input": return ctx.inputs[arg] === undefined ? null : ctx.inputs[arg];
      case "field": {
        const i = String(arg).indexOf(".");
        const alias = i < 0 ? String(arg) : String(arg).slice(0, i), field = i < 0 ? "" : String(arg).slice(i + 1);
        const record = ctx.aliases[alias];
        if (!record) throw new EngineError(`no record called ${alias} here`);
        return record[field] === undefined ? null : record[field];
      }
      case "user": return this.user === null ? null : (this.user[arg] === undefined ? null : this.user[arg]);
      case "session": return this.session[arg] === undefined ? null : this.session[arg];
      case "today": return isoDate(this.clock);
      case "now": return isoDateTime(this.clock);
      case "add": case "sub": case "mul": case "div": {
        const a = num(this.ev(arg[0], ctx)), b = num(this.ev(arg[1], ctx));
        if (op === "div" && b === 0) throw new Refused("Cannot divide by zero");
        return clean(op === "add" ? a + b : op === "sub" ? a - b : op === "mul" ? a * b : a / b);
      }
      case "round": {
        const [value, places] = Array.isArray(arg) ? [arg[0], arg.length > 1 ? arg[1] : 2] : [arg, 2];
        return roundHalfUp(this.ev(value, ctx), num(this.ev(places, ctx)));
      }
      case "add_days": { const d = toDate(this.ev(arg[0], ctx)); d.setUTCDate(d.getUTCDate() + Math.trunc(num(this.ev(arg[1], ctx)))); return isoDate(d); }
      case "add_months": return isoDate(addMonths(toDate(this.ev(arg[0], ctx)), num(this.ev(arg[1], ctx))));
      case "days_between": return Math.round((toDate(this.ev(arg[1], ctx)) - toDate(this.ev(arg[0], ctx))) / 86400000);
      case "add_minutes": return isoDateTime(new Date(toDateTime(this.ev(arg[0], ctx)).getTime() + Math.trunc(num(this.ev(arg[1], ctx))) * 60000));
      case "minutes_between": return Math.floor((toDateTime(this.ev(arg[1], ctx)) - toDateTime(this.ev(arg[0], ctx))) / 60000);
      case "time": return isoDateTime(toDateTime(this.ev(arg, ctx))).slice(11, 16);
      case "weekday": return WEEKDAYS[(toDate(this.ev(arg, ctx)).getUTCDay() + 6) % 7];
      case "working_days": return workingDays(toDate(this.ev(arg[0], ctx)), toDate(this.ev(arg[1], ctx)));
      case "split": return split(this.ev(arg[0], ctx), arg.length > 1 ? arg[1] : ",");
      case "occurrences": {
        const items = this.ev(arg[0], ctx) || [], value = this.ev(arg[1], ctx);
        return items.filter((i) => same(i, value) || (typeof i === "string" && typeof value === "string" && i.toLowerCase() === value.toLowerCase())).length;
      }
      case "minutes_since": { const then = this.ev(arg, ctx); return then === null ? null : Math.floor((this.clock - toDateTime(then)) / 60000); }
      case "count": case "sum": case "exists": {
        const rows = this.rows(arg.entity, arg.where, ctx);
        if (op === "count") return rows.length;
        if (op === "exists") return rows.length > 0;
        return clean(rows.reduce((s, r) => s + num(r[arg.field] || 0), 0));
      }
      case "if": return this.cond(arg[0], ctx) ? this.ev(arg[1], ctx) : this.ev(arg[2], ctx);
      case "format": {
        const values = arg.length > 1 ? arg[1] : {};
        const resolved = {};
        for (const k of Object.keys(values)) resolved[k] = text(this.ev(values[k], ctx));
        return String(arg[0]).replace(/\{(\w+)\}/g, (m, k) => (k in resolved ? resolved[k] : m));
      }
      case "format_date": return formatDate(this.ev(arg[0], ctx), arg[1]);
      case "format_money": return formatMoney(this.ev(arg[0], ctx), arg.length > 1 ? arg[1] : "INR");
      case "upper": return text(this.ev(arg, ctx)).toUpperCase();
      case "lower": return text(this.ev(arg, ctx)).toLowerCase();
      case "trim": return text(this.ev(arg, ctx)).trim();
      case "length": { const v = this.ev(arg, ctx); return Array.isArray(v) ? v.length : chars(text(v)); }
      case "concat": return arg.map((x) => text(this.ev(x, ctx))).join("");
      case "eq": case "ne": case "lt": case "le": case "gt": case "ge": case "and": case "or": case "not": case "empty": case "matches": case "in":
        return this.cond(expr, ctx);
      default: throw new EngineError(`unknown operator ${op}`);
    }
  }
  cond(expr, ctx) {
    if (typeof expr === "boolean") return expr;
    if (!isPlain(expr)) throw new EngineError(`not a condition: ${JSON.stringify(expr)}`);
    const [op, arg] = one(expr);
    switch (op) {
      case "and": return arg.every((c) => this.cond(c, ctx));
      case "or": return arg.some((c) => this.cond(c, ctx));
      case "not": return !this.cond(arg, ctx);
      case "empty": { const v = this.ev(arg, ctx); return v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0) || (typeof v === "string" && v.trim() === ""); }
      case "matches": return fullMatch(arg[1], this.ev(arg[0], ctx));
      case "in": { const v = this.ev(arg[0], ctx); return this.ev(arg[1], ctx).some((o) => same(v, o)); }
      case "eq": case "ne": { const a = this.ev(arg[0], ctx), b = this.ev(arg[1], ctx); const s = same(a, b); return op === "eq" ? s : !s; }
      case "lt": case "le": case "gt": case "ge": {
        const c = compare(this.ev(arg[0], ctx), this.ev(arg[1], ctx));
        return op === "lt" ? c < 0 : op === "le" ? c <= 0 : op === "gt" ? c > 0 : c >= 0;
      }
      default: { const v = this.ev(expr, ctx); if (typeof v === "boolean") return v; throw new EngineError(`not a condition: ${JSON.stringify(expr)}`); }
    }
  }
  rows(entity, where, ctx) {
    if (!(entity in this.store)) throw new EngineError(`unknown record type ${entity}`);
    if (where === undefined || where === null) return this.store[entity].slice();
    return this.store[entity].filter((row) => this.cond(where, { inputs: ctx.inputs, aliases: Object.assign({}, ctx.aliases, { row }) }));
  }

  messages() {
    return Object.assign({ required_login: "Please enter your login", required_password: "Please enter your password",
      invalid: "Invalid login or password", blocked: "Your account is blocked",
      expired: "Your session has expired. Please log in again.", need_login: "Please log in first" }, (this.users && this.users.messages) || {});
  }
  checkSession() {
    if (!this.users) return;
    if (this.user === null) throw new Refused(this.messages().need_login, this.loginPage());
    const minutes = this.users.session_minutes;
    if (minutes && this.lastActive !== null && (this.clock - this.lastActive) > minutes * 60000) {
      this.user = null; this.session = {};
      throw new Refused(this.messages().expired, this.loginPage());
    }
    this.lastActive = new Date(this.clock.getTime());
  }
  login(login, password) {
    const msgs = this.messages();
    if (login === null || login === undefined || String(login).trim() === "") throw new Refused(msgs.required_login);
    if (password === null || password === undefined || String(password) === "") throw new Refused(msgs.required_password);
    const field = this.users.login_field || "email";
    const match = this.store[this.users.entity].find((u) => String(u[field] === undefined ? "" : u[field]).toLowerCase() === String(login).trim().toLowerCase());
    const lockout = this.users.lockout, who = String(login).trim().toLowerCase();
    if (match && lockout && who in this.lockedUntil) {
      const until = this.lockedUntil[who];
      if (until === true || this.clock < until) throw new Refused(lockout.message);
    }
    if (!match || String(match[this.users.password_field || "password"]) !== String(password)) {
      if (match && lockout) {
        this.failedLogins[who] = (this.failedLogins[who] || 0) + 1;
        if (this.failedLogins[who] >= lockout.attempts) {
          this.failedLogins[who] = 0;
          this.lockedUntil[who] = lockout.minutes ? new Date(this.clock.getTime() + lockout.minutes * 60000) : true;
          throw new Refused(lockout.message);
        }
      }
      throw new Refused(msgs.invalid);
    }
    delete this.failedLogins[who];
    if (this.users.blocked_when && this.cond(this.users.blocked_when, { inputs: {}, aliases: { user: match } })) throw new Refused(msgs.blocked);
    this.user = match;
    this.lastActive = new Date(this.clock.getTime());
    this.session = {};
    const nameField = this.users.name_field || field;
    return match[nameField] === undefined ? null : match[nameField];
  }

  checkInputs(definitions, inputs) {
    for (const def of definitions) {
      const value = inputs[def.name];
      const present = !(value === null || value === undefined || (typeof value === "string" && value.trim() === ""));
      for (const check of def.checks || []) {
        const rule = check.rule, limit = check.value;
        let ok = true;
        if (rule === "required") ok = present;
        else if (!present) continue;
        else if (rule === "min_length") ok = chars(value) >= limit;
        else if (rule === "max_length") ok = chars(value) <= limit;
        else if (rule === "pattern") ok = fullMatch(limit, value);
        else if (rule === "min" || rule === "max") { try { const n = num(value); ok = rule === "min" ? n >= limit : n <= limit; } catch (e) { ok = false; } }
        else if (rule === "number") { try { num(value); } catch (e) { ok = false; } }
        else if (rule === "one_of") ok = limit.some((o) => same(value, o) || String(value).toLowerCase() === String(o).toLowerCase());
        else if (rule === "date") { try { const d = toDate(String(value)); ok = !isNaN(d.getTime()) && isoDate(d) === String(value).slice(0, 10); } catch (e) { ok = false; } }
        else if (rule === "not_past") ok = String(value).slice(0, 10) >= isoDate(this.clock);
        else if (rule === "not_future") ok = String(value).slice(0, 10) <= isoDate(this.clock);
        else throw new EngineError(`unknown input check ${rule}`);
        if (!ok) throw new Refused(check.message);
      }
    }
  }
  findByKey(entity, key) {
    const k = this.key(entity);
    const wanted = String(key).trim().toLowerCase();
    return this.store[entity].find((r) => same(r[k], key) || String(r[k]).trim().toLowerCase() === wanted) || null;
  }

  // An unexpected problem is refused like any error message a real application shows.
  runAction(name, inputs) {
    try { return this.runActionCore(name, inputs); } catch (e) { if (e instanceof Refused) throw e; throw new Refused(UNEXPECTED); }
  }
  runQuery(name, inputs) {
    try { return this.runQueryCore(name, inputs); } catch (e) { if (e instanceof Refused) throw e; throw new Refused(UNEXPECTED); }
  }
  runActionCore(name, inputs) {
    const action = this.actions[name];
    for (const f of this.activeFaults) {
      const fault = this.faults[f];
      if (!fault.applies_to || fault.applies_to.length === 0 || fault.applies_to.includes(name)) throw new Refused(fault.message);
    }
    if (action.requires_login !== undefined ? action.requires_login : !!this.users) this.checkSession();
    this.checkInputs(action.inputs || [], inputs);
    const ctx = { inputs, aliases: {} };
    for (const load of action.load || []) {
      const record = this.findByKey(load.entity, this.ev(load.key, ctx));
      if (!record) throw new Refused(load.missing);
      ctx.aliases[load.as] = record;
    }
    this.checkRules(action.rules || [], ctx);
    this.transaction(action.effects || [], ctx);
    const message = action.message !== undefined && action.message !== null ? text(this.ev(action.message, ctx)) : "";
    const returns = {};
    for (const k of Object.keys(action.returns || {})) returns[k] = this.ev(action.returns[k], ctx);
    return [message, returns, action.next_page || null];
  }
  entityOf(record) {
    for (const [e, rows] of Object.entries(this.store)) if (rows.includes(record)) return e;
    return null;
  }
  items(source, ctx) {
    if ("entity" in source) return this.rows(source.entity, source.where, ctx).slice();
    return (this.ev(source.list, ctx) || []).map((v) => ({ value: v }));
  }
  checkRules(rules, ctx) {
    for (const rule of rules) {
      if (rule.for_each) {
        const each = rule.for_each, inner = Object.assign({}, rule);
        delete inner.for_each;
        for (const item of this.items(each, ctx)) this.checkRules([inner], { inputs: ctx.inputs, aliases: Object.assign({}, ctx.aliases, { [each.as]: item }) });
        continue;
      }
      const refused = ("unless" in rule && !this.cond(rule.unless, ctx)) || ("when" in rule && this.cond(rule.when, ctx));
      if (refused) {
        if (rule.then && rule.then.length) this.transaction(rule.then, ctx);
        throw new Refused(text(this.ev(rule.message, ctx)));
      }
    }
  }
  transaction(effects, ctx) {
    const field = (this.users && this.users.login_field) || "email";
    const who = this.user === null ? null : this.user[field];
    const saved = [clone(this.store), clone(this.session), clone(this.outbox), clone(this.counters), new Set(this.created)];
    try {
      for (const effect of effects) this.apply(effect, ctx);
    } catch (e) {
      [this.store, this.session, this.outbox, this.counters, this.created] = saved;
      this.user = who === null ? null : (this.store[this.users.entity].find((u) => u[field] === who) || null);
      throw e;
    }
  }
  apply(effect, ctx) {
    const [op, arg] = one(effect);
    if (op === "if") {
      const branch = this.cond(arg[0], ctx) ? arg[1] : (arg.length > 2 ? arg[2] : []);
      for (const inner of branch) this.apply(inner, ctx);
      return;
    }
    if (op === "for_each") {
      for (const item of this.items(arg, ctx)) {
        const inner = { inputs: ctx.inputs, aliases: Object.assign({}, ctx.aliases, { [arg.as]: item }) };
        for (const e of arg.effects) this.apply(e, inner);
      }
      return;
    }
    if (op === "set") {
      const record = ctx.aliases[arg.record];
      const entity = this.entityOf(record);
      const value = this.ev(arg.value, ctx);
      record[arg.field] = entity ? this.typed(entity, arg.field, value) : value;
    } else if (op === "create") {
      const entity = arg.entity;
      const values = {};
      for (const k of Object.keys(arg.values || {})) values[k] = this.typed(entity, k, this.ev(arg.values[k], ctx));
      const k = this.key(entity);
      if (values[k] === null || values[k] === undefined) {
        this.counters[entity] += 1;
        const fmt = this.entities[entity].id_format;
        values[k] = fmt ? Engine.makeId(fmt, this.counters[entity]) : this.counters[entity];
      }
      this.store[entity].push(values);
      this.created.add(`${entity}\u0000${values[k]}`);
      if (arg.as) ctx.aliases[arg.as] = values;
    } else if (op === "delete") {
      const record = ctx.aliases[arg.record];
      for (const rows of Object.values(this.store)) { const i = rows.indexOf(record); if (i >= 0) { rows.splice(i, 1); return; } }
    } else if (op === "set_session") {
      this.session[arg.key] = this.ev(arg.value, ctx);
    } else if (op === "clear_session") {
      delete this.session[arg];
    } else if (op === "send") {
      const message = {};
      for (const k of Object.keys(arg)) message[k] = text(this.ev(arg[k], ctx));
      this.outbox.push(message);
    } else if (op === "logout") {
      this.user = null; this.session = {};
    } else throw new EngineError(`unknown effect ${op}`);
  }
  static makeId(fmt, n) { return fmt.replace(/\{n(?::0?(\d+))?\}/g, (m, w) => String(n).padStart(Number(w || 0), "0")); }

  runQueryCore(name, inputs) {
    const query = this.queries[name];
    if (query.requires_login !== undefined ? query.requires_login : !!this.users) this.checkSession();
    this.checkInputs(query.inputs || [], inputs);
    const ctx = { inputs, aliases: {} };
    this.checkRules(query.rules || [], ctx);
    const entity = query.entity;
    if (query.key_input) {
      const record = this.findByKey(entity, inputs[query.key_input]);
      if (!record || (query.where && !this.cond(query.where, { inputs, aliases: { row: record } }))) throw new Refused(query.missing || "Not found");
      return [this.show(query, record, ctx), null];
    }
    const match = query.match;
    let rows = this.rows(entity, query.where, ctx);
    if (match) {
      const term = inputs[match.input];
      const blank = term === null || term === undefined || String(term).trim() === "";
      if (blank) {
        const how = match.blank || "all";
        if (how.startsWith("error:")) throw new Refused(how.slice("error:".length));
        if (how === "empty") rows = [];
      } else {
        if (match.max_length && chars(term) > match.max_length) throw new Refused(match.too_long || "Search text is too long");
        const needle = String(term).trim().toLowerCase();
        const mode = match.mode || "contains";
        rows = rows.filter((row) => match.fields.some((f) => {
          const t = text(row[f]).toLowerCase();
          return (mode === "equals" && t === needle) || (mode === "starts_with" && t.startsWith(needle)) || (mode === "contains" && t.includes(needle));
        }));
      }
    }
    for (const order of (query.order_by || []).slice().reverse()) {
      const f = order.field, dir = order.desc ? -1 : 1;
      rows = rows.map((r, i) => [r, i]).sort((x, y) => {
        const a = x[0][f], b = y[0][f];
        const an = a === null || a === undefined, bn = b === null || b === undefined;
        let c;
        if (an || bn) c = an && bn ? 0 : (an ? 1 : -1);
        else c = compare(a, b) * dir;
        if (an !== bn && dir < 0) c = -c;
        return c !== 0 ? c : x[1] - y[1];
      }).map((p) => p[0]);
    }
    const shown = rows.map((r) => this.show(query, r, ctx));
    return [shown, shown.length === 0 ? (query.none_message === undefined ? null : query.none_message) : null];
  }
  show(query, record, ctx) {
    if (!query.show) return clone(record);
    const aliases = Object.assign({}, ctx.aliases, { row: record });
    if (query.as) aliases[query.as] = record;
    const out = {};
    for (const f of query.show) {
      if (isPlain(f)) out[f.name] = this.ev(f.value, { inputs: ctx.inputs, aliases });
      else out[f] = record[f] === undefined ? null : clone(record[f]);
    }
    return out;
  }

  uiOpen(page) {
    if (!(this.spec.pages || []).includes(page)) { this.message = `Page not found: ${page}`; return false; }
    if (this.users && page !== this.loginPage() && !(this.spec.public_pages || []).includes(page)) {
      try { this.checkSession(); } catch (e) {
        if (!(e instanceof Refused)) throw e;
        this.page = e.page || this.page; this.message = e.message; return false;
      }
    }
    this.page = page; this.message = ""; return true;
  }
  uiLogin(login, password) {
    this.page = this.loginPage();
    try { this.login(login, password); } catch (e) {
      if (!(e instanceof Refused)) throw e;
      this.message = e.message; return false;
    }
    this.page = this.spec.home_page || this.page; this.message = ""; return true;
  }
  uiLogout() { this.user = null; this.session = {}; if (this.users) this.page = this.loginPage(); this.message = ""; return true; }
  uiAction(name, inputs) {
    const action = this.actions[name];
    if (action.page && this.page !== action.page) { this.message = `${action.label || name} is not available on this page`; return false; }
    let result;
    try { result = this.runAction(name, inputs); } catch (e) {
      if (!(e instanceof Refused)) throw e;
      this.message = e.message; if (e.page) this.page = e.page; return false;
    }
    this.message = result[0]; this.last = result[1]; if (result[2]) this.page = result[2]; return true;
  }
  uiQuery(name, inputs) {
    const query = this.queries[name];
    const empty = query.key_input ? null : [];
    if (query.page && this.page !== query.page) { this.message = `${query.label || name} is not available on this page`; return empty; }
    let result;
    try { result = this.runQuery(name, inputs); } catch (e) {
      if (!(e instanceof Refused)) throw e;
      this.message = e.message; if (e.page) this.page = e.page; return empty;
    }
    this.message = result[1] || "";
    if (query.next_page) this.page = query.next_page;
    return result[0];
  }
  apiLogin(login, password) {
    try { return { ok: true, error: null, user: this.login(login, password) }; } catch (e) {
      if (!(e instanceof Refused)) throw e;
      return { ok: false, error: e.message, user: null };
    }
  }
  apiAction(name, inputs) {
    try { const [message, returns] = this.runAction(name, inputs); return Object.assign({ ok: true, error: null, message }, returns); } catch (e) {
      if (!(e instanceof Refused)) throw e;
      return { ok: false, error: e.message, message: null };
    }
  }
  apiQuery(name, inputs) {
    try { const [result, none] = this.runQuery(name, inputs); return { ok: true, error: null, result, message: none }; } catch (e) {
      if (!(e instanceof Refused)) throw e;
      return { ok: false, error: e.message, result: null };
    }
  }
  dbGet(entity, key) {
    const k = this.key(entity);
    const r = this.store[entity].find((row) => same(row[k], key) || String(row[k]) === String(key));
    return r ? clone(r) : null;
  }
  dbFind(entity, field, value) { return this.store[entity].filter((r) => same(r[field], value)).map(clone); }
  dbCount(entity, field, value) { return field === null || field === undefined ? this.store[entity].length : this.dbFind(entity, field, value).length; }
  dbAll(entity) { return clone(this.store[entity]); }
  advanceMinutes(minutes) {
    if (typeof minutes !== "number" || !Number.isInteger(minutes) || minutes < 0 || minutes > 5256000) throw new EngineError("Test.advance_minutes needs a whole number of minutes from 0 to 5256000 (10 years)");
    this.clock = new Date(this.clock.getTime() + minutes * 60000); return isoDateTime(this.clock);
  }
  simulate(fault) {
    if (!(fault in this.faults)) throw new EngineError(`unknown failure ${fault} - see the list at the top of this file`);
    this.activeFaults.add(fault); return true;
  }
  teardown(key) {
    if (key === null || key === undefined) return;
    for (const [entity, rows] of Object.entries(this.store)) {
      const k = this.key(entity);
      const i = rows.findIndex((r) => String(r[k]) === String(key) && this.created.has(`${entity}\u0000${r[k]}`));
      if (i >= 0) { rows.splice(i, 1); return; }
    }
  }
}
