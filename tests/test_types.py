"""Type checking as part of the suite - a ratchet (mypy.ini): every file
listed there is type-clean and must stay that way. Add files as they're
cleaned up; never remove one to make this pass."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_ratcheted_files_stay_type_clean():
    mypy = shutil.which("mypy") or str(ROOT / ".venv" / "bin" / "mypy")
    if not Path(mypy).exists():
        pytest.skip("mypy not installed - pip install -r requirements-dev.txt")
    result = subprocess.run([mypy, "--config-file", "../mypy.ini"], cwd=ROOT / "backend", capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-3000:]
