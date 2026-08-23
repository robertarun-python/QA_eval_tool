"""
Executes candidate code locally via subprocess, one interpreter/compiler
invocation per language - see docs/superpowers/specs/2026-08-23-round3-ai-
coding-design.md's "Execution model" section for the original design (that
version used the hosted Piston API; this replaced it - see the 2026-08-23
"switch execution to local subprocess" note below). Round 3 (AI-prompted
coding) is the only round that runs real code; centralizing this the same
way llm_service.py centralizes the Claude API means there's one place to
change the execution approach later.

Batch stdin only, not a live terminal: the candidate supplies every input
value upfront (see run_code's `stdin` argument), the process runs
start-to-finish in one call, and the full output is returned afterward.

Switched from the hosted Piston API to local subprocess execution on
2026-08-23: Piston's public API went whitelist-only in Feb 2026 (confirmed
via a direct 401: "Public Piston API is now whitelist only"), and this
deployment has no Docker available to self-host it. Running candidate code
as a plain local subprocess instead needs no external service, but comes
with a real trade-off worth being explicit about: there is NO sandbox here
beyond a wall-clock timeout (see settings.execution_timeout_seconds) - no
network isolation, no filesystem restriction, no memory/CPU cap. That's an
acceptable trust boundary for this tool's actual shape (a single HR user
running it locally, scoring code the LLM wrote in response to a candidate's
own natural-language prompts - not arbitrary third-party or multi-tenant
code), but it is NOT a hardened sandbox and must not be treated as one if
this tool's deployment model ever changes (e.g. a hosted multi-tenant
version) - that would need real isolation (containers, a VM, gVisor, ...)
before running LLM-generated code at all.
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..config import settings

# javac requires the public class name to match the filename exactly -
# this fixes the class name the round 3 turn-generation prompt should
# have the assistant use for Java (see round3_coding_turn.txt), matching
# the filename convention the old Piston integration already used.
_SOURCE_FILENAME = {
    "python": "main.py",
    "java": "Main.java",
    "javascript": "main.js",
}


class ExecutionResult:
    def __init__(self, stdout: str, stderr: str, exit_code: int | None, timed_out: bool, infra_error: bool, duration_ms: int | None = None):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.infra_error = infra_error
        self.duration_ms = duration_ms


def _run_subprocess(cmd: list[str], cwd: Path, stdin_text: str, timeout_seconds: int) -> tuple[str, str, int | None, bool]:
    """Runs one subprocess to completion, feeding it stdin_text and
    capturing everything - the one place that actually shells out, same
    isolation pattern llm_service.py uses for _call_claude (tests
    monkeypatch this, never subprocess.run itself). Returns
    (stdout, stderr, exit_code, timed_out) - a timeout is reported this
    way rather than raised, since it's an expected outcome (a candidate's
    infinite loop), not an infrastructure failure."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, input=stdin_text, capture_output=True, text=True,
            timeout=timeout_seconds,
        )
        return proc.stdout, proc.stderr, proc.returncode, False
    except subprocess.TimeoutExpired as e:
        # e.stdout/e.stderr are bytes-or-None here even with text=True
        # (a subprocess.run quirk on timeout) - decode defensively.
        stdout = e.stdout.decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return stdout, stderr, None, True


def run_code(language: str, code: str, stdin: list[str]) -> ExecutionResult:
    """Runs `code` once, feeding `stdin` (one value per line, in order)
    to the process's standard input - a single batch call, not a live
    interactive session (see this module's docstring)."""
    filename = _SOURCE_FILENAME.get(language)
    if filename is None:
        raise ValueError(f"Unsupported language: {language}")

    stdin_text = "\n".join(stdin)
    timeout_seconds = settings.execution_timeout_seconds

    try:
        with tempfile.TemporaryDirectory(prefix="qa_eval_round3_") as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / filename
            source.write_text(code, encoding="utf-8")

            if language == "python":
                interpreter = sys.executable
                run_cmd = [interpreter, str(source)]
            elif language == "javascript":
                node = shutil.which("node")
                if node is None:
                    return ExecutionResult(stdout="", stderr="", exit_code=None, timed_out=False, infra_error=True)
                run_cmd = [node, str(source)]
            else:  # java
                javac = shutil.which("javac")
                java = shutil.which("java")
                if javac is None or java is None:
                    return ExecutionResult(stdout="", stderr="", exit_code=None, timed_out=False, infra_error=True)
                compile_proc = subprocess.run(
                    [javac, source.name], cwd=tmp_path, capture_output=True, text=True,
                    timeout=timeout_seconds,
                )
                if compile_proc.returncode != 0:
                    # Same "[compile]" prefix convention the old Piston
                    # integration used, so HR/candidates keep seeing a
                    # compile failure clearly distinguished from a runtime
                    # one - a real candidate-code fault, not infra_error.
                    compile_stderr = compile_proc.stderr.strip()
                    stderr = f"[compile] {compile_stderr}" if compile_stderr else "[compile] compilation failed"
                    return ExecutionResult(stdout="", stderr=stderr, exit_code=compile_proc.returncode, timed_out=False, infra_error=False)
                run_cmd = [java, "-cp", str(tmp_path), "Main"]

            start = time.monotonic()
            stdout, stderr, exit_code, timed_out = _run_subprocess(run_cmd, tmp_path, stdin_text, timeout_seconds)
            duration_ms = int((time.monotonic() - start) * 1000)
    # Covers a missing interpreter, a permissions failure, or any other
    # environment-level surprise launching the subprocess - infra_error,
    # not a candidate-code fault, same as the old integration's API-failure
    # handling.
    except (OSError, subprocess.SubprocessError):
        return ExecutionResult(stdout="", stderr="", exit_code=None, timed_out=False, infra_error=True)

    return ExecutionResult(
        stdout=stdout, stderr=stderr, exit_code=exit_code, timed_out=timed_out,
        infra_error=False, duration_ms=duration_ms,
    )
