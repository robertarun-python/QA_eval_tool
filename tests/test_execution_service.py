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
    # Trailing "\n\n" - see run_code's comment: guarantees a genuinely
    # blank line is always readable past the supplied values, so a
    # candidate's own "read until blank" loop can terminate for real
    # instead of hitting a bare EOFError.
    assert captured["stdin_text"] == "2\n3\n\n"
    assert captured["cmd"][0] == sys.executable


def test_run_code_lets_a_read_until_blank_loop_terminate_cleanly():
    code = (
        "values = []\n"
        "while True:\n"
        "    line = input()\n"
        "    if line == '':\n"
        "        break\n"
        "    values.append(int(line))\n"
        "print(sum(values))\n"
    )
    result = execution_service.run_code(language="python", code=code, stdin=["2", "3"])
    assert result.stdout == "5\n"
    assert result.stderr == ""
    assert result.exit_code == 0


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


# ---- InteractiveSession (the candidate's own Run button - a real, live
# subprocess the caller polls/writes into across several calls, not one
# blocking batch call like run_code above). Real Python subprocesses
# throughout, same reasoning as the batch tests above - this is exactly
# the code path that matters here, not a mock of it.

async def _poll_until_exited(session, max_attempts=50, delay=0.05):
    import asyncio
    for _ in range(max_attempts):
        if session.exited:
            return
        await asyncio.sleep(delay)
    raise AssertionError("session never exited within the polling budget")


def test_interactive_session_waits_for_real_typed_input_before_producing_output():
    """The core behavior this whole feature exists for: the process
    actually blocks on input() until write_input is called - nothing is
    pre-supplied or guessed. Confirms output only appears in response to
    what was actually typed, in the order a real terminal session would
    show it."""
    import asyncio

    async def scenario():
        session = await execution_service.start_interactive(
            language="python",
            code="a = input('enter a: ')\nb = input('enter b: ')\nprint(int(a) + int(b))\n",
        )
        try:
            # Give the process a moment to actually reach and print the
            # first prompt - it must NOT have exited or produced the sum
            # yet, since no input has been sent.
            await asyncio.sleep(0.3)
            assert session.exited is False
            assert "enter a" in session.stdout_buffer
            assert "enter b" not in session.stdout_buffer

            await session.write_input("2")
            await asyncio.sleep(0.3)
            assert "enter b" in session.stdout_buffer
            assert session.exited is False  # still waiting on the second input

            await session.write_input("3")
            await _poll_until_exited(session)
            assert "5" in session.stdout_buffer
            assert session.exit_code == 0
            assert session.stdin_sent == ["2", "3"]
        finally:
            await session.stop()

    asyncio.run(scenario())


def test_interactive_session_rejects_unsupported_language():
    import asyncio

    async def scenario():
        await execution_service.start_interactive(language="ruby", code="puts 1")

    with pytest.raises(ValueError):
        asyncio.run(scenario())


def test_interactive_session_stop_kills_a_still_running_process():
    import asyncio

    async def scenario():
        session = await execution_service.start_interactive(language="python", code="input()\n")
        try:
            await asyncio.sleep(0.2)
            assert session.exited is False
        finally:
            await session.stop()
        assert session.exited is True

    asyncio.run(scenario())
