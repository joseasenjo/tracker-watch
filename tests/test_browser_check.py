"""The Browser check page: built into the site, linked from the menu, and touching the network only in its opt-in test."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "site_src" / "assets" / "browser-check"
NETWORK_WORDS = re.compile(r"\b(fetch|XMLHttpRequest|sendBeacon|WebSocket|EventSource)\b")
STORAGE_WORDS = re.compile(r"\b(localStorage\.setItem|sessionStorage\.setItem|document\.cookie|indexedDB\.open)\b")
ONLY_THE_OPT_IN_CHECKS_MAY_ASK = ("tracker-test.js", "connection-test.js", "app.js")


def _code(path):
    """The module without its comments (they may mention these words)."""
    return re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8"))


def test_nothing_stores_anything_and_only_the_opt_in_test_can_ask_for_the_network():
    for path in ASSETS.glob("*.js"):
        text = _code(path)
        assert not STORAGE_WORDS.search(text), path.name
        if path.name not in ONLY_THE_OPT_IN_CHECKS_MAY_ASK:
            assert not NETWORK_WORDS.search(text), path.name
            assert "https://" not in text, path.name


def test_app_asks_for_the_network_only_inside_the_two_opt_in_handlers():
    text = _code(ASSETS / "app.js")
    assert text.count("fetch(") == 2
    assert text.index("fetch(") > text.index("async function onTest")
    assert text.count("RTCPeerConnection") == 1 and text.index("RTCPeerConnection") > text.index("bc-conn")


def test_the_template_lists_every_server_the_opt_in_test_contacts():
    html = (ROOT / "site_src" / "templates" / "browser_check.html").read_text(encoding="utf-8")
    hosts = re.findall(r"host: '([^']+)'", (ASSETS / "tracker-test.js").read_text(encoding="utf-8"))
    assert len(hosts) == 7
    for host in hosts:
        assert host in html, host
    connection = (ASSETS / "connection-test.js").read_text(encoding="utf-8")
    for needle in re.findall(r"'(?:https://|stun:)([^':/]+)", connection):
        assert needle in html, needle


def test_template_loads_only_its_own_script_and_names_no_third_party_resource():
    html = (ROOT / "site_src" / "templates" / "browser_check.html").read_text(encoding="utf-8")
    assert "assets/browser-check/app.js" in html
    assert not re.findall(r"<(?:script|img|link|iframe)[^>]+(?:src|href)=\"https?://", html)


def test_page_has_the_selector_and_open_close_all_and_wraps_each_group():
    html = (ROOT / "site_src" / "templates" / "browser_check.html").read_text(encoding="utf-8")
    for needle in ("bc-select", "bc-expand", "bc-collapse", "bc-groups", 'class="bc-group"'):
        assert needle in html, needle
    assert html.count("<div") == html.count("</div>")


def test_glossary_and_links_from_other_pages():
    templates = ROOT / "site_src" / "templates"
    html = (templates / "browser_check.html").read_text(encoding="utf-8")
    for term in ("Browser fingerprint", "Canvas", "WebGL and WebGPU", "WebRTC", "STUN server", "TLS", "Hash"):
        assert f"<dt>{term}</dt>" in html, term
    for page in ("protect.html", "extension.html", "index.html"):
        assert "browser-check.html" in (templates / page).read_text(encoding="utf-8"), page


def test_home_hero_links_to_the_page_with_a_correctly_sized_preview():
    import struct
    home = (ROOT / "site_src" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'class="hero-shot"' in home and "browser-check.html" in home and "Analyse your browser" in home
    image = ROOT / "site_src" / "assets" / "browser-check-preview.png"
    data = image.read_bytes()
    width, height = struct.unpack(">II", data[16:24])
    assert f'width="{width}" height="{height}"' in home
    assert len(data) < 150_000
    assert re.search(r'<img[^>]+alt="[^"]{40,}"', home)


def test_reading_waits_for_a_button():
    app = _code(ASSETS / "app.js")
    assert "addEventListener('click', onRead)" in app
    assert not re.search(r"^run\(\)|^onRead\(\)|^readAll\(\)", app, re.M)


def test_menu_footer_and_privacy_page_mention_the_page_and_its_test():
    base = (ROOT / "site_src" / "templates" / "base.html").read_text(encoding="utf-8")
    assert base.count("browser-check.html") >= 3 and "'browser-check'" in base
    privacy = (ROOT / "site_src" / "templates" / "privacy.html").read_text(encoding="utf-8")
    assert "tracker test" in privacy


def _node_major():
    node = shutil.which("node")
    if node is None:
        return 0
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    return int(out.split(".")[0]) if out else 0


@pytest.mark.skipif(_node_major() < 22, reason="Node 22 or newer is needed to run the ES-module tests")
def test_javascript_unit_tests_pass():
    done = subprocess.run(["node", "--test", "tests/browser_check/util.test.js"], cwd=ROOT, capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
@pytest.mark.parametrize("path", sorted(ASSETS.glob("*.js")), ids=lambda p: p.name)
def test_every_module_is_valid_javascript(path):
    done = subprocess.run(["node", "--input-type=module", "--check"], input=path.read_bytes(), capture_output=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
