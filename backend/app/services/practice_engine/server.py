"""
The practice app as a real application: web pages, a JSON API and an SQLite
database, all served from one engine running the scenario description. A
candidate's automation reaches it the way it would reach any real app -
Selenium through a browser, HTTP calls to the API, SQL on the database - and
never sees the engine, the description or the rules.

    python -m app.services.practice_engine.server --spec spec.json --db app.db --port 0

prints "READY <port>" once it is listening. One server per Run: every Run
starts from the description's starting data.

Pages (plain HTML forms, stable ids for locators):
    GET  /                          -> the user's current page
    GET  /page/<page-slug>          open a page (sign-in required unless public)
    POST /ui/login, /ui/logout
    POST /ui/action/<action_name>   a page's form; redirects to the page shown next
    GET  /ui/query/<query_name>     a page's search / lookup form; shows the results
API (JSON; "Authorization: Bearer <token>" from /api/login):
    POST /api/login   {"<login field>": ..., "<password field>": ...} -> {"token", "name"}
    POST /api/logout
    POST /api/<action-name>   body = the action's inputs -> 200/201 {"message", ...} or {"error"}
    GET  /api/<query-name>?<inputs> -> 200 {"results": [...]} or {"record": {...}}
Test controls (for timing and failure tests):
    POST /test/reset, POST /test/advance-minutes {"minutes"}, POST /test/simulate {"failure"},
    GET /test/outbox, GET /test/clock
Browser (Selenium): <app>/wd/hub is this Run's door to the Selenium Grid -
    every browser a test opens goes through it, so exactly this Run's
    browsers are closed when the Run ends (never another Run's).
Database: one table per record type (snake_case), one column per field. It
is read before and written after every request, so a test may also set up
data by writing to it directly.
"""
import argparse
import html
import json
import re
import secrets
import signal
import sqlite3
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

from .render import snake
from .runtime import Engine, _Refused

# Browser options a test may ask for; everything else is dropped (see Handler._safe_new_session).
_SAFE_CHROME_ARG = re.compile(r"--(headless(=new)?|window-size=\d{2,5},\d{2,5}|start-maximized|disable-gpu|incognito|lang=[A-Za-z-]{2,10})")
# Raw browser (CDP / BiDi) and Grid admin commands - never through the practice environment.
_BLOCKED_WD_SEGMENTS = {"goog", "se", "chromium", "moz", "ms", "cdp", "bidi", "grid"}

CLIENT_STATE = ("user", "session", "page", "message", "last", "last_active")
SQL_TYPES = {"int": "INTEGER", "number": "REAL", "money": "REAL", "bool": "INTEGER"}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")


def table(entity: str) -> str:
    return snake(entity)


def label(name: str) -> str:
    text = str(name).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


