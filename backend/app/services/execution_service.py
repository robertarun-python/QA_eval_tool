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
with a real trade-off: candidates can type code directly (Round 2 and
Round 3), so everything they run is untrusted. An audit (Sep 2026) showed
unsandboxed candidate code could read .env (API key, login-signing secret),
write the real database and reach the network. Every run is therefore
wrapped by _sandboxed (settings.execution_sandbox): on macOS the built-in
sandbox-exec denies the network, any read under /Users, any write outside
the run's own temp directory and spawning processes; the child also gets a
minimal environment (_child_env). Where no sandbox is available, code is
refused (infra_error) unless execution_sandbox is explicitly "off" - never
silently run unprotected. Not a VM: a hosted multi-tenant deployment still
needs real isolation (containers, gVisor, ...).
"""
import ast
import asyncio
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from ..config import settings


_TOOLCHAIN_CACHE: dict[str, bool] = {}


def toolchain_available(language: str) -> bool:
    """Whether code in this language can actually run on this host. Java
    counts only when `javac -version` succeeds - macOS ships /usr/bin/javac
    as a placeholder that exists even with no JDK installed (see
    _MISSING_JAVA_RUNTIME_MARKERS). Cached per process."""
    if language not in _TOOLCHAIN_CACHE:
        if language == "python":
            ok = True
        elif language == "javascript":
            ok = shutil.which("node") is not None
        elif language == "java":
            javac = shutil.which("javac")
            try:
                ok = bool(javac and shutil.which("java")) and subprocess.run(
                    [javac, "-version"], capture_output=True, text=True, timeout=10,
                ).returncode == 0
            except (OSError, subprocess.SubprocessError):
                ok = False
        else:
            ok = False
        _TOOLCHAIN_CACHE[language] = ok
    return _TOOLCHAIN_CACHE[language]


# javac has no syntax-only mode, so only its genuine parse errors count -
# "cannot find symbol" and similar mean the code parsed fine.
_JAVA_SYNTAX_ERROR_RE = re.compile(
    r"error: (.*expected|illegal start of \w+|not a statement|unclosed .*|reached end of file while parsing"
    r"|'else' without 'if'|orphaned \w+|illegal character.*|class, interface, enum, or record expected)"
)
_PUBLIC_CLASS_NAME_RE = re.compile(r"\bpublic\s+(?:final\s+|abstract\s+)*class\s+([A-Za-z_]\w*)")


def syntax_error(language: str, code: str | None) -> tuple[bool, str | None]:
    """(checked, error): whether the code could be checked for syntax at
    all here, and if so its first syntax error (None = parses cleanly).
    Used to verify the syntax-fix assistant's claims instead of trusting
    them - see llm_service.round3_syntax_fix."""
    code = code or ""
    if language == "python":
        try:
            ast.parse(code)
        except SyntaxError as e:
            return True, f"{e.msg} (line {e.lineno})"
        except ValueError as e:  # e.g. a null byte in the source
            return True, str(e)
        return True, None
    if not toolchain_available(language):
        return False, None
    with tempfile.TemporaryDirectory() as tmp:
        try:
            if language == "javascript":
                source = Path(tmp) / "main.js"
                source.write_text(code, encoding="utf-8")
                proc = subprocess.run([shutil.which("node"), "--check", str(source)], capture_output=True, text=True, timeout=15)
                if proc.returncode == 0:
                    return True, None
                lines = [l.strip() for l in proc.stderr.splitlines() if l.strip()]
                where = next((l for l in lines if "main.js:" in l), "")  # path may be shown via /private on macOS
                message = next((l for l in lines if "Error" in l), lines[-1] if lines else "syntax error")
                line_no = where.rsplit(":", 1)[-1] if ":" in where else ""
                return True, message + (f" (line {line_no})" if line_no.isdigit() else "")
            if language == "java":
                names = _PUBLIC_CLASS_NAME_RE.findall(code)
                source = Path(tmp) / f"{names[0] if len(names) == 1 else 'Main'}.java"
                source.write_text(code, encoding="utf-8")
                proc = subprocess.run([shutil.which("javac"), "-d", tmp, str(source)], capture_output=True, text=True, timeout=30)
                for line in proc.stderr.splitlines():
                    m = _JAVA_SYNTAX_ERROR_RE.search(line)
                    if m:
                        line_no = line.split(":")[1] if line.count(":") >= 2 else ""
                        return True, m.group(1) + (f" (line {line_no})" if line_no.isdigit() else "")
                return True, None
        except (OSError, subprocess.SubprocessError):
            return False, None
    return False, None


def _child_env(cwd: Path | None = None) -> dict:
    """Environment for every candidate process we launch, forcing UTF-8 on
    the CHILD's own stdout/stderr.

    Without this, CPython picks the platform default for its output streams
    - cp1252 on a Windows host - so a program that merely PRINTS a non-ASCII
    character (an AI-generated `print("✓ passed")` is the common case) dies
    with UnicodeEncodeError inside the child, before a single byte reaches
    us. The candidate then sees a failed run for a test whose logic was
    correct. Decoding more carefully on the parent side cannot fix that;
    the child has already crashed, so the encoding has to be set here.

    PYTHONIOENCODING is Python-specific and simply ignored by node/java, so
    one environment is safe for all three languages.

    Minimal on purpose: never the server's own environment, which can hold
    secrets (API keys, tokens) - candidate code must not see them."""
    env = {"PATH": _CHILD_PATH, "LANG": "en_US.UTF-8", "PYTHONIOENCODING": "utf-8"}
    if cwd is not None:
        env.update(HOME=str(cwd), TMPDIR=str(cwd))
    return env


# The base interpreter, not the venv's (which sits under /Users, hidden by the sandbox).
PYTHON = next((str(p) for p in (Path(sys.base_prefix) / "bin" / f"python{sys.version_info[0]}.{sys.version_info[1]}",
                                  Path(sys.base_prefix) / "bin" / "python3") if p.exists()), sys.executable)
_CHILD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
_SANDBOX_EXEC = "/usr/bin/sandbox-exec"


class SandboxUnavailable(OSError):  # an OSError, so both launch sites report it as infra_error
    """No way to isolate candidate code on this host - refused, not run unprotected."""


def _sandbox_profile(workdir: Path) -> str:
    work = str(workdir.resolve()).replace('"', "")
    return f"""(version 1)
