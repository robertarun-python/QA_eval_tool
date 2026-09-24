"""
The test suite must never open the real database or spend on the real AI (backend/qa_eval.db) -
not through the app's own engine, not through settings. conftest.py
points DATABASE_URL at an in-memory database before the app is imported;
these fail if that ever stops being true.
"""
from pathlib import Path

import app.database as database_module
from app.config import settings

REAL_DB = (Path(__file__).resolve().parent.parent / "backend" / "qa_eval.db").resolve()


def test_settings_point_at_a_throwaway_database():
    assert ":memory:" in settings.database_url
    assert str(REAL_DB) not in settings.database_url


def test_the_apps_own_engine_is_not_the_real_database():
    url = database_module.engine.url
    assert url.database in (None, "", ":memory:"), url


def test_normal_runs_have_no_api_key_and_tool_output_pinned_off():
    import os
    if os.environ.get("RUN_LLM_REPLAY") != "1":
        assert settings.anthropic_api_key == ""
    assert settings.llm_tool_output is False
