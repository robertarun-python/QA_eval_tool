"""
Shared pytest fixtures. Uses an in-memory SQLite DB per test run so
tests never touch your real qa_eval.db and can run in any order.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.seed import seed_users
from app.config import settings

# Re-exported so test files can log in as the seeded accounts without
# hardcoding credentials that live in .env.
HR_EMAIL, HR_PASSWORD = settings.hr_email, settings.hr_password
CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD = settings.candidate1_email, settings.candidate1_password
CANDIDATE2_EMAIL, CANDIDATE2_PASSWORD = settings.candidate2_email, settings.candidate2_password
CANDIDATE3_EMAIL, CANDIDATE3_PASSWORD = settings.candidate3_email, settings.candidate3_password


@pytest.fixture()
def client(monkeypatch):
    # StaticPool is required here: SQLite's ":memory:" DB normally lives
    # only as long as the single connection that created it - without
    # StaticPool, SQLAlchemy hands out a *new* connection (and therefore
    # a blank, table-less database) per session, which is why the first
    # query from inside a request would fail with "no such table".
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    seed_db = TestingSessionLocal()
    seed_users(seed_db)
    seed_db.close()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # candidate.py's background scoring task opens its own session
    # directly from app.database.SessionLocal (it can't use the get_db
    # dependency - there's no request in flight by the time it runs).
    # Point that at the test engine too, or scored submissions would
    # silently be written to your real qa_eval.db instead of showing up
    # in test assertions.
    import app.database as database_module
    monkeypatch.setattr(database_module, "SessionLocal", TestingSessionLocal)

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---- Shared test helpers (used by test_round1.py, test_round2.py, test_round3.py) ----

FAKE_REFERENCE = [
    {"title": "ref row", "preconditions": "", "steps": "...", "expected_result": "...", "priority": "High", "type": "Positive"},
]

FAKE_ENVIRONMENT = {
    "fields": {"Test account email": "qa.tester@example.com", "Test account password": "Passw0rd!", "API base URL": "https://api.example.test/v1"},
    "notes": "Fictional test environment for this scenario.",
}

FAKE_UI_MOCKUP = {
    "screens": [
        {"name": "Login", "elements": [
            {"type": "label", "text": "Email"},
            {"type": "input", "text": "Email"},
            {"type": "button", "text": "Login"},
            {"type": "link", "text": "Log In"},
        ]},
    ],
}

_REFERENCE_GENERATOR_BY_ROUND = {
    1: "generate_round1_reference",
    2: "generate_round2_reference",
}


def _login(client, email, password):
    # The token now travels only as an httpOnly Set-Cookie header (see
    # routers/auth.py), not in the JSON body - res.cookies reads what
    # that specific response set, independent of the shared TestClient's
    # persistent cookie jar (which would only ever hold ONE session at a
    # time, whichever login happened most recently - useless for tests
    # that log in as HR and a candidate in the same test and need both
    # tokens usable afterward).
    res = client.post("/auth/login", json={"identifier": email, "password": password})
    return res.cookies["qa_eval_token"]


def _auth(token):
    # Passed as `cookies=` (not `headers=`) at every call site - httpx
    # merges an explicit per-request `cookies=` on top of the client's
    # jar for that one request, so this reliably acts as "this specific
    # user" even when the shared client's jar holds a different, more
    # recently-logged-in user's cookie.
    return {"qa_eval_token": token}


def _publish_scenario(client, hr_token, monkeypatch, round_number=1, band="0-7", title="Login form"):
    from app.services import llm_service
    generator_name = _REFERENCE_GENERATOR_BY_ROUND[round_number]
    monkeypatch.setattr(llm_service, generator_name, lambda **kwargs: list(FAKE_REFERENCE))

    scenario = client.post(
        "/hr/scenarios",
        json={"round_number": round_number, "title": title, "description": "desc", "experience_band": band, "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    ).json()
    client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))
    return scenario


def _publish_round3_scenario(client, hr_token, monkeypatch, band="0-7", title="Automation challenge"):
    """Round 3 has no test-case reference to generate, but it does
    auto-generate a Test Environment reference sheet and reference UI
    screens at creation time (see hr.py's _generate_reference) - mock
    both the same way _publish_scenario mocks the round 1/2 reference
    generators."""
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round3_environment", lambda **kwargs: dict(FAKE_ENVIRONMENT))
    monkeypatch.setattr(llm_service, "generate_round3_ui_mockup", lambda **kwargs: dict(FAKE_UI_MOCKUP))

    scenario = client.post(
        "/hr/scenarios",
        json={"round_number": 3, "title": title, "description": "Automate a subset of your round 1 test cases.", "experience_band": band, "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    ).json()
    client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))
    return scenario


def _create_round3_test_case(client, token, title=None):
    return client.post(
        "/candidate/round/3/test-case",
        json={"title": title},
        cookies=_auth(token),
    ).json()
