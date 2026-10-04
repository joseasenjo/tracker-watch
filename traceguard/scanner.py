"""Passive page measurement with Playwright.

The scanner loads one page like an ordinary visitor, never clicks or types, and records
what happens during a fixed observation window. It does not hide that it is automated.
"""
from __future__ import annotations

import re
import time
from typing import Iterable
from urllib.parse import urlsplit

from . import __version__
from .classify import Classifier, TrackerList, registrable_domain
from .report import build_report
from .safety import HostGuard, Resolver, check_target, system_resolver

NAVIGATION_TIMEOUT_MS = 30_000
BLOCKED_STATUSES = (401, 403, 429, 503)

# Records API calls that are commonly used for fingerprinting or tracking, together with the
# script URL that made them. Wrappers keep `this`, arguments and the WebRTC prototype intact.
INIT_SCRIPT = r"""
(() => {
  const events = (window.__tg_events = []);
  const caller = () => {
    try {
      const urls = (new Error().stack || "").match(/https?:\/\/[^\s)\]]+/g);
      return urls && urls.length ? urls[0] : null;
    } catch (e) { return null; }
  };
  const record = (kind) => { if (events.length < 500) events.push({ kind, script: caller() }); };
  const wrap = (target, name, kind) => {
    try {
      const original = target && target[name];
      if (typeof original !== "function") return;
      target[name] = function () { record(kind); return original.apply(this, arguments); };
    } catch (e) {}
  };
  wrap(HTMLCanvasElement.prototype, "toDataURL", "canvas_read");
  wrap(HTMLCanvasElement.prototype, "toBlob", "canvas_read");
  if (window.CanvasRenderingContext2D) wrap(CanvasRenderingContext2D.prototype, "getImageData", "canvas_read");
  if (window.Geolocation) {
    wrap(Geolocation.prototype, "getCurrentPosition", "geolocation_request");
    wrap(Geolocation.prototype, "watchPosition", "geolocation_request");
  }
  if (window.RTCPeerConnection) {
    window.RTCPeerConnection = new Proxy(window.RTCPeerConnection, {
      construct(target, args, newTarget) { record("webrtc_connection"); return Reflect.construct(target, args, newTarget); }
    });
  }
  const addListener = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (type) {
    if (type === "keydown" || type === "keyup" || type === "keypress") record("key_listener");
    return addListener.apply(this, arguments);
  };
})();
"""


