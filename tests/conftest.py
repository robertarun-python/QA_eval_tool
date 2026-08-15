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
