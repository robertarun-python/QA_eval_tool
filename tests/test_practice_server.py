"""
The practice app as a real application (practice_engine.server): pages with
forms, a JSON API with real status codes, an SQLite database in step with
the app, and test controls - driven over HTTP the way a candidate's
automation drives it. No AI calls.
"""
import http.cookiejar
import json
import re
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from app.services.practice_engine import server

SPEC = json.loads((Path(__file__).parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())


@pytest.fixture
def app(tmp_path):
    db = tmp_path / "practice.db"
    srv = server.serve(SPEC, str(db))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", db
    srv.shutdown()
    srv.server_close()


def call(base, method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def sql(db, query, *args):
    con = sqlite3.connect(db)
    try:
        with con:
            return con.execute(query, args).fetchall()
    finally:
        con.close()


class Browser:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def get(self, path):
        with self.opener.open(self.base + path) as res:
            return res.read().decode()

    def post(self, path, form):
        with self.opener.open(self.base + path, urllib.parse.urlencode(form).encode()) as res:  # follows the redirect
            return res.read().decode()


def text_of(page, element_id):
    m = re.search(rf'id="{element_id}"[^>]*>([^<]*)<', page)
    return m.group(1) if m else None


def login(base, email="testuser@library.test", password="Test@123"):
    status, body = call(base, "POST", "/api/login", {"email": email, "password": password})
    assert status == 200, body
    return body["token"]


# ---- API --------------------------------------------------------------------------------------
def test_api_login_gives_a_token_and_real_status_codes(app):
    base, _ = app
    status, body = call(base, "POST", "/api/login", {"email": "testuser@library.test", "password": "Test@123"})
    assert status == 200 and body["name"] == "Test User" and body["token"]
    assert call(base, "POST", "/api/login", {"email": "testuser@library.test", "password": "wrong"}) == (401, {"error": "Invalid email or password"})
    assert call(base, "POST", "/api/login", {"email": "blocked@library.test", "password": "Block@123"})[0] == 403
    assert call(base, "POST", "/api/login", {"email": "", "password": "x"})[0] == 400
    assert call(base, "GET", "/api/search-books?term=gatsby")[0] == 401  # no token


def test_api_search_borrow_and_the_database_agree(app):
    base, db = app
    token = login(base)
    status, body = call(base, "GET", "/api/search-books?term=fitzgerald", token=token)
    assert status == 200 and [b["id"] for b in body["results"]] == ["BK-001", "BK-006"]
    assert call(base, "GET", "/api/search-books?term=zzz", token=token) == (200, {"results": [], "message": "No books found"})
    status, body = call(base, "POST", "/api/borrow-book", {"book_id": "BK-001"}, token=token)
    assert status == 201 and body["loan_id"] == "LN-006" and body["message"].startswith("Book borrowed successfully")
    assert sql(db, 'SELECT available_copies FROM "book" WHERE id = ?', "BK-001") == [(2,)]
    assert sql(db, 'SELECT member, status FROM "loan" WHERE id = ?', "LN-006") == [("testuser@library.test", "Borrowed")]
    status, body = call(base, "POST", "/api/borrow-book", {"book_id": "BK-002"}, token=token)
    assert (status, body) == (400, {"error": "No copies available"})
    assert call(base, "POST", "/api/borrow-book", {"book_id": "BK-999"}, token=token) == (404, {"error": "Book not found"})
    assert call(base, "GET", "/api/borrow-book", token=token)[0] == 405
    assert call(base, "GET", "/api/open-book?book_id=BK-003", token=token)[1]["record"]["title"]


def test_a_record_a_test_writes_to_the_database_becomes_the_apps_data(app):
    base, db = app
    token = login(base)
    sql(db, 'UPDATE "book" SET available_copies = 0 WHERE id = ?', "BK-001")
    assert call(base, "POST", "/api/borrow-book", {"book_id": "BK-001"}, token=token) == (400, {"error": "No copies available"})
    sql(db, 'INSERT INTO "loan" (id, member, book, title, status) VALUES (?, ?, ?, ?, ?)', "LN-006", "x@y.z", "BK-003", "1984", "Borrowed")
    status, body = call(base, "POST", "/api/borrow-book", {"book_id": "BK-003"}, token=token)
    assert status == 201 and body["loan_id"] == "LN-007"  # never an id already in the database


def test_bad_requests_get_plain_errors_not_crashes(app):
    base, _ = app
    req = urllib.request.Request(base + "/api/login", data=b"{not json", method="POST", headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req)
    assert err.value.code == 400 and json.loads(err.value.read()) == {"error": "The request body is not valid JSON"}
    assert call(base, "GET", "/api/nothing-here")[0] == 404
    token = login(base)
    assert call(base, "POST", "/api/borrow-book", {"book_id": None}, token=token)[0] == 400


def test_test_controls_clock_failures_outbox_and_reset(app):
    base, db = app
    token = login(base)
    assert call(base, "POST", "/test/simulate", {"failure": "network_down"})[0] == 200
    assert call(base, "POST", "/api/borrow-book", {"book_id": "BK-001"}, token=token) == (503, {"error": "Network error. Please try again."})
    assert call(base, "POST", "/test/simulate", {"failure": "nope"})[0] == 400
    call(base, "POST", "/test/reset")
    token = login(base)
    assert call(base, "POST", "/api/borrow-book", {"book_id": "BK-001"}, token=token)[0] == 201
    assert call(base, "GET", "/test/outbox")[1]["messages"][0]["to"] == "testuser@library.test"
    assert call(base, "POST", "/test/advance-minutes", {"minutes": 31}) == (200, {"now": "2024-02-10T10:31"})
    assert call(base, "GET", "/api/my-loans", token=token) == (401, {"error": "Your session has expired. Please log in again."})
    assert call(base, "POST", "/test/advance-minutes", {"minutes": "abc"})[0] == 400
    call(base, "POST", "/test/reset")
    assert sql(db, 'SELECT count(*) FROM "loan"') == [(5,)]


# ---- pages ------------------------------------------------------------------------------------
def test_pages_login_search_borrow_and_messages_like_a_browser(app):
    base, db = app
    b = Browser(base)
    page = b.get("/")
    assert text_of(page, "page-title") == "Login" and 'id="email"' in page and 'id="login"' in page
    page = b.post("/ui/login", {"email": "testuser@library.test", "password": "wrong"})
    assert text_of(page, "message") == "Invalid email or password"
    page = b.post("/ui/login", {"email": "testuser@library.test", "password": "Test@123"})
    assert text_of(page, "page-title") == "Home" and text_of(page, "signed-in-user") == "Test User"
    page = b.get("/page/search")
    page = b.get("/ui/query/search_books?term=gatsby")
    assert '<table id="results">' in page and "The Great Gatsby" in page
    page = b.get("/ui/query/open_book?book_id=BK-001")
    assert text_of(page, "page-title") == "Book Details" and 'id="borrow-book"' in page
    page = b.post("/ui/action/borrow_book", {"book_id": "BK-001"})
    assert text_of(page, "page-title") == "Borrow Confirmation"
    assert text_of(page, "message") == "Book borrowed successfully. Due date: 24-Feb-2024"
    assert text_of(page, "result-loan-id") == "LN-006"
    assert sql(db, 'SELECT available_copies FROM "book" WHERE id = ?', "BK-001") == [(2,)]


def test_pages_need_sign_in_and_each_browser_has_its_own_session(app):
    base, _ = app
    anonymous = Browser(base)
    page = anonymous.get("/page/search")
    assert text_of(page, "page-title") == "Login" and text_of(page, "message") == "Please log in first"
    signed_in = Browser(base)
    signed_in.post("/ui/login", {"email": "testuser@library.test", "password": "Test@123"})
    assert text_of(signed_in.get("/page/my-books"), "page-title") == "My Books"
    assert text_of(anonymous.get("/page/my-books"), "page-title") == "Login"
    with pytest.raises(urllib.error.HTTPError) as err:
        anonymous.get("/page/nowhere")
    assert err.value.code == 404 and "Page not found" in err.value.read().decode()


def test_after_a_lookup_form_the_address_is_the_pages_own(app):
    """A simulated candidate (2026-09-28) checked the address after "View EMI Details" against the page
    listed in the Reference and got /ui/query/... - every form now lands on its page's own address, with
    the results still shown."""
    base, _ = app
    b = Browser(base)
    b.post("/ui/login", {"email": "testuser@library.test", "password": "Test@123"})
    b.get("/page/search")                               # the search form is on the Search page
    with b.opener.open(base + "/ui/query/search_books?term=gatsby") as res:
        page, address = res.read().decode(), res.geturl()
    assert address.endswith("/page/search?shown=1") and '<table id="results">' in page and "The Great Gatsby" in page
    with b.opener.open(base + "/ui/query/open_book?book_id=BK-001") as res:
        page, address = res.read().decode(), res.geturl()
    assert "/page/book-details?shown=1" in address and text_of(page, "page-title") == "Book Details"
    page = b.get("/page/search")                        # opening the page again starts it fresh
    assert "The Great Gatsby" not in page
