"""Lint as part of the suite (ruff.toml): undefined or unused names and
syntax errors fail the tests instead of crashing a live request - the way
"NameError: name 're' is not defined" once reached the running server."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_backend_and_tests_pass_lint():
    ruff = shutil.which("ruff") or str(ROOT / ".venv" / "bin" / "ruff")
    if not Path(ruff).exists():
        pytest.skip("ruff not installed - pip install -r requirements-dev.txt")
    result = subprocess.run([ruff, "check", "backend/app", "tests", "--no-cache"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-3000:]


def test_page_scripts_parse():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    scripts = sorted((ROOT / "backend" / "app" / "static" / "js").glob("*.js"))
    assert scripts, "no page scripts found"
    for script in scripts:
        result = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr[-1500:]}"
