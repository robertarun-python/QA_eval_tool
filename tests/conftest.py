"""
Shared pytest fixtures. Uses an in-memory SQLite DB per test run so
tests never touch your real qa_eval.db and can run in any order.
"""
import os
import sys
from pathlib import Path

# Before anything imports the app: point its own engine at a throwaway
# in-memory database, overriding .env. Importing app.main runs
# create_all() on that engine, and anything that reaches the module-level
# SessionLocal without the `client` fixture's patch would otherwise read
# and write the real qa_eval.db. With this it gets an empty database and
# fails loudly instead. tests/test_db_isolation.py keeps it that way.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
# Same idea for the AI: tests fake it, so they must never reach the paid API.
# With no key a stray real call fails loudly instead of spending; only the
# live replay tests (RUN_LLM_REPLAY=1, a person's decision) keep the key.
# Tool-use output stays at its code default (off) whatever .env says - the
# tests pin the behaviour they check.
os.environ["LLM_TOOL_OUTPUT"] = "false"
if os.environ.get("RUN_LLM_REPLAY") != "1":
    os.environ["ANTHROPIC_API_KEY"] = ""

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import httpx
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
        # Fix for a bug found in the 2026-09-12 engineering review: httpx's
        # default Client._merge_cookies() MERGES an explicit per-request
        # `cookies=` argument on top of this client's own persistent jar,
        # rather than having it win outright. A cookie passed via `cookies=`
        # gets domain="" (see httpx.Cookies.set()'s default), while a cookie
        # this jar already holds from an earlier login's real Set-Cookie
        # response is domain-matched to the real test host - so httpx's own
        # domain-matching silently prefers the jar's cookie over our
        # explicit override once more than one identity has ever logged in
        # against this shared client. That directly broke `_auth(token)`
        # (below) any time a test logged in as a second user and then tried
        # to act as the first one again - e.g. test_bulk_upload.py's
        # re-upload tests, which were silently authenticating as the wrong
        # role and failing with a confusing TypeError/KeyError instead of a
        # clean, correct response. Overriding _merge_cookies here to fully
        # REPLACE (not merge) the client's cookies whenever a caller passes
        # `cookies=` restores the "this call acts as exactly this identity"
        # guarantee `_auth`/`_login` were always meant to provide, without
        # touching any of the ~200 call sites that already pass
        # `cookies=_auth(token)`. A call that passes no `cookies=` at all
        # (e.g. test_auth.py's implicit-jar logout test) is unaffected - this
        # only changes behavior in the explicit-cookies branch.
        _original_merge_cookies = c._merge_cookies

        def _replace_instead_of_merge_cookies(cookies=None):
            if cookies:
                return httpx.Cookies(cookies)
            return _original_merge_cookies(cookies)

        c._merge_cookies = _replace_instead_of_merge_cookies

        yield c
    app.dependency_overrides.clear()


# ---- Shared test helpers (used by test_round1.py, test_round2.py, test_round4.py) ----

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

FAKE_ROUND3_CODING_REFERENCE = {
    "test_cases": [{"input": "2 3", "expected_output": "5", "description": "basic sum"}],
    "expected_approach": "Read two integers and add them directly.",
    "reference_solution": "a, b = input().split(',')\nprint(int(a) + int(b))",
}

# Slot-keyed. Since the 2<->4 renumbering the debugging round sits at
# slot 4 (its generator function keeps its historical round2 name - see
# scoring_service._SCORERS); slot 2 is the automation round, which has no
# test-case reference to generate and uses _publish_round4_scenario instead.
_REFERENCE_GENERATOR_BY_ROUND = {
    1: "generate_round1_reference",
    3: "generate_round3_reference",
    4: "generate_round2_reference",
}

_FAKE_REFERENCE_BY_ROUND = {
    1: lambda: list(FAKE_REFERENCE),
    3: lambda: dict(FAKE_ROUND3_CODING_REFERENCE),
    4: lambda: list(FAKE_REFERENCE),
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
    monkeypatch.setattr(llm_service, generator_name, lambda **kwargs: _FAKE_REFERENCE_BY_ROUND[round_number]())

    scenario = client.post(
        "/hr/scenarios",
        json={"round_number": round_number, "title": title, "description": "desc", "experience_band": band, "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    ).json()
    client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))
    return scenario


def _publish_round4_scenario(client, hr_token, monkeypatch, band="0-7", title="Automation challenge"):
    """Round 4 has no test-case reference to generate, but it does
    auto-generate a Test Environment reference sheet and reference UI
    screens at creation time (see hr.py's _generate_reference) - mock
    both the same way _publish_scenario mocks the round 1/2 reference
    generators."""
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "generate_round4_environment", lambda **kwargs: dict(FAKE_ENVIRONMENT))
    monkeypatch.setattr(llm_service, "generate_round4_ui_mockup", lambda **kwargs: dict(FAKE_UI_MOCKUP))

    scenario = client.post(
        "/hr/scenarios",
        json={"round_number": 2, "title": title, "description": "Automate a subset of your round 1 test cases.", "experience_band": band, "time_limit_minutes": 30,
              "config_json": {"mode": "ai_test_automation", "environment_code_by_language": {"python": "# env\n"}}},
        cookies=_auth(hr_token),
    ).json()
    # The automation reference is system-authored (seed_round2_automation.py),
    # not generated - set it directly, as that seed does.
    import app.database as database_module
    from app.models import Scenario
    db = database_module.SessionLocal()
    db.get(Scenario, scenario["id"]).reference_json = {"ground_truth": "g", "validation_notes": "v"}
    db.commit()
    db.close()
    client.post(f"/hr/scenarios/{scenario['id']}/publish", cookies=_auth(hr_token))
    return scenario


