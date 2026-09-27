"""
The reference panel candidates see in Round 2 - what a tester joining a real
project would be given: how to reach the application, each page's source (to
find locators themselves), the API reference, the database tables with their
starting data, the test accounts and the test controls.

Built from the scenario description by code, never by the AI. It never
contains the app's rules or the messages it shows: those are what a
candidate's own Round 1 test cases say to expect, and what their automation
must find out by running.
"""
import copy
import json
import re
import tempfile
from pathlib import Path

from .render import action_params, query_params
from .server import SQL_TYPES, PracticeApp, slug, table

CONNECT = [
    {"name": "PRACTICE_APP_URL", "meaning": "The application's web address (open it in the browser)."},
    {"name": "PRACTICE_API_URL", "meaning": "The API's base address (PRACTICE_APP_URL + \"api/\")."},
    {"name": "PRACTICE_DB", "meaning": "Path of the application's SQLite database file."},
    {"name": "SELENIUM_GRID_URL", "meaning": "The Selenium Grid - create a RemoteWebDriver with this address (Chrome)."},
]
API_ERRORS = 'An error comes back as {"error": "<message>"} with a 4xx or 5xx status code.'
TEST_CONTROLS = [
    {"method": "POST", "path": "/test/reset", "body": [], "meaning": "Back to the starting data."},
    {"method": "POST", "path": "/test/advance-minutes", "body": ["minutes"], "meaning": "Move the application's clock forward."},
    {"method": "POST", "path": "/test/simulate", "body": ["failure"], "meaning": "Switch on a simulated failure (see the list)."},
    {"method": "GET", "path": "/test/outbox", "body": [], "meaning": "Emails, SMS and alerts the application has sent."},
    {"method": "GET", "path": "/test/clock", "body": [], "meaning": "The application's current date and time."},
]


def _has_create(action: dict) -> bool:
    return any("create" in e for e in action.get("effects") or [])


def _shown_fields(spec: dict, query: dict) -> list[str]:
    if query.get("show"):
        return [f["name"] if isinstance(f, dict) else f for f in query["show"]]
    return list(((spec.get("entities") or {}).get(query["entity"]) or {}).get("fields") or {})


def api(spec: dict) -> list[dict]:
    users = spec.get("users") or {}
    rows = []
    if users:
        login, password = users.get("login_field", "email"), users.get("password_field", "password")
        rows.append({"method": "POST", "path": "/api/login", "body": [login, password], "query": [], "success": 200,
                     "returns": ["token", "name"], "sign_in": False})
        rows.append({"method": "POST", "path": "/api/logout", "body": [], "query": [], "success": 200, "returns": ["message"],
                     "sign_in": True})
    for q in spec.get("queries") or []:
        rows.append({"method": "GET", "path": f"/api/{slug(q['name'])}", "body": [], "query": query_params(q), "success": 200,
                     "returns": ["record"] if q.get("key_input") else ["results"], "fields": _shown_fields(spec, q),
                     "sign_in": q.get("requires_login", bool(users))})
    for a in spec.get("actions") or []:
        rows.append({"method": "POST", "path": f"/api/{slug(a['name'])}", "body": action_params(a), "query": [],
                     "success": 201 if _has_create(a) else 200, "returns": ["message", *(a.get("returns") or {})],
                     "sign_in": a.get("requires_login", bool(users))})
    return rows


def database(spec: dict) -> list[dict]:
    out = []
    for entity, e in (spec.get("entities") or {}).items():
        fields = e.get("fields") or {}
        out.append({"table": table(entity), "key": e.get("key", "id"),
                    "columns": [{"name": f, "type": SQL_TYPES.get(kind, "TEXT")} for f, kind in fields.items()],
                    "rows": copy.deepcopy((spec.get("data") or {}).get(entity) or [])})
    return out


def accounts(spec: dict) -> list[dict]:
    users = spec.get("users")
    if not users:
        return []
    rows = (spec.get("data") or {}).get(users["entity"]) or []
    return [{"login": r.get(users.get("login_field", "email")), "password": r.get(users.get("password_field", "password")),
             "name": r.get(users.get("name_field")) if users.get("name_field") else None} for r in rows]


