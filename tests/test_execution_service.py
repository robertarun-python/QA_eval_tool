"""
execution_service.py runs candidate code as a local subprocess (Python
interpreter, node, or javac+java) - see that module's docstring for why
this replaced the earlier hosted-Piston-API approach. These tests
monkeypatch _run_subprocess (the one function that actually shells out,
same isolation pattern llm_service.py uses for _call_claude) so no real
process ever gets spawned in the suite, except in the two "real
interpreter" tests at the bottom, which intentionally exercise the actual
Python subprocess path end-to-end since Python is guaranteed present in
this test environment.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest

from app.services import execution_service


def test_run_code_returns_stdout_on_success(monkeypatch):
    monkeypatch.setattr(execution_service, "_run_subprocess", lambda cmd, cwd, stdin_text, timeout_seconds: ("5\n", "", 0, False))
    result = execution_service.run_code(language="python", code="print(2 + 3)", stdin=[])
    assert result.stdout == "5\n"
    assert result.stderr == ""
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.infra_error is False
    assert result.duration_ms is not None


def test_run_code_passes_stdin_joined_by_newlines(monkeypatch):
    captured = {}

    def fake_run_subprocess(cmd, cwd, stdin_text, timeout_seconds):
        captured["stdin_text"] = stdin_text
        captured["cmd"] = cmd
        return "5\n", "", 0, False

    monkeypatch.setattr(execution_service, "_run_subprocess", fake_run_subprocess)
    execution_service.run_code(language="python", code="a=int(input());b=int(input());print(a+b)", stdin=["2", "3"])
    assert captured["stdin_text"] == "2\n3"
    assert captured["cmd"][0] == sys.executable


def test_run_code_detects_timeout(monkeypatch):
    monkeypatch.setattr(execution_service, "_run_subprocess", lambda cmd, cwd, stdin_text, timeout_seconds: ("", "", None, True))
    result = execution_service.run_code(language="python", code="while True: pass", stdin=[])
    assert result.timed_out is True
    assert result.infra_error is False


def test_run_code_reports_infra_error_when_subprocess_launch_fails(monkeypatch):
    def boom(cmd, cwd, stdin_text, timeout_seconds):
        raise OSError("no such interpreter")

    monkeypatch.setattr(execution_service, "_run_subprocess", boom)
    result = execution_service.run_code(language="python", code="print(1)", stdin=[])
    assert result.infra_error is True
    assert result.exit_code is None
    assert result.timed_out is False


def test_run_code_rejects_unsupported_language():
    with pytest.raises(ValueError):
        execution_service.run_code(language="ruby", code="puts 1", stdin=[])


def test_run_code_reports_infra_error_when_node_is_missing(monkeypatch):
    monkeypatch.setattr(execution_service.shutil, "which", lambda name: None)
    result = execution_service.run_code(language="javascript", code="console.log(1)", stdin=[])
    assert result.infra_error is True


def test_run_code_reports_infra_error_when_java_toolchain_is_missing(monkeypatch):
    monkeypatch.setattr(execution_service.shutil, "which", lambda name: None)
    result = execution_service.run_code(language="java", code="broken", stdin=[])
    assert result.infra_error is True


def test_run_code_surfaces_java_compile_failure_as_compile_stderr(monkeypatch):
    monkeypatch.setattr(execution_service.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_compile_run(cmd, cwd, capture_output, text, timeout):
        class FakeProc:
            returncode = 1
            stderr = "Main.java:3: error: ';' expected"
            stdout = ""
        return FakeProc()

    monkeypatch.setattr(execution_service.subprocess, "run", fake_compile_run)
    result = execution_service.run_code(language="java", code="broken", stdin=[])
    assert "[compile]" in result.stderr
    assert "';' expected" in result.stderr
    assert result.infra_error is False
    assert result.exit_code == 1


def test_run_code_does_not_reach_run_stage_on_compile_failure(monkeypatch):
    """A compile failure must short-circuit before _run_subprocess (the
    `run` stage) is ever called - there's no binary to run."""
    monkeypatch.setattr(execution_service.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_compile_run(cmd, cwd, capture_output, text, timeout):
        class FakeProc:
            returncode = 1
            stderr = "error"
            stdout = ""
        return FakeProc()

    monkeypatch.setattr(execution_service.subprocess, "run", fake_compile_run)

    def fail_if_called(cmd, cwd, stdin_text, timeout_seconds):
        raise AssertionError("run stage should not be reached after a compile failure")

    monkeypatch.setattr(execution_service, "_run_subprocess", fail_if_called)
    execution_service.run_code(language="java", code="broken", stdin=[])


# ---- Real subprocess execution (Python only - guaranteed present here) ----

def test_run_code_actually_executes_python_end_to_end():
    result = execution_service.run_code(language="python", code="a=int(input());b=int(input());print(a+b)", stdin=["2", "3"])
    assert result.stdout == "5\n"
    assert result.exit_code == 0
    assert result.infra_error is False
    assert result.timed_out is False


def test_run_code_actually_times_out_python_infinite_loop(monkeypatch):
    monkeypatch.setattr(execution_service.settings, "execution_timeout_seconds", 1)
    result = execution_service.run_code(language="python", code="while True: pass", stdin=[])
    assert result.timed_out is True
    assert result.infra_error is False