def _complete_rounds_1_through_3(client, hr_token, cand_token, monkeypatch, band="0-7", email=None, seed_upto=2):
    """ROUND_SEQUENCE is (1, 2, 3, 4) - the last round isn't reachable at
    all until a candidate has actually submitted rounds 1-3, so any test
    that needs a candidate sitting at the final round has to get them
    there first.

    Since the 2<->4 renumbering the order is: round 1 (manual design),
    round 2 (AI-assisted automation), round 3 (coding). Round 2 is seeded
    rather than driven end-to-end - its real flow needs a test selection,
    AI turns and an execution, none of which any caller of this helper is
    actually testing (see test_round2_automation.py for that round's own
    coverage)."""
    from app.services import llm_service, execution_service

    _publish_scenario(client, hr_token, monkeypatch, round_number=1, band=band)
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, band=band, title="Debug scenario")
    _publish_scenario(client, hr_token, monkeypatch, round_number=3, band=band, title="Coding challenge")
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {
        "coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok",
    })
    monkeypatch.setattr(llm_service, "score_round2_submission", lambda **kwargs: {
        "coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok",
    })
    monkeypatch.setattr(llm_service, "round3_coding_turn", lambda **kwargs: {
        "response_kind": "code_edit", "response_message": "ok", "code_after": "print(1)",
    })
    monkeypatch.setattr(llm_service, "score_round3_coding", lambda **kwargs: {
        "correctness_score": 100, "precision_score": 100, "efficiency_score": 100,
        "independent_judgment_score": 100, "final_score": 100,
        "misses": [], "guardrail_violations": [], "feedback_text": "ok",
    })
    monkeypatch.setattr(execution_service, "run_code", lambda **kwargs: execution_service.ExecutionResult(
        stdout="", stderr="", exit_code=0, timed_out=False, infra_error=False, duration_ms=1,
    ))

    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "Login works", "steps": "...", "expected_result": "..."}]},
        cookies=_auth(cand_token),
    )
    # seed_upto=1 leaves round 2 untouched, for a caller that wants to
    # START the automation round itself (it only needs round 1 behind it).
    _seed_completed_rounds(email or CANDIDATE1_EMAIL, seed_upto)
    if seed_upto >= 2:
        client.post("/candidate/round/3/start", json={"language": "python"}, cookies=_auth(cand_token))
        client.post("/candidate/round/3/turn", json={"candidate_prompt": "solve it"}, cookies=_auth(cand_token))
        client.post("/candidate/round/3/submit", cookies=_auth(cand_token))


def _seed_completed_rounds(candidate_email, upto, scenario_id=None):
    """Marks rounds 1..upto as already submitted for a candidate, directly
    in the DB.

    Needed since the 2<->4 renumbering moved the debugging round to slot 4,
    which `_require_round_unlocked` only unlocks after rounds 1-3 are done.
    Driving the full automation + coding flows inside a debugging test just
    to satisfy gating would make those tests about everything except
    debugging, so the prerequisites are seeded instead - the gating rule
    itself is covered directly by test_round_gating_after_swap.py.
    """
    import app.database as database_module
    from app.models import Submission, Scenario, User, RoundStatus
    from datetime import datetime

    db = database_module.SessionLocal()
    try:
        user = db.query(User).filter(User.email == candidate_email).first()
        for round_number in range(1, upto + 1):
            existing = (
                db.query(Submission)
                .filter(
                    Submission.user_id == user.id,
                    Submission.round_number == round_number,
                    Submission.archived.is_(False),
                )
                .first()
            )
            if existing is not None:
                existing.status = RoundStatus.submitted
                existing.submitted_at = existing.submitted_at or datetime.utcnow()
                continue
            scenario = (
                db.query(Scenario).filter(Scenario.round_number == round_number).first()
                or db.query(Scenario).filter(Scenario.id == scenario_id).first()
                or db.query(Scenario).first()
            )
            db.add(Submission(
                user_id=user.id, scenario_id=scenario.id, round_number=round_number,
                status=RoundStatus.submitted, started_at=datetime.utcnow(),
                submitted_at=datetime.utcnow(),
            ))
        db.commit()
    finally:
        db.close()


def pytest_collection_modifyitems(session, config, items):
    """Browser tests (tests/e2e) always run last: Playwright keeps an event
    loop running for the rest of the session once it starts, and the
    execution tests that use asyncio.run() fail if they come after it."""
    items.sort(key=lambda item: "tests/e2e/" in str(item.path).replace("\\", "/"))
