"""
Candidate code runs in a sandbox (execution_service._sandboxed). An audit
showed unsandboxed candidate code could read .env (API key, login-signing
secret), write the real database and reach the network. These run real code
through the real sandbox - offline, nothing leaves the machine.
"""
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.services import execution_service

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec")
REPO = Path(__file__).resolve().parent.parent

PROBE = f'''
import os
def spawn():
    if os.system("true") != 0:
        raise OSError("spawn refused")
checks = [
    ("read .env", lambda: open({str(REPO / ".env")!r}).read(1)),
    ("read the real database", lambda: open({str(REPO / "backend" / "qa_eval.db")!r}, "rb").read(1)),
    ("write into the repo", lambda: open({str(REPO / "sandbox_probe.txt")!r}, "w").write("x")),
    ("list the home folder", lambda: os.listdir(os.path.expanduser("~{Path.home().name}"))),
    ("start another process", spawn),
    ("reach the network", lambda: __import__("socket").create_connection(("1.1.1.1", 53), timeout=3)),
]
for label, fn in checks:
    try:
        fn()
        print("ALLOWED:", label)
    except Exception:
        pass
print("secrets:", [k for k in os.environ if any(w in k for w in ("KEY", "SECRET", "TOKEN", "PASSWORD"))])
open("own_temp_file.txt", "w").write("ok")
print("sum", sum(int(x) for x in input().split(",")))
'''


def test_candidate_code_cannot_reach_secrets_data_network_or_processes():
    result = execution_service.run_code(language="python", code=PROBE, stdin=["3,4"])
    assert not result.infra_error, result.stderr
    assert "ALLOWED" not in result.stdout, result.stdout
    assert "secrets: []" in result.stdout
    assert "sum 7" in result.stdout  # ordinary code - input, output, its own temp files - still works
    assert not (REPO / "sandbox_probe.txt").exists()


@pytest.mark.parametrize("language, code", [
    ("javascript", "try { require('fs').readFileSync('%s'); console.log('ALLOWED') } catch (e) { console.log('blocked') }"),
    ("java", 'public class Main { public static void main(String[] a) { try { new java.io.FileReader("%s").read(); System.out.println("ALLOWED"); } catch (Exception e) { System.out.println("blocked"); } } }'),
])
def test_other_languages_are_sandboxed_too(language, code):
    if not execution_service.toolchain_available(language):
        pytest.skip(f"{language} isn't installed here")
    result = execution_service.run_code(language=language, code=code % (REPO / ".env"), stdin=[])
    assert result.stdout.strip() == "blocked", (result.stdout, result.stderr)


def test_no_sandbox_means_no_run(monkeypatch):
    """Where the sandbox can't be used, code is refused - never run unprotected."""
    monkeypatch.setattr(execution_service, "_SANDBOX_EXEC", "/nonexistent/sandbox-exec")
    result = execution_service.run_code(language="python", code="print(1)", stdin=[])
    assert result.infra_error and result.stdout == ""
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    assert execution_service.run_code(language="python", code="print(1)", stdin=[]).stdout.strip() == "1"