def _sample_result(app, page: str):
    """What the page shows the test user - their loans, their payments, one
    record's details - so a screen reads like the real app, not an empty form
    (owner, 2026-09-27: "account number, EMI amount missing"). A lookup is tried
    with the starting records' own ids (the test user's loan, their account...)
    for the page it belongs to or the page it opens. Only the starting data
    (already on the Database tab); a refused lookup shows nothing, so no message
    of the app's - the answer key - ever appears."""
    e = app.engine
    ids = [r.get(e._key(ent)) for ent, rows in e.store.items() for r in (rows or [])[:5]]
    queries = [q for q in app.spec.get("queries") or [] if q.get("next_page") == page] + \
              [q for q in app.spec.get("queries") or [] if q.get("page") == page and q.get("next_page") != page]
    for q in queries:
        params = [q["match"]["input"]] if q.get("match") else ([q["key_input"]] if q.get("key_input") else [])
        params += [i["name"] for i in q.get("inputs") or [] if i["name"] not in params]
        if q.get("match") and (q["match"].get("blank") or "all") != "all":
            continue  # a search needs a term - there's no neutral one to show
        # A lookup reading a choice made earlier in the session ("the selected loan") is
        # shown as if the user had chosen one of their own records.
        session_keys = sorted(set(re.findall(r'"session":\s*"([^"]+)"', json.dumps(q))))
        for value in ([None] if not params and not session_keys else ids):
            inputs = {p: "" for p in params}
            if value is not None and params:
                inputs[params[0]] = value
            saved = dict(e.session)
            e.session.update({k: value for k in session_keys})
            try:
                result, _ = e.run_query(q["name"], inputs)
            except Exception:  # noqa: BLE001 - refused or unusable: show nothing
                continue
            finally:
                e.session.clear()
                e.session.update(saved)
            if result:
                return {"query": q["name"], "result": result}
    return None


def page_sources(spec: dict) -> list[dict]:
    """Each page's HTML as the application serves it (to a signed-in user,
    except the sign-in page and public pages) - with no message showing, and
    the page's first lookup showing the test user's own data (_sample_result)."""
    with tempfile.TemporaryDirectory(prefix="practice_reference_") as tmp:
        app = PracticeApp(spec, str(Path(tmp) / "reference.db"))
        e = app.engine
        signed_in = accounts(spec)[:1]
        public = set(spec.get("public_pages") or [])
        out = []
        for page in spec.get("pages") or []:
            app.enter("reference")
            e.user = None
            if signed_in and page != (e._login_page() if e.users else None) and page not in public:
                who = signed_in[0]["login"]
                e.user = next((r for r in e.store[e.users["entity"]] if r.get(app.login_field) == who), None)
            e.page, e.message, e.last = page, "", {}
            sample = _sample_result(app, page) if e.user is not None or page in public else None
            app.results.pop("reference", None)
            if sample:
                app.results["reference"] = sample
            e.page, e.message, e.last = page, "", {}
            out.append({"name": page, "path": f"/page/{slug(page)}", "source": app.render_page("reference")})
            app.results.pop("reference", None)
            app.leave("reference")
        return out


# What Round 1 may see of the app: its structure - screens, API, database and starting data -
# so candidates can write concrete steps and test data (owner, 2026-09-27: "how will they know
# the API details or the EMI screens?"). Left out: the test controls and simulated failures,
# which would hand them their negative test ideas. The panel never lists the app's messages
# or rules - the Round 1 answer key.
ROUND1_HIDDEN = ("test_controls", "failures", "connect")  # connect: how a test Run finds the app - Round 1 runs nothing


_PANELS: dict = {}


def current_panel(config: dict | None) -> dict | None:
    """A Round 2 scenario's reference panel, drawn from its description with
    today's code - not the copy saved when it was approved, so an improvement
    (the screens showing the test user's data, 2026-09-27) reaches every live
    scenario without approving it again. Kept per description; None for an
    older practice app."""
    spec = (config or {}).get("practice_spec")
    if not isinstance(spec, dict):
        return None
    key = json.dumps(spec, sort_keys=True)
    if key not in _PANELS:
        try:
            _PANELS[key] = reference_panel(spec)
        except Exception:  # noqa: BLE001 - a description today's code can't draw: the saved copy
            saved = (config or {}).get("reference_panel")
            return saved if isinstance(saved, dict) else None
    return _PANELS[key]


def round1_panel(panel: dict | None) -> dict | None:
    return {k: v for k, v in panel.items() if k not in ROUND1_HIDDEN} if isinstance(panel, dict) else None


def reference_panel(spec: dict) -> dict:
    return {
        "app_name": spec.get("app_name"),
        "connect": CONNECT,
        "accounts": accounts(spec),
        "pages": page_sources(spec),
        "api": api(spec),
        "api_sign_in": 'Sign in with POST /api/login; send the token it returns as "Authorization: Bearer <token>".' if spec.get("users") else "",
        "api_errors": API_ERRORS,
        "database": database(spec),
        "test_controls": TEST_CONTROLS,
        "failures": [f["name"] for f in spec.get("faults") or []],
    }
