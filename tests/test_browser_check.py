"""The Browser check page: built into the site, linked from the menu, and never touching the network."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "site_src" / "assets" / "browser-check"


def test_modules_make_no_network_request_and_store_nothing():
    forbidden = re.compile(r"\b(fetch|XMLHttpRequest|sendBeacon|WebSocket|EventSource|localStorage\.setItem|"
                           r"sessionStorage\.setItem|document\.cookie|indexedDB\.open)\b|https?://")
    for path in ASSETS.glob("*.js"):
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"//[^\n]*", "", text)  # comments may mention these words
        assert not forbidden.search(text), path.name


def test_template_loads_only_its_own_script_and_names_no_third_party_resource():
    html = (ROOT / "site_src" / "templates" / "browser_check.html").read_text(encoding="utf-8")
    assert "assets/browser-check/app.js" in html
    for tag in re.findall(r"<(?:script|img|link|iframe)[^>]+(?:src|href)=\"(https?://[^\"]+)\"", html):
        raise AssertionError(f"external resource: {tag}")


def test_menu_and_footer_link_to_the_page():
    base = (ROOT / "site_src" / "templates" / "base.html").read_text(encoding="utf-8")
    assert base.count("browser-check.html") >= 2 and "'browser-check'" in base


def _node_major():
    node = shutil.which("node")
    if node is None:
        return 0
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    return int(out.split(".")[0]) if out else 0


@pytest.mark.skipif(_node_major() < 22, reason="Node 22 or newer is needed to run the ES-module tests")
def test_javascript_unit_tests_pass():
    done = subprocess.run(["node", "--test", "tests/browser_check/util.test.js"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
