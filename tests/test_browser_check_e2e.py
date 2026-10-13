"""The Browser check page in real browsers (Chromium and Firefox, skipped when they are not installed).

The site is built into a temporary folder and served under a sub-path, like GitHub Pages does. Every request to another
host is intercepted: nothing here reaches the internet."""
import functools
import http.server
import re
import threading
from urllib.parse import urlparse

import pytest

from test_site import built  # noqa: F401  (fixture)

PAGE = "/site/browser-check.html"
TRACKER_HOSTS = ["pagead2.googlesyndication.com", "securepubads.g.doubleclick.net", "static.ads-twitter.com",
                 "bat.bing.com", "www.google-analytics.com", "connect.facebook.net", "sb.scorecardresearch.com"]
TRACE = "fl=1\nip=203.0.113.9\nloc=ES\ntls=TLSv1.3\nhttp=http/2\n"


@pytest.fixture(scope="module", params=["chromium", "firefox"])
def browser(request):
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        p = sync_api.sync_playwright().start()
        b = getattr(p, request.param).launch()
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"{request.param} not available: {exc}")
    yield b
    b.close()
    p.stop()


@pytest.fixture
def served(built):  # noqa: F811
    tmp_path, _, _ = built
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


class Visit:
    """A page with every outside request recorded and answered by the test."""

    def __init__(self, browser, base, outside="abort"):
        self.page = browser.new_page()
        self.outside_requests, self.errors = [], []
        self.base_host = urlparse(base).netloc
        self.outside = outside
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.route("**/*", self._route)
        self.page.goto(base + PAGE)

    def _route(self, route):
        url = route.request.url
        if urlparse(url).netloc == self.base_host:
            return route.continue_()
        self.outside_requests.append(url)
        if "cloudflare.com/cdn-cgi/trace" in url:
            return route.fulfill(status=200, body=TRACE, headers={"Access-Control-Allow-Origin": "*", "Content-Type": "text/plain"})
        if self.outside == "reach":
            return route.fulfill(status=200, body="", headers={"Access-Control-Allow-Origin": "*"})
        return route.abort()


def test_nothing_is_read_and_nothing_leaves_before_a_button_is_pressed(browser, served):
    visit = Visit(browser, served)
    assert visit.page.locator("#bc-results").is_hidden()
    assert visit.page.locator("#bc-tiles > *").count() == 0
    visit.page.wait_for_timeout(500)
    assert visit.outside_requests == [] and visit.errors == []


def test_reading_shows_every_detail_without_errors_or_outside_requests(browser, served):
    visit = Visit(browser, served)
    visit.page.click("#bc-read")
    visit.page.wait_for_selector("#bc-results:not([hidden])", timeout=15000)
    assert visit.page.locator(".bc-entry").count() == 27
    assert visit.page.locator("#bc-code").inner_text() != ""
    assert visit.outside_requests == [] and visit.errors == []


def test_open_all_close_all_and_the_selector(browser, served):
    visit = Visit(browser, served)
    visit.page.click("#bc-read")
    visit.page.wait_for_selector("#bc-results:not([hidden])", timeout=15000)
    assert visit.page.locator(".bc-entry[open]").count() == 0
    visit.page.click("#bc-expand")
    assert visit.page.locator(".bc-entry[open]").count() == 27
    visit.page.click("#bc-collapse")
    assert visit.page.locator(".bc-entry[open]").count() == 0
    visit.page.select_option("#bc-select", "timezone")
    assert visit.page.locator(".bc-entry:visible").count() == 1
    visit.page.select_option("#bc-select", "all")
    assert visit.page.locator(".bc-entry:visible").count() == 27
    assert visit.errors == []


def test_reading_works_from_the_keyboard_alone(browser, served):
    visit = Visit(browser, served)
    for _ in range(60):
        visit.page.keyboard.press("Tab")
        if visit.page.evaluate("document.activeElement && document.activeElement.id") == "bc-read":
            break
    else:
        pytest.fail("the Read button cannot be reached with the Tab key")
    visit.page.keyboard.press("Enter")
    visit.page.wait_for_selector("#bc-results:not([hidden])", timeout=15000)
    assert visit.page.locator(".bc-entry").count() == 27


def test_comparing_two_reports(browser, served):
    visit = Visit(browser, served)
    visit.page.click("#bc-read")
    visit.page.wait_for_selector("#bc-results:not([hidden])", timeout=15000)
    visit.page.click("#bc-again")
    visit.page.wait_for_function("document.getElementById('bc-compare').textContent.length > 0", timeout=15000)
    assert "Same combined code" in visit.page.locator("#bc-compare").inner_text()
    visit.page.evaluate("document.querySelector('#bc-after details').open = true")
    visit.page.fill("#bc-paste", "Browser check, x\nCombined code: ffff\n\nPlatform: Other")
    visit.page.click("#bc-compare-btn")
    out = visit.page.locator("#bc-compare-out").inner_text()
    assert "differ" in out and "Platform" in out


def test_tracker_test_says_blocked_when_every_request_is_refused(browser, served):
    visit = Visit(browser, served, outside="abort")
    visit.page.click("#bc-test")
    visit.page.wait_for_function("document.querySelectorAll('#bc-test-out tbody tr').length === 7", timeout=20000)
    assert sorted(urlparse(u).netloc for u in visit.outside_requests) == sorted(TRACKER_HOSTS)
    text = visit.page.locator("#bc-test-out").inner_text()
    assert text.count("Yes, all blocked") == 2 and "reached the server" not in text


def test_tracker_test_says_none_blocked_when_every_request_gets_an_answer(browser, served):
    visit = Visit(browser, served, outside="reach")
    visit.page.click("#bc-test")
    visit.page.wait_for_function("document.querySelectorAll('#bc-test-out tbody tr').length === 7", timeout=20000)
    text = visit.page.locator("#bc-test-out").inner_text()
    assert text.count("No, none blocked") == 2


def test_connection_check_shows_the_address_the_service_returned(browser, served):
    visit = Visit(browser, served)
    visit.page.click("#bc-conn")
    visit.page.wait_for_function("document.querySelectorAll('#bc-conn-out .bc-tile').length >= 3", timeout=20000)
    text = visit.page.locator("#bc-conn-out").inner_text()
    assert "203.0.113.9" in text and "ES" in text and "TLSv1.3" in text


def test_the_page_is_labelled_for_assistive_technology(browser, served):
    visit = Visit(browser, served)
    page = visit.page
    assert page.locator("h1").count() == 1
    levels = page.evaluate("[...document.querySelectorAll('main h1, main h2, main h3')].map(h => Number(h.tagName[1]))")
    assert all(b - a <= 1 for a, b in zip(levels, levels[1:])), levels
    assert page.locator("label[for='bc-select']").count() == 1 and page.locator("label[for='bc-paste']").count() == 1
    for region in ("bc-status", "bc-compare", "bc-compare-out", "bc-test-out", "bc-conn-out"):
        assert page.get_attribute(f"#{region}", "role") == "status", region
    names = page.evaluate("[...document.querySelectorAll('main button')].map(b => (b.innerText || b.getAttribute('aria-label') || '').trim())")
    assert all(names), names
    assert re.search(r"nothing happens until you press a button", page.locator("main").inner_text(), re.I)


def test_nothing_overflows_on_a_phone_sized_screen(browser, served):
    visit = Visit(browser, served)
    visit.page.set_viewport_size({"width": 375, "height": 812})
    visit.page.click("#bc-read")
    visit.page.wait_for_selector("#bc-results:not([hidden])", timeout=15000)
    visit.page.click("#bc-expand")
    assert visit.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert visit.errors == []