class PracticeApp:
    """The engine plus per-client state (a browser, or an API token), and the
    database it is kept in step with."""

    def __init__(self, spec: dict, db_path: str):
        self.spec, self.db_path = spec, db_path
        self.engine = Engine(spec)
        self.clients: dict[str, dict] = {}
        self.tokens: dict[str, str] = {}  # API token -> client id
        self.results: dict[str, dict] = {}  # client id -> the last query shown on its page
        users = spec.get("users") or {}
        self.login_field = users.get("login_field", "email")
        self.password_field = users.get("password_field", "password")
        self.write_db()

    # ---- database ------------------------------------------------------------------------------
    def write_db(self) -> None:
        con = sqlite3.connect(self.db_path)
        try:
            with con:
                for entity, e in (self.spec.get("entities") or {}).items():
                    fields = list((e.get("fields") or {}).items())
                    key = e.get("key", "id")
                    cols = ", ".join(f'"{f}" {SQL_TYPES.get(kind, "TEXT")}' + (" PRIMARY KEY" if f == key else "") for f, kind in fields)
                    con.execute(f'DROP TABLE IF EXISTS "{table(entity)}"')
                    con.execute(f'CREATE TABLE "{table(entity)}" ({cols})')
                    for row in self.engine.store[entity]:
                        values = [int(row[f]) if kind == "bool" and row.get(f) is not None else row.get(f) for f, kind in fields]
                        con.execute(f'INSERT INTO "{table(entity)}" VALUES ({", ".join("?" * len(fields))})', values)
        finally:
            con.close()

    def read_db(self) -> None:
        """Whatever a test wrote to the database directly becomes the app's data."""
        con = sqlite3.connect(self.db_path)
        try:
            for entity, e in (self.spec.get("entities") or {}).items():
                fields = e.get("fields") or {}
                try:
                    cur = con.execute(f'SELECT * FROM "{table(entity)}"')
                except sqlite3.Error:
                    self.engine.store[entity] = []
                    continue
                names = [d[0] for d in cur.description]
                rows = []
                for values in cur.fetchall():
                    row = {f: None for f in fields}
                    for n, v in zip(names, values):
                        if n in fields:
                            row[n] = bool(v) if fields[n] == "bool" and v is not None else v
                    rows.append(row)
                self.engine.store[entity] = rows
        finally:
            con.close()

    # ---- clients -------------------------------------------------------------------------------
    def _fresh_state(self) -> dict:
        e = self.engine
        page = e._login_page() if e.users else (self.spec.get("home_page") or (self.spec.get("pages") or ["Home"])[0])
        return {"user": None, "session": {}, "page": page, "message": "", "last": {}, "last_active": None}

    def enter(self, client: str) -> None:
        """Loads the database and this client's state into the engine."""
        self.read_db()
        state = self.clients.setdefault(client, self._fresh_state())
        for k in CLIENT_STATE:
            setattr(self.engine, k, state[k])
        if state["user"] is not None:  # the signed-in user is their record in the data just read
            entity = self.engine.users["entity"]
            self.engine.user = next((r for r in self.engine.store[entity] if r.get(self.login_field) == state["user"]), None)

    def leave(self, client: str) -> None:
        e = self.engine
        self.clients[client] = {k: getattr(e, k) for k in CLIENT_STATE}
        self.clients[client]["user"] = None if e.user is None else e.user.get(self.login_field)
        self.write_db()

    def reset(self) -> None:
        self.engine.reset()
        self.clients.clear()
        self.tokens.clear()
        self.results.clear()
        self.write_db()


    # ---- pages ---------------------------------------------------------------------------------
    def document(self, title: str, content: str) -> str:
        app_name = html.escape(self.spec.get("app_name", "Practice app"))
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)} - {app_name}</title>'
                '<style>body{font-family:sans-serif;margin:24px;max-width:900px}form{margin:12px 0;padding:12px;border:1px solid #ccc}'
                'label{display:block;margin-top:6px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}'
                '#message{font-weight:bold}</style></head>'
                f'<body><header><strong id="app-name">{app_name}</strong></header>{content}</body></html>')

    def form(self, form_id: str, action: str, method: str, fields: list[tuple[str, str]], button_id: str, button: str) -> str:
        inputs = "".join(f'<label for="{html.escape(n)}">{html.escape(lbl)}</label><input id="{html.escape(n)}" name="{html.escape(n)}"'
                         + (' type="password"' if n == self.password_field else "") + ">" for n, lbl in fields)
        return (f'<form id="{form_id}" method="{method}" action="{action}">{inputs}'
                f'<button id="{button_id}" type="submit">{html.escape(button)}</button></form>')

    def render_page(self, client: str) -> str:
        app, e = self, self.engine
        page = e.page
        parts = [f'<h1 id="page-title">{html.escape(page)}</h1>']
        if e.user is not None:
            name = e.user.get((e.users or {}).get("name_field", app.login_field))
            parts.append(f'<p>Signed in as <span id="signed-in-user">{html.escape(str(name))}</span></p>'
                         '<form id="logout-form" method="post" action="/ui/logout"><button id="logout" type="submit">Log out</button></form>')
        public = set(app.spec.get("public_pages") or [])
        links = [p for p in app.spec.get("pages") or [] if e.user is not None or p in public or p == e._login_page()]
        parts.append("<nav>" + " | ".join(f'<a id="nav-{slug(p)}" href="/page/{slug(p)}">{html.escape(p)}</a>' for p in links) + "</nav>")
        parts.append(f'<p id="message" role="alert">{html.escape(e.message or "")}</p>')
        if e.users and page == e._login_page():
            parts.append(self.form("login-form", "/ui/login", "post", [(app.login_field, label(app.login_field)),
                                                                       (app.password_field, label(app.password_field))], "login", "Log in"))
        for q in app.spec.get("queries") or []:
            if q.get("page") == page:
                params = [q["match"]["input"]] if q.get("match") else ([q["key_input"]] if q.get("key_input") else [])
                params += [i["name"] for i in q.get("inputs") or [] if i["name"] not in params]
                parts.append(self.form(f"{slug(q['name'])}-form", f"/ui/query/{q['name']}", "get", [(p, label(p)) for p in params],
                                        slug(q["name"]), q.get("label") or label(q["name"])))
        for a in app.spec.get("actions") or []:
            if a.get("page") == page:
                fields = [(i["name"], label(i["name"])) for i in a.get("inputs") or []]
                parts.append(self.form(f"{slug(a['name'])}-form", f"/ui/action/{a['name']}", "post", fields, slug(a["name"]),
                                        a.get("label") or label(a["name"])))
        if e.last:
            parts.append('<dl id="result-details">' + "".join(f'<dt>{html.escape(label(k))}</dt><dd id="result-{slug(k)}">{html.escape(_text(v))}</dd>'
                                                             for k, v in e.last.items()) + "</dl>")
        shown = app.results.get(client)
        if shown and shown["result"] is not None:
            result = shown["result"]
            if isinstance(result, dict):
                parts.append('<dl id="details">' + "".join(f'<dt>{html.escape(label(k))}</dt><dd id="detail-{slug(k)}">{html.escape(_text(v))}</dd>'
                                                           for k, v in result.items()) + "</dl>")
            elif result:
                cols = list(result[0].keys())
                head = "".join(f"<th>{html.escape(label(c))}</th>" for c in cols)
                rows = "".join("<tr>" + "".join(f'<td class="col-{slug(c)}">{html.escape(_text(r.get(c)))}</td>' for c in cols) + "</tr>" for r in result)
                parts.append(f'<table id="results"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>')
        return self.document(page, "".join(parts))