(allow default)
(deny network*)
(deny process-fork)
(deny file-read* (subpath "/Users"))
(deny file-write*)
(allow file-write* (subpath "{work}") (literal "/dev/null") (literal "/dev/zero") (literal "/dev/tty"))
(allow file-read* (subpath "{work}"))
"""


def _sandboxed(cmd: list[str], workdir: Path) -> list[str]:
    """`cmd` wrapped so it can't reach secrets, the database, the network or
    other processes - see the module docstring. Raises SandboxUnavailable
    rather than ever returning the bare command, unless the setting is "off"."""
    mode = settings.execution_sandbox
    if mode == "off":
        return cmd
    if sys.platform == "darwin" and Path(_SANDBOX_EXEC).exists():
        return [_SANDBOX_EXEC, "-p", _sandbox_profile(workdir), *cmd]
    raise SandboxUnavailable(f"no sandbox available on {sys.platform} (execution_sandbox={mode!r})")

# What macOS's /usr/bin/javac placeholder prints when no JDK is installed
# (see the java branch of _prepare_run).
_MISSING_JAVA_RUNTIME_MARKERS = ("Unable to locate a Java Runtime",)

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


class _PreparedRun:
    """What run_code and start_interactive both need before they can
    actually launch the candidate's process: either a command to run, or
    the reason they can't (missing interpreter/compiler, or a Java
    compile failure) - each caller maps this onto its own return shape
    (ExecutionResult vs. InteractiveSession)."""

    def __init__(self, run_cmd: list[str] | None = None, infra_error: bool = False, compile_stderr: str | None = None, compile_exit_code: int | None = None):
        self.run_cmd = run_cmd
        self.infra_error = infra_error
        self.compile_stderr = compile_stderr
        self.compile_exit_code = compile_exit_code


def _javac_works(javac: str) -> bool:
    try:
        return subprocess.run([javac, "-version"], capture_output=True, text=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _prepare_run(language: str, source: Path, tmp_path: Path, timeout_seconds: int, *, unbuffered_python: bool) -> _PreparedRun:
    """Resolves `language` to a runnable command, compiling first for
    Java. Shared by run_code (batch) and start_interactive (the
    candidate's Run button) - the only difference between the two is
    unbuffered_python (see start_interactive's -u flag comment)."""
    if language == "python":
        interpreter = [PYTHON, "-I", "-u", str(source)] if unbuffered_python else [PYTHON, "-I", str(source)]
        return _PreparedRun(run_cmd=interpreter)
    elif language == "javascript":
        node = shutil.which("node")
        if node is None:
            return _PreparedRun(infra_error=True)
        return _PreparedRun(run_cmd=[node, str(source)])
    else:  # java
        javac = shutil.which("javac")
        java = shutil.which("java")
        if javac is None or java is None:
            return _PreparedRun(infra_error=True)
        # Deliberately left on the host locale: this captures JAVAC's own
        # diagnostics, not the candidate program's output, so it's outside
        # the UTF-8 defect _child_env exists for (a Java program's real
        # stdout/stderr goes through _run_subprocess below, which is
        # fixed). Changing it here would also break the existing
        # compile-failure tests' subprocess.run stubs for no gain.
        compile_proc = subprocess.run(
            [javac, source.name], cwd=tmp_path, capture_output=True, text=True,
            timeout=timeout_seconds,
        )
        if compile_proc.returncode != 0:
            compile_stderr = compile_proc.stderr.strip()
            # macOS ships /usr/bin/javac and /usr/bin/java as placeholders
            # even with no JDK installed - shutil.which finds them, and
            # "compiling" just prints this and exits 1. That's a missing
            # toolchain on the host (same as which() finding nothing), not
            # the candidate's code - left as a compile failure it was
            # scored as every test case failing.
            # The marker text is only macOS's current wording; a compiler
            # that can't even report its own version is missing whatever
            # it prints, so that is checked too.
            if any(marker in compile_stderr for marker in _MISSING_JAVA_RUNTIME_MARKERS) or not _javac_works(javac):
                return _PreparedRun(infra_error=True)
            # Same "[compile]" prefix convention the old Piston
            # integration used, so HR/candidates keep seeing a compile
            # failure clearly distinguished from a runtime one - a real
            # candidate-code fault, not infra_error.
            stderr = f"[compile] {compile_stderr}" if compile_stderr else "[compile] compilation failed"
            return _PreparedRun(compile_stderr=stderr, compile_exit_code=compile_proc.returncode)
        # -XX:-UsePerfData: the JVM would otherwise write a perf file outside the run's temp directory.
        return _PreparedRun(run_cmd=[java, "-XX:-UsePerfData", "-cp", str(tmp_path), "Main"])


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
            _sandboxed(cmd, cwd), cwd=cwd, input=stdin_text, capture_output=True, text=True,
            # Both halves are needed: env makes the CHILD write UTF-8 (see
            # _child_env), encoding/errors make the PARENT read it back as
            # UTF-8 instead of the host locale, which would otherwise
            # mojibake exactly the characters the child just emitted.
            # errors="replace" keeps a stray undecodable byte from turning
            # a candidate's real run into an infrastructure failure.
            encoding="utf-8", errors="replace", env=_child_env(cwd),
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

    # Trailing "\n\n", not just "\n": a plain join leaves the stream
    # ending exactly at the last real value's own newline, with nothing
    # after it - so a candidate's own "read lines until a blank one"
    # loop (a completely normal way to read a list of unknown length)
    # doesn't get a real blank line to read; it hits a bare EOFError
    # instead, indistinguishable from an infra problem and unrelated to
    # whether their code is actually correct. One extra blank line
    # guarantees a genuinely empty read is always available at the end,
    # so that idiom either breaks cleanly (the common case) or fails on
    # the candidate's own missed empty-string check - both real,
    # attributable outcomes. A no-op for code that reads a fixed,
    # already-known number of values and never touches the extra line.
    stdin_text = "\n".join(stdin) + "\n\n"
    timeout_seconds = settings.execution_timeout_seconds

    try:
        with tempfile.TemporaryDirectory(prefix="qa_eval_round3_") as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / filename
            source.write_text(code, encoding="utf-8")

            prepared = _prepare_run(language, source, tmp_path, timeout_seconds, unbuffered_python=False)
            if prepared.infra_error:
                return ExecutionResult(stdout="", stderr="", exit_code=None, timed_out=False, infra_error=True)
            if prepared.compile_stderr is not None:
                return ExecutionResult(stdout="", stderr=prepared.compile_stderr, exit_code=prepared.compile_exit_code, timed_out=False, infra_error=False)
            run_cmd = prepared.run_cmd

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


# ---- Interactive execution (the candidate's own "Run" button) ----
#
# run_code above is batch-only: every input value is decided before the
# process even starts, which is right for scoring (grading a submission
# against HR's fixed test suite - nobody's waiting on a human) but wrong
# for a candidate trying their own code, who needs the SAME experience
# as running it in a real terminal: see the program's input() prompt
# appear, type a real answer, see it continue, and only ever hit an
# error where the code itself would actually produce one - never a
# guessed/substituted value they never saw or typed. InteractiveSession
# is a real, long-lived subprocess (asyncio, not subprocess.run) that
# the candidate.py route layer polls and writes into across several HTTP
# calls instead of one blocking call - see that router's
# /round/3/run/start, /run/poll, /run/input, /run/stop.


class InteractiveSession:
    """One live candidate Run. Constructed already in its terminal state
    (exited=True) for the "never even started" cases (unsupported infra,
    a Java compile failure) - the polling route treats those exactly
    like a session that ran and finished, no separate code path needed.

    Built on plain subprocess.Popen + background threads, deliberately
    NOT asyncio.create_subprocess_exec - see this module's "switch
    execution to local subprocess" note above and the 2026-08-24 note
    below: on Windows, uvicorn's --reload forces WindowsSelectorEventLoopPolicy
    (see uvicorn/loops/asyncio.py's asyncio_setup, use_subprocess=True),
    and it does this BEFORE importing the app - by the time our own code
    runs, the event loop already exists as a SelectorEventLoop, which
    categorically does not support asyncio subprocesses on Windows.
    Threads sidestep that entirely: they work under any event loop, the
    same way run_code's plain subprocess.run already did."""

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self._tmp_dir: tempfile.TemporaryDirectory | None = None
        self._lock = threading.Lock()
        self.stdout_buffer = ""
        self.stderr_buffer = ""
        self.stdin_sent: list[str] = []
        self.exited = False
        self.exit_code: int | None = None
        self.timed_out = False
        self.infra_error = False
        self._reader_threads: list[threading.Thread] = []
        # None for a session that never really started (compile/infra
        # failure) - duration doesn't mean anything there.
        self.started_monotonic: float | None = None

    def _read_stream_thread(self, stream, attr: str) -> None:
        while True:
            chunk = stream.read(4096)  # blocks THIS thread only, never the event loop
            if not chunk:
                return
            text = chunk.decode("utf-8", errors="replace")
            with self._lock:
                setattr(self, attr, getattr(self, attr) + text)

    def _watchdog_thread(self, timeout_seconds: int) -> None:
        """Bounds the OVERALL session lifetime (settings.
        interactive_execution_timeout_seconds - a human-typing-paced
        ceiling, not the short batch one) - a safety net against a
        genuinely hung or abandoned run, not a per-response wait."""
        try:
            self.process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            self.timed_out = True
            try:
                self.process.kill()
            except ProcessLookupError:
                pass
            self.process.wait()
        # The reader threads only finish once each pipe is actually
        # closed, which follows the process's own exit - joining them
        # here guarantees every last already-buffered byte of output has
        # been captured before this session reports itself exited.
        for thread in self._reader_threads:
            thread.join()
        self.exit_code = self.process.returncode
        self.exited = True

    def _start(self, process: subprocess.Popen, tmp_dir: tempfile.TemporaryDirectory, timeout_seconds: int) -> None:
        self.process = process
        self._tmp_dir = tmp_dir
        self.started_monotonic = time.monotonic()
        self._reader_threads = [
            threading.Thread(target=self._read_stream_thread, args=(process.stdout, "stdout_buffer"), daemon=True),
            threading.Thread(target=self._read_stream_thread, args=(process.stderr, "stderr_buffer"), daemon=True),
        ]
        for thread in self._reader_threads:
            thread.start()
        threading.Thread(target=self._watchdog_thread, args=(timeout_seconds,), daemon=True).start()

    async def write_input(self, line: str) -> None:
        """Feeds one line (the candidate's typed reply to whatever the
        program's last input() prompt was) straight to the live
        process's stdin - never buffered/decided upfront like run_code's
        batch stdin. The actual pipe write is a blocking call, so it runs
        in a thread (asyncio.to_thread) rather than on the event loop."""
        if self.process is None or self.exited:
            return
        self.stdin_sent.append(line)

        def _write() -> None:
            try:
                self.process.stdin.write((line + "\n").encode("utf-8"))
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass  # the process already exited/closed stdin on its own - nothing left to send to

        await asyncio.to_thread(_write)

    async def stop(self) -> None:
        """Candidate-initiated cancel, or cleanup on Run being clicked
        again / the round ending - kills the process (if still alive)
        and releases its temp directory. Idempotent. Reader/watchdog
        threads are daemon threads - they exit on their own once the
        killed process's pipes close, no explicit join needed here."""
        if self.process is not None and self.process.returncode is None:
            try:
                self.process.kill()
            except ProcessLookupError:
                pass
        if self._tmp_dir is not None:
            await asyncio.to_thread(self._tmp_dir.cleanup)
        self.exited = True


async def start_interactive(language: str, code: str) -> InteractiveSession:
    """Starts one real, live subprocess for the candidate's own Run
    button. Returns immediately (the process keeps running in the
    background) - the caller polls the returned session's
    stdout_buffer/stderr_buffer/exited/exit_code as it progresses."""
    session = InteractiveSession()
    filename = _SOURCE_FILENAME.get(language)
    if filename is None:
        raise ValueError(f"Unsupported language: {language}")

    tmp_dir = tempfile.TemporaryDirectory(prefix="qa_eval_round3_interactive_")
    tmp_path = Path(tmp_dir.name)
    source = tmp_path / filename
    source.write_text(code, encoding="utf-8")

    # unbuffered_python=True: without it, CPython fully buffers stdout
    # when it's a pipe rather than a real terminal - an input() prompt's
    # text would sit in that buffer instead of actually reaching the
    # poll loop (and the candidate) before the process blocks waiting for
    # a reply, which would look like the program had silently hung.
    prepared = _prepare_run(language, source, tmp_path, settings.execution_timeout_seconds, unbuffered_python=True)
    if prepared.infra_error:
        tmp_dir.cleanup()
        session.infra_error = True
        session.exited = True
        return session
    if prepared.compile_stderr is not None:
        tmp_dir.cleanup()
        session.stderr_buffer = prepared.compile_stderr
        session.exit_code = prepared.compile_exit_code
        session.exited = True
        return session
    run_cmd = prepared.run_cmd

    try:
        # Popen itself is effectively non-blocking (it returns as soon as
        # the OS has started the process), but still run it off the event
        # loop for consistency with everything else here being safe to
        # call from an async route without stalling it.
        process = await asyncio.to_thread(
            subprocess.Popen, _sandboxed(run_cmd, tmp_path), cwd=tmp_path,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            # bufsize=0: unbuffered on the PARENT's read side. Popen's
            # default (-1, fully buffered for a non-interactive pipe)
            # makes stream.read(4096) block until 4096 bytes accumulate
            # rather than returning as soon as anything is available - a
            # short prompt like "enter a: " would never reach that
            # threshold, so it would just sit there looking hung. This
            # is the parent-side counterpart to python's own -u flag
            # above (that one's about the CHILD not buffering writes;
            # this one's about the PARENT not buffering reads).
            bufsize=0,
            # Same UTF-8 child environment as the batch path - see
            # _child_env. The parent side here already decodes utf-8 with
            # errors="replace" (_read_stream_thread), so this is the only
            # half that was missing.
            env=_child_env(tmp_path),
        )
    except OSError:
        tmp_dir.cleanup()
        session.infra_error = True
        session.exited = True
        return session

    session._start(process, tmp_dir, settings.interactive_execution_timeout_seconds)
    return session
