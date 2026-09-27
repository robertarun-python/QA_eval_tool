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


def page_sources(spec: dict) -> list[dict]:
    """Each page's HTML as the application serves it (to a signed-in user,
    except the sign-in page and public pages) - with no message showing and
    no results yet."""
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
            out.append({"name": page, "path": f"/page/{slug(page)}", "source": app.render_page("reference")})
            app.leave("reference")
        return out


# What Round 1 may see of the app: its structure - screens, API, database and starting data -
# so candidates can write concrete steps and test data (owner, 2026-09-27: "how will they know
# the API details or the EMI screens?"). Left out: the test controls and simulated failures,
# which would hand them their negative test ideas. The panel never lists the app's messages
# or rules - the Round 1 answer key.
ROUND1_HIDDEN = ("test_controls", "failures", "connect")  # connect: how a test Run finds the app - Round 1 runs nothing


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
