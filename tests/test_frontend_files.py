"""Static checks on the frontend files.

Regression: a single stray "}" in app.css made the browser drop the next rule (.drawer), so the glossary and the
event-log drawers rendered in normal flow below every page and the page scrolled for thousands of pixels past its
content. No browser is needed to catch that class of mistake.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
CSS = FRONTEND / "css" / "app.css"


def _css():
    return re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)


def test_fe01_stylesheet_braces_balance_at_every_line():
    depth = 0
    for number, line in enumerate(_css().splitlines(), 1):
        depth += line.count("{") - line.count("}")
        assert depth >= 0, f"app.css: unmatched closing brace on line {number} (comments stripped)"
    assert depth == 0, f"app.css: {depth} unclosed block(s)"


def test_fe02_overlays_stay_out_of_the_page_flow():
    css = _css()
    for selector, needle in ((".drawer", "position: fixed"), (".mobile-nav", "position: fixed"), (".toasts", "position: fixed"),
                             (".scrim", "position: fixed"), (".topbar", "position: sticky")):
        m = re.search(r"(?m)^" + re.escape(selector) + r" \{([^{}]*)\}", css)
        assert m, f"app.css has no top-level {selector} rule (the browser may have dropped it)"
        assert needle in m.group(1), f"{selector} must be {needle}"


def test_fe03_every_script_parses():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    files = sorted((FRONTEND / "js").rglob("*.js"))
    assert len(files) > 20
    bad = []
    for f in files:
        r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            bad.append(f"{f.relative_to(FRONTEND)}: {r.stderr.strip().splitlines()[0] if r.stderr.strip() else 'syntax error'}")
    assert not bad, "\n".join(bad)


def test_fe04_every_module_imported_by_the_app_exists():
    missing = []
    for f in (FRONTEND / "js").rglob("*.js"):
        for target in re.findall(r"""from\s+['"](\.{1,2}/[^'"]+)['"]""", f.read_text(encoding="utf-8")):
            if not (f.parent / target).resolve().exists():
                missing.append(f"{f.relative_to(FRONTEND)} imports {target}")
    assert not missing, "\n".join(missing)


def test_fe05_every_module_the_page_preloads_exists():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    hrefs = re.findall(r'rel="modulepreload" href="([^"]+)"', html)
    assert len(hrefs) >= 8
    missing = [h for h in hrefs if not (FRONTEND / h.lstrip("/")).exists()]
    assert not missing, f"index.html preloads files that do not exist: {missing}"


def test_fe06_every_icon_the_scripts_use_is_in_the_sprite():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    sprite = set(re.findall(r'<symbol id="i-([a-z0-9-]+)"', html))
    used = set()
    for text in [html] + [f.read_text(encoding="utf-8") for f in (FRONTEND / "js").rglob("*.js")]:
        used.update(re.findall(r'href="#i-([a-z0-9-]+)"', text))
        for chunk in text.split("icon('")[1:]:
            used.add(chunk.split("'", 1)[0])
    missing = sorted(u for u in used - sprite if u and "$" not in u)
    assert not missing, f"icons used but not defined in the sprite in index.html: {missing}"
