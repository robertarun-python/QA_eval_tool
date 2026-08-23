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


def test_run_code_surfaces_compile_stage_stderr_on_compile_failure(monkeypatch):
    """Fix 4 (final whole-branch review): Piston returns compile
    diagnostics in a separate `compile` stage (present for compiled
    languages like Java) - on a compile failure, `run` just shows a
    generic runtime symptom while the real compiler error sits in
    `compile`. Confirm it's folded into the returned stderr."""
    monkeypatch.setattr(execution_service, "_execute", lambda payload: {
        "compile": {"stdout": "", "stderr": "Main.java:3: error: ';' expected", "code": 1, "signal": None},
        "run": {"stdout": "", "stderr": "Error: Could not find or load main class Main", "code": 1, "signal": None, "wall_time": 30},
    })
    result = execution_service.run_code(language="java", code="broken", stdin=[])
    assert "[compile]" in result.stderr
    assert "';' expected" in result.stderr
    # The run stage's own stderr isn't discarded either.
    assert "Could not find or load main class" in result.stderr
    assert result.infra_error is False


def test_run_code_does_not_add_compile_prefix_on_successful_compile(monkeypatch):
    monkeypatch.setattr(execution_service, "_execute", lambda payload: {
        "compile": {"stdout": "", "stderr": "", "code": 0, "signal": None},
        "run": {"stdout": "hello\n", "stderr": "", "code": 0, "signal": None, "wall_time": 30},
    })
    result = execution_service.run_code(language="java", code="ok", stdin=[])
    assert result.stdout == "hello\n"
    assert result.stderr == ""


def test_run_code_reports_infra_error_on_malformed_response_shape(monkeypatch):
    """Fix 8: data.get("run", {}) would raise AttributeError uncaught if
    `data` isn't a dict (e.g. Piston returns something unexpected) -
    confirm that maps to infra_error=True like every other API-failure
    mode, rather than propagating."""
    monkeypatch.setattr(execution_service, "_execute", lambda payload: None)
    result = execution_service.run_code(language="python", code="print(1)", stdin=[])
    assert result.infra_error is True
    assert result.exit_code is None
