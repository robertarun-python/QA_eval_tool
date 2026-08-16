"""
SQLAlchemy engine/session setup.

This is boilerplate you'll see in almost every FastAPI+SQLAlchemy
project: an `engine` (the actual DB connection), a `SessionLocal`
factory (one session per request), a `Base` class every model inherits
from, and a `get_db` dependency FastAPI injects into route functions.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

_SQLITE_PREFIX = "sqlite:///"

# check_same_thread=False is only needed for SQLite (it's normally
# picky about being used from one thread) - harmless with Postgres.
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session, always closes it after."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def sqlite_db_path() -> str:
    """The raw filesystem path behind DATABASE_URL, for the one-off
    migration scripts (see migrate_round3_freeform.py) that need to run
    hand-written ALTER TABLE statements via the stdlib sqlite3 module
    rather than through SQLAlchemy. Shared here instead of copy-pasted
    into every migration script."""
    if not settings.database_url.startswith(_SQLITE_PREFIX):
        raise RuntimeError(
            f"This migration is SQLite-specific (raw ALTER TABLE via sqlite3); "
            f"DATABASE_URL is {settings.database_url!r}, not a sqlite:/// URL."
        )
    return settings.database_url[len(_SQLITE_PREFIX):]
