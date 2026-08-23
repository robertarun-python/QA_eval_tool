"""
Executes candidate code via a hosted code-execution API (Piston) - see
docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md's "Execution
model" section. Round 3 (AI-prompted coding) is the only round that runs
real code; centralizing this the same way llm_service.py centralizes the
Claude API means there's one place to change providers/timeouts later.

Batch stdin only, not a live terminal: the candidate supplies every input
value upfront (see run_code's `stdin` argument), the process runs
start-to-finish in one call, and the full output is returned afterward.
"""
import httpx

from ..config import settings

# Piston requires an exact runtime version per language, not "latest" -
# see https://github.com/engineer-man/piston#executing-code. One place
# to bump these if the public instance's supported versions change.
# Verified 2026-08-23 against the live GET /runtimes response on
# https://emkc.org/api/v2/piston/runtimes - these three are still
# current (python 3.10.0, java 15.0.2, javascript/node 18.15.0).
_LANGUAGE_RUNTIME = {
    "python": {"language": "python", "version": "3.10.0", "filename": "main.py"},
    "java": {"language": "java", "version": "15.0.2", "filename": "Main.java"},
    "javascript": {"language": "javascript", "version": "18.15.0", "filename": "main.js"},
}


class ExecutionResult:
    def __init__(self, stdout: str, stderr: str, exit_code: int | None, timed_out: bool, infra_error: bool, duration_ms: int | None = None):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.infra_error = infra_error
        self.duration_ms = duration_ms


def _execute(payload: dict) -> dict:
    """The one function that actually calls the hosted execution API -
    isolated so tests can monkeypatch just this, the same pattern
    llm_service.py uses for _call_claude."""
    response = httpx.post(
        f"{settings.piston_api_url}/execute", json=payload,
        timeout=settings.execution_timeout_seconds + 5,  # a little slack over Piston's own run_timeout below
    )
    response.raise_for_status()
    return response.json()


def run_code(language: str, code: str, stdin: list[str]) -> ExecutionResult:
    """Runs `code` once, feeding `stdin` (one value per line, in order)
    to the process's standard input - a single batch call, not a live
    interactive session (see this module's docstring)."""
    runtime = _LANGUAGE_RUNTIME.get(language)
    if runtime is None:
        raise ValueError(f"Unsupported language: {language}")

    payload = {
        "language": runtime["language"],
        "version": runtime["version"],
        "files": [{"name": runtime["filename"], "content": code}],
        "stdin": "\n".join(stdin),
        "run_timeout": settings.execution_timeout_seconds * 1000,
    }
    try:
        data = _execute(payload)

        run = data.get("run", {})
        stderr = run.get("stderr", "")

        # Compiled languages (Java) run a separate `compile` stage before
        # `run` - on a compile failure, `run` just shows a generic symptom
        # (e.g. "could not find or load main class") while the actual
        # compiler error sits unread in `compile`. Fold it into the
        # returned stderr (prefixed so it's distinguishable from a
        # runtime stderr) rather than adding a new ExecutionResult field
        # for it.
        compile_stage = data.get("compile") or {}
        compile_stderr = (compile_stage.get("stderr") or "").strip()
        compile_code = compile_stage.get("code")
        compile_failed = bool(compile_stderr) or (compile_code is not None and compile_code != 0)
        if compile_stage and compile_failed:
            compile_note = f"[compile] {compile_stderr}" if compile_stderr else "[compile] compilation failed"
            stderr = f"{compile_note}\n{stderr}" if stderr else compile_note

        # Piston reports a killed-by-timeout run via signal "SIGKILL"
        # rather than a dedicated boolean field - confirmed 2026-08-23
        # against the Piston API source (api/src/job.js): the isolate
        # sandbox's TO/OL/EL statuses are all normalized to signal:
        # 'SIGKILL' before the response is sent, so this string check is
        # the documented shape.
        timed_out = run.get("signal") == "SIGKILL"
    # AttributeError/TypeError: same defensiveness as the other three -
    # a malformed/unexpected response body (data isn't a dict, or a
    # nested stage isn't) would otherwise raise uncaught out of this
    # function past every other API-failure mode's infra_error=True
    # handling. Not purely theoretical - this plan's own testing found
    # the public Piston endpoint returning 401s in practice.
    except (httpx.HTTPError, ValueError, KeyError, AttributeError, TypeError):
        return ExecutionResult(stdout="", stderr="", exit_code=None, timed_out=False, infra_error=True)

    return ExecutionResult(
        stdout=run.get("stdout", ""),
        stderr=stderr,
        exit_code=run.get("code"),
        timed_out=timed_out,
        infra_error=False,
        duration_ms=run.get("wall_time"),
    )