def _app_address(spec: dict) -> str:
    """The host (and port) of the address Round 1 shows for the app - spec base_url - or "" if none."""
    m = re.match(r"^https?://([^/?#]+)", str(spec.get("base_url") or "").strip(), re.I)
    return m.group(1).lower() if m else ""


def _status_of_action(action: dict) -> int:
    return 201 if any("create" in e for e in action.get("effects") or []) else 200


class Handler(BaseHTTPRequestHandler):
    """Connections are accepted in parallel (a browser opens spare ones it may
    never use - one waiting on those would stall the real request), but
    requests are handled one at a time: the app is a single state machine."""
    app: PracticeApp
    lock: threading.Lock
    grid: str | None = None
    sessions: set
    server_version = "PracticeApp/1.0"

    def log_message(self, *args):  # quiet: the candidate's run output is what matters
        pass

    # ---- plumbing ------------------------------------------------------------------------------
    def _send(self, status: int, body: str, ctype: str = "application/json", headers: dict | None = None) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False))

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        if "application/json" in (self.headers.get("Content-Type") or ""):
            try:
                data = json.loads(raw or "{}")
            except ValueError:
                raise _Refused("The request body is not valid JSON", status=400) from None
            if not isinstance(data, dict):
                raise _Refused("The request body must be a JSON object", status=400)
            return data
        return {k: v[-1] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    def _browser(self) -> tuple[str, dict]:
        cookie = self.headers.get("Cookie") or ""
        m = re.search(r"practice_session=([\w-]+)", cookie)
        if m:
            return m.group(1), {}
        client = secrets.token_urlsafe(12)
        return client, {"Set-Cookie": f"practice_session={client}; Path=/; HttpOnly; SameSite=Lax"}

    def _api_client(self) -> str:
        auth = self.headers.get("Authorization") or ""
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return self.app.tokens.get(token) or f"anonymous-{secrets.token_urlsafe(6)}"

    def _route(self, method: str) -> None:
        if self.path.startswith("/wd/hub/") and self.grid:
            return self._relay(method)  # outside the lock: the browser it drives calls this app meanwhile
        with self.lock:
            self._handle(method)

    def _wd_error(self, status: int, message: str) -> None:
        self._json(status, {"value": {"error": "unsupported operation", "message": message}})

    def _safe_new_session(self, body: bytes | None) -> bytes:
        """The browser a Run gets, whatever the test asked for: Chrome, headless,
        and every connection forced through a dead proxy except this Run's
        practice app (loopback included - so not the real server, the Grid or
        the internet). Nothing else the test asked for (binary, extensions,
        prefs, proxy, debugger) is passed on."""
        try:
            asked = json.loads(body or b"{}")
        except ValueError:
            asked = {}
        caps = (asked.get("capabilities") or {}) if isinstance(asked, dict) else {}
        merged = dict(caps.get("alwaysMatch") or {})
        first = caps.get("firstMatch") or [{}]
        if isinstance(first, list) and first and isinstance(first[0], dict):
            merged.update(first[0])
        options = merged.get("goog:chromeOptions") if isinstance(merged.get("goog:chromeOptions"), dict) else {}
        args = [a for a in options.get("args") or [] if isinstance(a, str) and _SAFE_CHROME_ARG.fullmatch(a)]
        port = self.server.server_address[1]
        args += ["--headless=new", "--proxy-server=http://127.0.0.1:9",
                 f"--proxy-bypass-list=<-loopback>;127.0.0.1:{port};localhost:{port}"]
        safe = {"browserName": "chrome", "goog:chromeOptions": {"args": args}}
        for key in ("pageLoadStrategy", "timeouts", "unhandledPromptBehavior"):
            if key in merged:
                safe[key] = merged[key]
        return json.dumps({"capabilities": {"alwaysMatch": safe}}).encode()

    def _relay(self, method: str) -> None:
        """Passes a Selenium command to the Grid - only the standard WebDriver
        commands, only for browsers this Run opened, and "go to URL" only for
        this Run's practice app - and notes the browser sessions opened and
        closed. Chrome runs outside the sandbox, so this is its fence: no
        file:// or chrome:// pages, no other site or port, no raw browser
        (CDP) or Grid admin commands."""
        parts = [p for p in self.path[len("/wd/hub/"):].split("?")[0].split("/") if p]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        if any(p in _BLOCKED_WD_SEGMENTS for p in parts):
            return self._wd_error(403, "This command is not available in the practice environment")
        if parts == ["status"] and method == "GET":
            pass
        elif parts == ["session"] and method == "POST":
            body = self._safe_new_session(body)
        elif len(parts) >= 2 and parts[0] == "session":
            if parts[1] not in self.sessions:
                return self._wd_error(404, "No such browser session in this Run")
            if parts[2:] == ["url"] and method == "POST":
                try:
                    url = str(json.loads(body or b"{}").get("url") or "")
                except (ValueError, AttributeError):
                    url = ""
                origins = {f"http://127.0.0.1:{self.server.server_address[1]}", f"http://localhost:{self.server.server_address[1]}"}
                # The address Round 1 showed (the spec's base_url, e.g. https://loan-emi.example.test) is this
                # Run's practice app: a candidate who used it got a blocked-address exception (owner's Round 2,
                # 2026-09-27). Any page under it is sent to the practice app, http or https.
                named = _app_address(self.app.spec)
                if named and re.match(rf"^https?://{re.escape(named)}(?=$|[/?#])", url, re.I):
                    url = f"http://127.0.0.1:{self.server.server_address[1]}" + re.sub(rf"^https?://{re.escape(named)}", "", url, flags=re.I)
                    body = json.dumps({"url": url}).encode()
                if not any(url == o or url.startswith(o + "/") for o in origins):
                    return self._wd_error(403, "Only the practice application can be opened - use the address in PRACTICE_APP_URL"
                                               + (f" (or {named})" if named else ""))
        else:
            return self._wd_error(403, "This command is not available in the practice environment")
        target = self.grid + "/" + "/".join(parts)
        req = urllib.request.Request(target, data=body, method=method, headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=180) as res:
                status, data = res.status, res.read()
        except urllib.error.HTTPError as err:
            status, data = err.code, err.read()
        except OSError:
            return self._json(502, {"value": {"error": "unknown error", "message": "The browser service is not available"}})
        if method == "POST" and parts == ["session"] and status == 200:
            try:
                self.sessions.add(json.loads(data)["value"]["sessionId"])
            except (ValueError, KeyError, TypeError):
                pass
        elif method == "DELETE" and len(parts) == 2 and parts[0] == "session":
            self.sessions.discard(parts[1])
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method: str) -> None:
        path, _, query = self.path.partition("?")
        # PRACTICE_API_URL already ends in "api/", and the Reference lists "/api/login": joined, that's
        # ".../api//api/login" - a trap for the assistant's code, not a test idea (simulated API tester,
        # 2026-09-28: 8 turns stuck on a 404). Doubled slashes and a doubled "api/" mean the same path.
        path = re.sub(r"/{2,}", "/", path)
        while path.startswith("/api/api/"):
            path = path[4:]
        params = {k: v[-1] for k, v in urllib.parse.parse_qs(query, keep_blank_values=True).items()}
        try:
            if path.startswith("/api/"):
                return self._api(method, path[5:], params)
            if path.startswith("/test/"):
                return self._test(method, path[6:])
            return self._ui(method, path, params)
        except _Refused as refused:
            self._json(refused.status, {"error": refused.message})
        except Exception:  # never a stack trace to the candidate's test
            self._json(500, {"error": "Something went wrong. Please check your input and try again."})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    # ---- API -----------------------------------------------------------------------------------
    def _api(self, method: str, name: str, params: dict) -> None:
        app, e = self.app, self.app.engine
        name = name.strip("/")
        actions = {slug(a["name"]): a for a in app.spec.get("actions") or []}
        queries = {slug(q["name"]): q for q in app.spec.get("queries") or []}
        if name == "login" and e.users:
            if method != "POST":
                return self._json(405, {"error": "Use POST"})
            body = self._body()
            client = f"api-{secrets.token_urlsafe(8)}"
            app.enter(client)
            try:
                who = e.login(body.get(app.login_field), body.get(app.password_field))
            except _Refused as refused:
                app.leave(client)
                return self._json(refused.status, {"error": refused.message})
            app.leave(client)
            token = secrets.token_urlsafe(16)
            app.tokens[token] = client
            return self._json(200, {"token": token, "name": who})
        if name == "logout" and e.users:
            client = self._api_client()
            app.enter(client)
            e.user, e.session = None, {}
            app.leave(client)
            return self._json(200, {"message": "Signed out"})
        if name in actions:
            if method != "POST":
                return self._json(405, {"error": "Use POST"})
            action = actions[name]
            body = self._body()
            client = self._api_client()
            app.enter(client)
            try:
                message, returns, _ = e.run_action(action["name"], body)
            except _Refused as refused:
                app.leave(client)
                return self._json(refused.status, {"error": refused.message})
            app.leave(client)
            return self._json(_status_of_action(action), dict({"message": message}, **returns))
        if name in queries:
            if method != "GET":
                return self._json(405, {"error": "Use GET"})
            query = queries[name]
            client = self._api_client()
            app.enter(client)
            try:
                result, none_message = e.run_query(query["name"], params)
            except _Refused as refused:
                app.leave(client)
                return self._json(refused.status, {"error": refused.message})
            app.leave(client)
            if query.get("key_input"):
                return self._json(200, {"record": result})
            return self._json(200, {"results": result} | ({"message": none_message} if none_message else {}))
        return self._json(404, {"error": "Not found"})

    # ---- test controls -------------------------------------------------------------------------
    def _test(self, method: str, name: str) -> None:
        app, e = self.app, self.app.engine
        name = name.strip("/")
        if name == "reset" and method == "POST":
            app.reset()
            return self._json(200, {"message": "Reset to the starting data"})
        if name == "advance-minutes" and method == "POST":
            minutes = self._body().get("minutes")
            app.read_db()
            try:
                now = e.advance_minutes(minutes)
            except Exception as err:
                return self._json(400, {"error": str(err)})
            app.write_db()
            return self._json(200, {"now": now})
        if name == "simulate" and method == "POST":
            failure = self._body().get("failure")
            if failure not in e.faults:
                return self._json(400, {"error": f"Unknown failure {failure!r}"})
            e.simulate(failure)
            return self._json(200, {"message": f"{failure} is on"})
        if name == "outbox" and method == "GET":
            return self._json(200, {"messages": [dict(m) for m in e.outbox]})
        if name == "clock" and method == "GET":
            return self._json(200, {"now": e.ev({"now": True}, {"inputs": {}, "aliases": {}})})
        return self._json(404, {"error": "Not found"})

    # ---- pages ---------------------------------------------------------------------------------
    def _ui(self, method: str, path: str, params: dict) -> None:
        app, e = self.app, self.app.engine
        client, cookie = self._browser()
        pages = {slug(p): p for p in app.spec.get("pages") or []}
        app.enter(client)
        if method == "GET" and path in ("/", ""):
            pass
        elif method == "GET" and path.startswith("/page/"):
            page = pages.get(path[6:].strip("/"))
            if page is None:
                app.leave(client)
                return self._send(404, self.app.document("Page not found", '<p id="message">Page not found</p>'), "text/html", cookie)
            if not (params.get("shown") and page == e.page):  # "shown": the page a form just led to - keep its message
                e.ui_open(page)
                e.last = {}
                app.results.pop(client, None)
        elif method == "POST" and path == "/ui/login":
            form = self._body()
            e.ui_login(form.get(app.login_field), form.get(app.password_field))
            app.results.pop(client, None)
        elif method == "POST" and path == "/ui/logout":
            e.ui_logout()
            app.results.pop(client, None)
        elif method == "POST" and path.startswith("/ui/action/"):
            name = path[len("/ui/action/"):].strip("/")
            if name not in e.actions:
                app.leave(client)
                return self._send(404, self.app.document("Not found", '<p id="message">Not found</p>'), "text/html", cookie)
            form = self._body()
            e.last = {}
            e.ui_action(name, {k: v for k, v in form.items()})
            app.results.pop(client, None)
        elif method == "GET" and path.startswith("/ui/query/"):
            name = path[len("/ui/query/"):].strip("/")
            if name not in e.queries:
                app.leave(client)
                return self._send(404, self.app.document("Not found", '<p id="message">Not found</p>'), "text/html", cookie)
            result = e.ui_query(name, params)
            app.results[client] = {"query": name, "result": result}
        else:
            app.leave(client)
            return self._send(404, self.app.document("Not found", '<p id="message">Not found</p>'), "text/html", cookie)
        # After a form: show the resulting page at its own address (refresh-safe) - a lookup form (GET) too:
        # it used to stay on /ui/query/..., which isn't any page the Reference lists, so a candidate checking
        # the address after "View EMI Details" was told they weren't on the EMI Details page (2026-09-28).
        if method == "POST" or path.startswith("/ui/query/"):
            app.leave(client)
            return self._send(303, "", "text/html", {**cookie, "Location": f"/page/{slug(e.page)}?shown=1"})
        body = self.app.render_page(client)
        app.leave(client)
        self._send(200, body, "text/html", cookie)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def close_browsers(server: HTTPServer) -> None:
    """Closes every browser this Run opened and didn't close itself."""
    handler = server.RequestHandlerClass
    for session in list(handler.sessions):
        try:
            urllib.request.urlopen(urllib.request.Request(f"{handler.grid}/session/{session}", method="DELETE"), timeout=10)
        except OSError:
            pass
        handler.sessions.discard(session)


def serve(spec: dict, db_path: str, port: int = 0, host: str = "127.0.0.1", grid: str | None = None) -> HTTPServer:
    """A server ready to serve_forever(); its port is server.server_address[1]."""
    handler = type("PracticeHandler", (Handler,), {"app": PracticeApp(spec, db_path), "lock": threading.Lock(),
                                                   "grid": grid.rstrip("/") if grid else None, "sessions": set()})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--grid", default=None)
    args = parser.parse_args(argv)
    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)
    server = serve(spec, args.db, args.port, grid=args.grid)

    def stop(*_):  # the Run is over: close this Run's browsers, then exit
        close_browsers(server)
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    print(f"READY {server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        stop()


if __name__ == "__main__":
    sys.exit(main())
