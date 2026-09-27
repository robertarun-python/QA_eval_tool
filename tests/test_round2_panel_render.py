"""
The candidate's reference panel renders every section, and page sources show
as text - never as live HTML on the candidate's screen. Runs the page's own
round2AutomationPanelHtml in Node. No AI calls.
"""
import json
import subprocess
from pathlib import Path

import pytest

from app.services import execution_service
from app.services.practice_engine import reference

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "backend" / "app" / "static" / "js"
SPEC = json.loads((ROOT / "tests" / "fixtures" / "practice_engine" / "library_spec.json").read_text())


@pytest.mark.skipif(not execution_service.toolchain_available("javascript"), reason="node not installed")
def test_the_panel_renders_every_section_and_escapes_page_sources():
    panel = reference.reference_panel(SPEC)
    script = f"""
const vm = require("vm"); const fs = require("fs");
// escapeHtml uses the browser: a stand-in that escapes text the way a browser's innerHTML does
const document = {{ createElement: () => ({{ set textContent(t) {{ this.t = String(t); }},
  get innerHTML() {{ return this.t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }} }}) }};
const ctx = {{ console, document }}; vm.createContext(ctx);
const guard = fs.readFileSync({json.dumps(str(JS / "tab_guard.js"))}, "utf8");
vm.runInContext(guard.match(/function escapeHtml[\\s\\S]*?\\n}}/)[0], ctx);
vm.runInContext(guard.match(/function escapeAttr[\\s\\S]*?\\n}}/)[0], ctx);
const r2 = fs.readFileSync({json.dumps(str(JS / "round2_automation.js"))}, "utf8");
vm.runInContext(r2.match(/const _VOID_TAGS[^\\n]*\\n/)[0] + r2.match(/function round2PrettyHtml[\\s\\S]*?\\n}}\\n/)[0], ctx);
vm.runInContext(fs.readFileSync({json.dumps(str(JS / "round2_automation.js"))}, "utf8").match(/function round2AutomationPanelHtml[\\s\\S]*?\\n}}\\n/)[0], ctx);
ctx.panel = {json.dumps(panel)};
process.stdout.write(vm.runInContext("round2AutomationPanelHtml(panel)", ctx));
"""
    html = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert html.returncode == 0, html.stderr
    out = html.stdout
    for part in ("Connect & accounts", "PRACTICE_APP_URL", "testuser@library.test", "/api/borrow-book", "available_copies",
                 "/test/advance-minutes", "network_down", "Book Details"):
        assert part in out, part
    assert "&lt;form id=" in out  # the page source is shown as text
    assert '<form id="borrow-book-form"' not in out  # never live HTML on the candidate's own page
    # Each page is also shown as a screen - only inside a locked-down frame whose content can't break out of it.
    import re
    frames = re.findall(r'<iframe class="r4a-page-screen" sandbox="" [^>]*srcdoc="([^"]*)"', out)
    assert len(frames) == len(panel["pages"]) and all("&lt;form" in f or "&lt;html" in f for f in frames)
    assert out.count("<iframe") == len(frames)  # no frame without the sandbox