def strip_query(url: str) -> str:
    """Drop query string and fragment: they can carry tokens or personal data."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def script_host_from_stack(script_url: str | None) -> str | None:
    if not script_url:
        return None
    return urlsplit(script_url).hostname


def _now_ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _scan_pass(browser, target: str, number: int, *, user_agent: str, locale: str, timezone: str,
               observe_seconds: float, min_requests: int, guard: HostGuard, classifier_factory) -> dict:
    run: dict = {
        "pass": number, "status": "ok", "http_status": None, "error": None,
        "final_url": None, "navigation_redirects": [],
        "security_headers": {"strict_transport_security": False, "content_security_policy": "absent"},
        "tls_protocol": None, "requests": [], "internal_requests_blocked": [],
        "cookies": [], "storage_items": {"local": 0, "session": 0}, "observations": [],
    }
    context = browser.new_context(user_agent=user_agent, locale=locale, timezone_id=timezone,
                                  viewport={"width": 1366, "height": 768})
    t0 = time.monotonic()
    recording = {"on": True}  # requests after the observation window are not recorded
    try:
        page = context.new_page()
        page.add_init_script(INIT_SCRIPT)

        def on_route(route):
            try:
                request = route.request
                parts = urlsplit(request.url)
                if parts.scheme in ("http", "https") and parts.hostname:
                    if guard.verdict(parts.hostname) == "internal":
                        if recording["on"]:
                            run["internal_requests_blocked"].append(
                                {"host": parts.hostname,
                                 "port": parts.port or (443 if parts.scheme == "https" else 80),
                                 "t_ms": _now_ms(t0)})
                        route.abort("blockedbyclient")
                        return
                    if recording["on"]:
                        run["requests"].append({
                            "t_ms": _now_ms(t0), "method": request.method,
                            "resource_type": request.resource_type,
                            "url": strip_query(request.url), "host": parts.hostname.lower(),
                        })
                route.continue_()
            except Exception:
                pass  # page closed while the request was in flight

        page.route("**/*", on_route)

        try:
            response = page.goto(target, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        except Exception as exc:
            run["status"] = "error"
            run["error"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
            return run

        run["final_url"] = strip_query(page.url)
        if response is None:
            run["status"], run["error"] = "error", "navigation produced no response"
            return run
        run["http_status"] = response.status
        if response.status in BLOCKED_STATUSES:
            run["status"] = "blocked"
        elif response.status >= 400:
            run["status"], run["error"] = "error", f"HTTP {response.status}"

        headers = response.headers
        run["security_headers"]["strict_transport_security"] = "strict-transport-security" in headers
        if "content-security-policy" in headers:
            run["security_headers"]["content_security_policy"] = "header"
        else:
            try:
                has_meta = page.evaluate(
                    "() => !!document.querySelector('meta[http-equiv=\"Content-Security-Policy\" i]')")
                if has_meta:
                    run["security_headers"]["content_security_policy"] = "meta"
            except Exception:
                pass
        try:
            details = response.security_details()
            if details:
                run["tls_protocol"] = details.get("protocol")
        except Exception:
            pass

        request = response.request
        chain = []
        while request.redirected_from:
            previous = request.redirected_from
            previous_response = previous.response()
            chain.append({"from": strip_query(previous.url), "to": strip_query(request.url),
                          "status": previous_response.status if previous_response else None})
            request = previous
        run["navigation_redirects"] = list(reversed(chain))

        if run["status"] == "blocked":
            return run

        page.wait_for_timeout(int(observe_seconds * 1000))
        recording["on"] = False  # late requests would reach the report without being classified

        if len(run["requests"]) < min_requests:
            # A real page makes many requests; one or two usually means an interstitial or a challenge page.
            run["status"] = "incomplete"
            run["error"] = f"only {len(run['requests'])} request(s) observed (minimum {min_requests})"
            return run

        try:
            run["storage_items"] = page.evaluate(
                """() => { const n = (s) => { try { return s.length; } catch (e) { return 0; } };
                           return { local: n(localStorage), session: n(sessionStorage) }; }""")
        except Exception:
            pass
        try:
            raw_events = page.evaluate("() => window.__tg_events || []")
        except Exception:
            raw_events = []

        final_domain = registrable_domain(urlsplit(page.url).hostname or "")
        classifier = classifier_factory(final_domain)

        for item in run["requests"]:
            item["domain"] = registrable_domain(item["host"])
            item["party"] = classifier.party(item["host"])
            item["tracker"] = classifier.tracker(item["host"])

        seen_cookies = set()
        for cookie in context.cookies():
            domain = registrable_domain(cookie["domain"].lstrip("."))
            key = (cookie["name"], cookie["domain"], cookie.get("path"))
            if key in seen_cookies:
                continue
            seen_cookies.add(key)
            run["cookies"].append({
                "name": cookie["name"], "domain": domain,
                "party": classifier.party(domain),
                "session": cookie.get("expires", -1) == -1,
                "secure": cookie.get("secure", False), "http_only": cookie.get("httpOnly", False),
                "same_site": cookie.get("sameSite"),
            })  # values are never stored

        seen_events = set()
        for event in raw_events:
            host = script_host_from_stack(event.get("script"))
            domain = registrable_domain(host) if host else None
            party = classifier.party(host) if host else "unknown"
            key = (event["kind"], domain)
            if key in seen_events:
                continue
            seen_events.add(key)
            run["observations"].append({"kind": event["kind"], "script_domain": domain, "party": party})
    finally:
        context.close()
    return run


def scan_site(url: str, *, name: str | None = None, first_party_domains: Iterable[str] = (),
              passes: int = 3, observe_seconds: float = 12.0, min_requests: int = 5,
              pause_seconds: float = 3.0, vantage: str = "unspecified",
              locale: str = "en-US", timezone: str = "UTC", tracker_list: TrackerList | None = None,
              resolver: Resolver = system_resolver, headless: bool = True) -> dict:
    """Scan one site `passes` times in fresh browser contexts and return the full report."""
    from playwright.sync_api import sync_playwright  # imported lazily so tests run without it

    target = check_target(url, resolver)
    tracker_list = tracker_list or TrackerList.load()
    declared = [d for d in first_party_domains if d]
    base_first_party = {registrable_domain(urlsplit(target).hostname or ""), *map(registrable_domain, declared)}
    guard = HostGuard(resolver)

    def classifier_factory(final_domain: str) -> Classifier:
        return Classifier({*base_first_party, final_domain}, tracker_list)

    runs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            browser_version = browser.version
            major = browser_version.split(".")[0]
            # Plain "Chrome" token (some CDNs reject the default "HeadlessChrome" one and serve an error
            # page) plus an explicit TraceGuard token, so the tool still identifies itself.
            user_agent = (f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          f"Chrome/{major}.0.0.0 Safari/537.36 TraceGuard/{__version__}")
            for number in range(1, passes + 1):
                if number > 1:
                    time.sleep(pause_seconds)  # spacing requests avoids tripping rate limits
                runs.append(_scan_pass(browser, target, number, user_agent=user_agent, locale=locale,
                                       timezone=timezone, observe_seconds=observe_seconds,
                                       min_requests=min_requests, guard=guard,
                                       classifier_factory=classifier_factory))
        finally:
            browser.close()

    measurement = {
        "vantage": vantage, "locale": locale, "timezone": timezone, "user_agent": user_agent,
        "browser": f"chromium {browser_version}", "passes": passes, "observe_seconds": observe_seconds,
        "min_requests": min_requests, "pause_seconds": pause_seconds,
        "interaction": "none", "tracker_list": tracker_list.source,
    }
    site = {"name": name or (urlsplit(target).hostname or target), "url": target,
            "first_party_domains": sorted(base_first_party)}
    return build_report(site, measurement, runs, tracker_list)
