"""The page's JavaScript as one text, in load order (main.APP_SCRIPTS) - for
tests that read the page source (the contract test, markup checks)."""
from pathlib import Path

from app.main import APP_SCRIPTS

JS_DIR = Path(__file__).resolve().parent.parent / "backend" / "app" / "static" / "js"


def page_js() -> str:
    return "\n".join((JS_DIR / name).read_text() for name in APP_SCRIPTS)
