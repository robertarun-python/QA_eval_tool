"""
execution_service.py wraps calls to a hosted code-execution API (Piston) -
see docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md's
"Execution model". These tests monkeypatch _execute (the one function
that actually makes a network call, same isolation pattern llm_service.py
uses for _call_claude) so no real HTTP call ever happens in the suite.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import httpx
import pytest

from app.services import execution_service


def test_run_code_returns_stdout_on_success(monkeypatch):
    monkeypatch.setattr(execution_service, "_execute", lambda payload: {
        "run": {"stdout": "5\n", "stderr": "", "code": 0, "signal": None, "wall_time": 120},
    })
    result = execution_service.run_code(language="python", code="print(2 + 3)", stdin=[])
    assert result.stdout == "5\n"
    assert result.stderr == ""
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.infra_error is False
    assert result.duration_ms == 120


def test_run_code_passes_stdin_joined_by_newlines(monkeypatch):
    captured = {}

    def fake_execute(payload):
        captured["payload"] = payload
        return {"run": {"stdout": "5\n", "stderr": "", "code": 0, "signal": None, "wall_time": 50}}

    monkeypatch.setattr(execution_service, "_execute", fake_execute)
    execution_service.run_code(language="python", code="a=int(input());b=int(input());print(a+b)", stdin=["2", "3"])
    assert captured["payload"]["stdin"] == "2\n3"
    assert captured["payload"]["language"] == "python"


def test_run_code_detects_timeout(monkeypatch):
    monkeypatch.setattr(execution_service, "_execute", lambda payload: {
        "run": {"stdout": "", "stderr": "", "code": None, "signal": "SIGKILL", "wall_time": 10000},
    })
    result = execution_service.run_code(language="python", code="while True: pass", stdin=[])
    assert result.timed_out is True
    assert result.infra_error is False


def test_run_code_reports_infra_error_on_network_failure(monkeypatch):
    def boom(payload):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(execution_service, "_execute", boom)
    result = execution_service.run_code(language="python", code="print(1)", stdin=[])
    assert result.infra_error is True
    assert result.exit_code is None
    assert result.timed_out is False


def test_run_code_rejects_unsupported_language():
    with pytest.raises(ValueError):
        execution_service.run_code(language="ruby", code="puts 1", stdin=[])
