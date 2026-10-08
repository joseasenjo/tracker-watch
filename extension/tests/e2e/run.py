"""End-to-end test: the test build loaded in Playwright's Chromium and Firefox, offline.

A local HTTPS server answers for every host (real tracker hosts from the list are pointed at 127.0.0.1),
a page contacts some of them before and one after a click, and the report must say exactly that.

    python extension/tests/e2e/run.py [--browser chromium|firefox|all] [--screenshot out.png]

Firefox: the add-on is installed as a temporary add-on over the remote debugging protocol (M0 spike).
"""
from __future__ import annotations

import argparse
import datetime
import http.server
import json
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

EXT = Path(__file__).resolve().parents[2]
PORT = 18543
RDP_PORT = 18602
HOSTS = ["site.test", "control.test", "www.googletagmanager.com", "stats.g.doubleclick.net", "ib.adnxs.com",
         "cdn.unknown.test", "connect.facebook.net", "www.google.com", "cdn.cookielaw.org", "www.youtube.com", "popads.net", "a.test", "b.test", "px.watcher.test"]
U = lambda host, path: f"https://{host}:{PORT}{path}"  # noqa: E731

PAGE = f"""<!doctype html><meta charset="utf-8"><title>e2e</title>
<script src="{U('www.googletagmanager.com', '/gtm.js')}"></script>
<script src="{U('ib.adnxs.com', '/ut.js')}"></script>
<script src="{U('cdn.unknown.test', '/x.js')}"></script>
<script>window.addEventListener('load', () => {{ new Image().src = "{U('ib.adnxs.com', '/px')}"; }});</script>
<img src="{U('stats.g.doubleclick.net', '/collect?gclid=SECRET')}">
<script src="{U('cdn.cookielaw.org', '/otSDKStub.js')}"></script>
<iframe src="{U('www.youtube.com', '/embed/x')}" width="10" height="10"></iframe>
<div id="onetrust-banner-sdk" style="position:fixed;bottom:0;left:0;right:0;background:#eee;padding:10px">
  We use cookies. <button id="onetrust-reject-all-handler">Reject all</button>
  <button id="onetrust-accept-btn-handler">Accept all</button></div>
<script>
document.getElementById('onetrust-reject-all-handler').addEventListener('click', () => {{
  const s = document.createElement('script'); s.src = "{U('connect.facebook.net', '/sdk.js')}"; document.head.appendChild(s);
}});
</script>"""

SERP = f"""<!doctype html><meta charset="utf-8"><title>results</title>
<a id="r1" href="/url?q={U('site.test', '/dest1')}&sa=U">result one</a>
<a id="r2" href="{U('site.test', '/dest2')}" ping="/gen_204?r=2">result two</a>
<a href="/preferences">settings</a>
<script>navigator.sendBeacon("/gen_204?beacon=1", "x");</script>"""

# A Spanish-style banner with custom buttons (as on elmundo.es), and a same-site page prerendered with
# speculation rules (Chromium shows it on click without a new navigation request).
ES_PAGE = f"""<!doctype html><meta charset="utf-8"><title>es</title>
<div id="didomi-popup" style="position:fixed;bottom:0;left:0;right:0;background:#eee;padding:10px">
  <span class="custom-cta"><button id="pay">Rechazar y suscribirse</button></span>
  <button id="ok">Aceptar y continuar</button></div>
<a id="next" href="/prerendered">next page</a>
<script type="speculationrules">{{"prerender": [{{"source": "list", "urls": ["/prerendered"]}}]}}</script>"""
UNKNOWN_BANNER = """<!doctype html><meta charset="utf-8"><title>unknown cmp</title>
<div class="consent-wall"><p>Usamos cookies y tecnolog\u00edas similares con nuestros socios.</p>
<button id="yes" onclick="setTimeout(() => location.reload(), 200)">Aceptar y cerrar</button>
<button>Rechazar y pagar</button></div>"""
PRERENDERED = f"""<!doctype html><meta charset="utf-8"><title>prerendered</title>
<img src="{U('ib.adnxs.com', '/pre.gif')}"><img src="{U('stats.g.doubleclick.net', '/pre.gif')}">"""

# "Block ads": an ad network of EasyList (not on our lists) and a slot hidden by one of its generic rules
ADS_PAGE = f"""<!doctype html><meta charset="utf-8"><title>ads</title>
<div class="ad--banner" id="slot">advert</div><p id="text">article</p>
<img src="{U('popads.net', '/ad.gif')}">"""

# Step 4: a third party on no list that gets an identifier cookie on every site that embeds it
LEARN_PAGE = f"""<!doctype html><meta charset="utf-8"><title>learn</title>
<img src="{U('px.watcher.test', '/p1.gif')}">
<script>setTimeout(() => {{ new Image().src = "{U('px.watcher.test', '/p2.gif')}"; }}, 400);</script>"""

EXPECTED = {"trackingBefore": 2, "trackingNewAfter": ["facebook.net"], "thirdPartyDomains": 7, "band": "A"}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        path = self.path.split("?")[0]
        if host == "site.test" and path == "/":
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
        elif host == "site.test" and path == "/es":
            body, ctype = ES_PAGE.encode(), "text/html; charset=utf-8"
        elif host == "site.test" and path == "/unknown":
            body, ctype = UNKNOWN_BANNER.encode(), "text/html; charset=utf-8"
        elif host in ("site.test", "a.test", "b.test") and path == "/learn":
            body, ctype = LEARN_PAGE.encode(), "text/html; charset=utf-8"
        elif host == "site.test" and path == "/loading":  # a page that keeps loading (the panel redraws itself)
            body, ctype = f'<!doctype html><title>loading</title><img src="{U("cdn.unknown.test", "/slow")}">'.encode(), "text/html"
        elif host == "cdn.unknown.test" and path == "/slow":
            time.sleep(9)
            body, ctype = b"/* slow */", "application/javascript"
        elif host == "site.test" and path == "/ads":
            body, ctype = ADS_PAGE.encode(), "text/html; charset=utf-8"
        elif host == "site.test" and path == "/prerendered":
            body, ctype = PRERENDERED.encode(), "text/html; charset=utf-8"
        elif host == "site.test":
            body, ctype = b"<!doctype html><title>dest</title><p>destination</p>", "text/html"
        elif host == "www.google.com" and path == "/search":
            body, ctype = SERP.encode(), "text/html; charset=utf-8"
        elif host == "www.google.com" and path == "/url":
            from urllib.parse import parse_qs, urlsplit
            target = parse_qs(urlsplit(self.path).query)["q"][0]
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif host == "www.youtube.com" and path == "/embed/x":  # an embedded video that contacts an ad service
            body, ctype = f"<!doctype html><img src=\"{U('stats.g.doubleclick.net', '/yt.gif')}\">".encode(), "text/html"
        elif host == "control.test":
            body, ctype = b"<!doctype html><title>control</title><p>control</p>", "text/html"
        elif host == "ib.adnxs.com" and path == "/ut.js":  # a listed service reads a canvas
            body, ctype = b"document.createElement('canvas').toDataURL();", "application/javascript"
        elif host == "cdn.unknown.test" and path == "/x.js":  # an unlisted third party opens WebRTC
            body, ctype = b"try { new RTCPeerConnection(); } catch (e) {}", "application/javascript"
        else:
            body, ctype = b"/* ok */", "application/javascript"
        self.send_response(200)
        if host == "www.google.com" and path == "/search":
            self.send_header("Set-Cookie", "NID=SECRETVALUE; Domain=google.com; Path=/; Max-Age=15552000; Secure; SameSite=None")
        if host == "px.watcher.test":
            self.send_header("Set-Cookie", "wid=8f3a9c2e1b7d4a60; Max-Age=3600; Path=/; Secure; SameSite=None")
        if host == "ib.adnxs.com" and path == "/ut.js":
            self.send_header("Set-Cookie", "uuid2=SECRETVALUE; Max-Age=3600; Path=/; Secure; SameSite=None")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:  # the tab was closed before a slow answer arrived
            pass


def start_server(tmp: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "lens-e2e")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in HOSTS]), critical=False)
            .sign(key, hashes.SHA256()))
    (tmp / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                    serialization.PrivateFormat.TraditionalOpenSSL,
                                                    serialization.NoEncryption()))
    (tmp / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    http.server.ThreadingHTTPServer.request_queue_size = 256
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.handle_error = lambda *a: None  # a tab closed mid-request: not a test failure
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp / "cert.pem", tmp / "key.pem")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def rdp_install(path: Path):
    for _ in range(150):
        try:
            sock = socket.create_connection(("127.0.0.1", RDP_PORT), timeout=10)
            break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("Firefox debugger server did not start")
    buf = b""

    def recv():
        nonlocal buf
        while b":" not in buf:
            buf += sock.recv(65536)
        n, rest = buf.split(b":", 1)
        while len(rest) < int(n):
            rest += sock.recv(65536)
        buf = rest[int(n):]
        return json.loads(rest[:int(n)])

    def request(obj):
        data = json.dumps(obj).encode()
        sock.sendall(str(len(data)).encode() + b":" + data)
        while True:
            m = recv()
            if m.get("from") == obj["to"]:
                return m

    recv()
    root = request({"to": "root", "type": "getRoot"})
    res = request({"to": root["addonsActor"], "type": "installTemporaryAddon", "addonPath": str(path)})
    if "error" in res:
        raise RuntimeError(f"install failed: {res}")
    lst = request({"to": "root", "type": "listAddons"})
    return [a.get("warnings") for a in lst.get("addons", []) if a.get("id") == "lens@trackerwatch.github.io"]


def read_reports(control):
    control.goto(U("control.test", "/"), wait_until="load")
    control.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    return json.loads(control.text_content("#lens-reports"))


def search_scenario(ctx, control, browser: str, screenshot: str | None = None) -> list[str]:
    """Results page, then a click through the engine's redirect and a click on a link with a ping."""
    errors = []
    serp_url = U("www.google.com", "/search?q=private+words&ei=SECRET")
    tabs = []
    for link in ("#r1", "#r2"):
        tab = ctx.new_page()
        tab.goto(serp_url, wait_until="load")
        tab.wait_for_timeout(2000)
        tabs.append((tab, link))
    reports = read_reports(control)
    serps = [r for r in reports.values() if r.get("search")]
    if len(serps) != 2:
        return [f"expected 2 results pages, got {len(serps)}"]
    q = serps[0]["search"]
    if q["pings"] != 0:
        errors.append(f"background beacons counted as click pings: {q['pings']}")
    if q["links"] != {"total": 2, "ping": 1, "redirect": 1, "mousedown": 0}:
        errors.append(f"result links {q['links']}")
    if [x["name"] for x in q["params"]] != ["q", "ei"] or not q["params"][0]["isQuery"]:
        errors.append(f"search params {q['params']}")
    if screenshot and browser == "chromium":
        serp_tab = next(tid for tid, r in reports.items() if r.get("search"))
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
        popup = ctx.new_page()
        popup.set_viewport_size({"width": 380, "height": 900})
        popup.goto(f"chrome-extension://{sw.url.split('/')[2]}/popup.html?tabId={serp_tab}")
        popup.wait_for_timeout(800)
        popup.screenshot(path=screenshot.replace(".png", "-search.png"), full_page=True)
        popup.close()
    nid = [c for c in q["cookies"] if c["name"] == "NID"]
    if not nid or not nid[0]["purpose"] or "SECRET" in json.dumps(q):
        errors.append(f"engine cookies {q['cookies']}")
    for tab, link in tabs:
        tab.click(link)
        tab.wait_for_url("**/dest*", timeout=8000)
        tab.wait_for_timeout(1500)
    reports = read_reports(control)
    arrivals = {r["page"]["host"] + str(r["arrival"]["redirect"]): r["arrival"] for r in reports.values()
                if r.get("arrival")}
    via_redirect = arrivals.get("site.testTrue")
    via_link = arrivals.get("site.testFalse")
    if not via_redirect or via_redirect["engine"] != "google":
        errors.append(f"no arrival through the redirect: {arrivals}")
    if not via_link or via_link["engine"] != "google":
        errors.append(f"no arrival from the results page: {arrivals}")
    # Chromium sends hyperlink-auditing pings; Firefox does not by default (browser.send_pings).
    expected_ping = browser == "chromium"
    if via_link and via_link["ping"] != expected_ping:
        errors.append(f"ping on arrival {via_link['ping']} != {expected_ping}")
    dests = [r["page"] for r in reports.values() if r.get("arrival")]
    if any(o["entity"] == "Google" and any(x["service"].startswith("google") for x in o["services"])
           for d in dests for o in d["operators"]):
        errors.append("the engine's ping was counted as a contact of the destination page")
    if not (q.get("server") or {}).get("steps") or q.get("signedIn") is not False:
        errors.append(f"server guide or signed-in state: {q.get('server')} {q.get('signedIn')}")
    print(f"{browser}: search {q['links']} arrivals {sorted(arrivals)} ping={via_link and via_link['ping']}")
    return errors


def custom_banner_and_prerender(ctx, control, browser: str) -> list[str]:
    errors = []
    tab = ctx.new_page()
    tab.goto(U("site.test", "/es"), wait_until="load")
    tab.wait_for_timeout(2500)  # time for Chromium to prerender the next page
    tab.click("#pay")
    tab.wait_for_timeout(500)
    reports = read_reports(control)
    es = [r for r in reports.values() if r.get("consent") and r["consent"]["banners"] == ["Didomi"]]
    if not es or es[0]["consent"]["click"] != {"tool": "Didomi", "choice": "pay"}:
        errors.append(f"custom 'pay or accept' button not named: {[r.get('consent') for r in reports.values()]}")
    elif es[0]["consent"].get("payOrAccept") is not True:
        errors.append(f"'accept or pay' banner not recognised: {es[0]['consent']}")
    unknown = ctx.new_page()
    unknown.goto(U("site.test", "/unknown"), wait_until="load")
    unknown.wait_for_timeout(800)
    unknown.click("#yes")
    unknown.wait_for_timeout(2500)
    reports = read_reports(control)
    prev = [r["consent"].get("previous") for r in reports.values() if r.get("consent") and r["consent"].get("previous")]
    if prev != [{"tool": None, "choice": "accept"}]:
        errors.append(f"answer to an unknown banner not kept across the reload: {prev}")
    tab.click("#next")
    tab.wait_for_url("**/prerendered", timeout=8000)
    tab.wait_for_timeout(1500)
    activated = tab.evaluate("() => performance.getEntriesByType('navigation')[0].activationStart > 0")
    reports = read_reports(control)
    pre = [r["page"] for r in reports.values() if r.get("page") and r["page"]["trackingBefore"] == 2
           and not r.get("consent", {}).get("banners") and r["page"]["thirdPartyDomains"] == 2]
    if not pre:
        errors.append(f"prerendered page not reported (activated={activated}): "
                      f"{[(r['page']['host'], r['page']['trackingBefore'], r['page']['thirdPartyDomains']) for r in reports.values() if r.get('page')]}")
    print(f"{browser}: custom banner ok={not errors}, prerender activated={activated}")
    return errors


def clean_scenario(ctx, control, browser: str, screenshot: str | None = None) -> list[str]:
    """Clean mode on (full list + parameter removal): tracking services stopped, gclid removed from the address."""
    errors = []
    control.goto(U("control.test", "/?clean=full"), wait_until="load")
    control.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    tab = ctx.new_page()
    tab.goto(U("site.test", "/?gclid=SECRET&keep=1"), wait_until="load")
    tab.wait_for_timeout(1500)
    if "gclid" in tab.url or "keep=1" not in tab.url:
        errors.append(f"tracking parameter not removed: {tab.url}")
    reports = read_reports(control)
    rep = [r for r in reports.values() if isinstance(r, dict) and r.get("page") and r["page"]["host"] == "site.test"
           and (r.get("clean") or {}).get("blocking") == "full"]
    if not rep:
        errors.append("no site.test report with clean mode on")
    else:
        pr = rep[-1]["page"]["protection"]
        if pr["stopped"] < 2 or rep[-1]["page"]["trackingBefore"] != 0:
            errors.append(f"clean mode did not stop tracking services: {pr} stopped={rep[-1]['page']['stopped']}")
    control.goto(U("control.test", "/?clean=extended"), wait_until="load")  # our full list + EasyPrivacy
    control.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    tab.goto(U("site.test", "/"), wait_until="load")
    tab.wait_for_timeout(1500)
    ext = [r for r in read_reports(control).values() if isinstance(r, dict) and (r.get("clean") or {}).get("blocking") == "extended"
           and r.get("page") and r["page"]["host"] == "site.test"]
    if not ext or ext[-1]["page"]["protection"]["stopped"] < 2:
        errors.append(f"extended clean mode: {[(r.get('clean'), r['page']['protection']) for r in ext]}")
    errors += siteonly_scenario(ctx, control, tab, browser, screenshot)
    control.goto(U("control.test", "/?clean=off"), wait_until="load")
    control.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    tab.close()
    print(f"{browser}: clean mode ok={not errors}")
    return errors


def siteonly_scenario(ctx, control, tab, browser: str, screenshot: str | None = None) -> list[str]:
    """"Site only": other sites' scripts stopped and listed (not the YouTube frame, allowed by default, nor the
    trackers, stopped by the lists); allowing one from the panel lets it load on that site."""
    errors = []

    def site_report():
        tab.goto(U("site.test", "/"), wait_until="load")
        tab.wait_for_timeout(1500)
        reps = [r for r in read_reports(control).values() if isinstance(r, dict) and r.get("page")
                and r["page"]["host"] == "site.test" and (r.get("clean") or {}).get("blocking") == "siteonly"]
        return reps[-1] if reps else None

    control.goto(U("control.test", "/?clean=siteonly"), wait_until="load")
    control.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    rep = site_report()
    so = (rep or {}).get("clean", {}).get("siteonly")
    blocked = [b["domain"] for b in (so or {}).get("blocked", [])]
    if not so or "cdn.unknown.test" not in blocked or "youtube.com" in blocked or "cookielaw.org" in blocked or "doubleclick.net" in blocked \
            or rep["page"]["trackingBefore"] != 0:
        errors.append(f"site only: blocked={blocked} trackingBefore={rep and rep['page']['trackingBefore']}")
    if screenshot and browser == "chromium":
        tab_id = next(tid for tid, r in read_reports(control).items() if (isinstance(r, dict) and r.get("page")
                      and r["page"]["host"] == "site.test" and ((r.get("clean") or {}).get("siteonly") or {}).get("blocked")))
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
        ext_id = sw.url.split("/")[2]
        shot = ctx.new_page()
        shot.set_viewport_size({"width": 380, "height": 900})
        shot.goto(f"chrome-extension://{ext_id}/popup.html?tabId={tab_id}&url=" + U("site.test", "/"))
        shot.wait_for_timeout(800)
        shot.evaluate("document.querySelector('details[data-id=protection]').open = true")
        shot.screenshot(path=screenshot.replace(".png", "-siteonly.png"), full_page=True)
        shot.set_viewport_size({"width": 800, "height": 900})
        shot.goto(f"chrome-extension://{ext_id}/options.html")
        shot.wait_for_timeout(800)
        shot.screenshot(path=screenshot.replace(".png", "-siteonly-options.png"), full_page=True)
        shot.close()
    control.goto(U("control.test", "/?allow=site.test:cdn.unknown.test"), wait_until="load")
    control.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    rep = site_report()
    so = (rep or {}).get("clean", {}).get("siteonly") or {}
    blocked = [b["domain"] for b in so.get("blocked", [])]
    if "cdn.unknown.test" in blocked or so.get("allowedHere") != ["cdn.unknown.test"]:
        errors.append(f"site only, after allowing cdn.unknown.test: {so}")
    print(f"{browser}: site only ok={not errors} (stopped: {', '.join(blocked)})")
    return errors


def ads_scenario(ctx, control, browser: str) -> list[str]:
    """"Block ads" on its own: the ad network stopped by the browser, the ad slot hidden, the rest untouched;
    off again: the slot shows."""
    errors = []
    control.goto(U("control.test", "/?clean=off&ads=on"), wait_until="load")
    control.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    tab = ctx.new_page()
    tab.goto(U("site.test", "/ads"), wait_until="load")
    tab.wait_for_timeout(1500)
    shown = tab.evaluate("[getComputedStyle(document.getElementById('slot')).display, "
                         "getComputedStyle(document.getElementById('text')).display]")
    reps = [r for r in read_reports(control).values() if isinstance(r, dict) and r.get("page")
            and r["page"]["host"] == "site.test" and (r.get("clean") or {}).get("ads")]
    stopped = reps[-1]["page"]["stopped"]["client"] if reps else None
    if shown != ["none", "block"] or not stopped:
        errors.append(f"ads: display={shown} stopped by client={stopped} page={reps and {k: reps[-1]['page'][k] for k in ('stopped', 'thirdPartyDomains')}}")
    control.goto(U("control.test", "/?clean=off"), wait_until="load")
    control.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    tab.goto(U("site.test", "/ads"), wait_until="load")
    tab.wait_for_timeout(800)
    if tab.evaluate("getComputedStyle(document.getElementById('slot')).display") == "none":
        errors.append("ads off: the slot is still hidden")
    tab.close()
    print(f"{browser}: ads ok={not errors}")
    return errors


def learn_scenario(ctx, control, browser: str) -> list[str]:
    """Step 4: learning on and trackers blocked; a third party on no list gets an identifier cookie on three
    sites, is learned, and is blocked on the next visit."""
    errors = []
    control.goto(U("control.test", "/?learn=on&clean=full"), wait_until="load")
    control.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    tab = ctx.new_page()
    for host in ("site.test", "a.test", "b.test"):
        tab.goto(U(host, "/learn"), wait_until="load")
        tab.wait_for_timeout(1500)
    tab.goto(U("site.test", "/learn"), wait_until="load")
    tab.wait_for_timeout(1500)
    reps = [r for r in read_reports(control).values() if isinstance(r, dict) and r.get("page")
            and r["page"]["host"] == "site.test" and ((r.get("clean") or {}).get("learn") or {}).get("here")]
    here = reps[-1]["clean"]["learn"]["here"] if reps else []
    if not here or here[0]["domain"] != "px.watcher.test" or not here[0]["stopped"]:
        errors.append(f"learn: here={here}")
    control.goto(U("control.test", "/?clean=off"), wait_until="load")
    control.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    tab.close()
    print(f"{browser}: learn ok={not errors}")
    return errors


def referrer_scenario(ctx, control, browser: str) -> list[str]:
    """A result opened in a new tab: the results page was not seen, the Referer still names the engine."""
    tab = ctx.new_page()
    tab.goto(U("site.test", "/from-search"), wait_until="load", referer="https://www.google.com/")
    tab.wait_for_timeout(1000)
    arr = [r.get("arrival") for r in read_reports(control).values() if isinstance(r, dict) and r.get("page")
           and r["page"]["host"] == "site.test" and (r.get("arrival") or {}).get("referrer")]
    tab.close()
    ok = bool(arr) and arr[-1]["engineName"] == "Google"
    print(f"{browser}: arrival from the Referer ok={ok}")
    return [] if ok else [f"arrival from the Referer: {arr}"]


def panel_scenario(ctx, control, tab_id: str) -> list[str]:
    """Chromium only (Playwright cannot open moz-extension pages): the panel's sections open one at a time, and
    Open all / Close all follows the real state and survives reopening the panel."""
    errors = []
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
    url = f"chrome-extension://{sw.url.split('/')[2]}/popup.html?tabId={tab_id}&url=" + U("site.test", "/")
    pop = ctx.new_page()
    pop.goto(url)
    pop.wait_for_timeout(800)
    state = lambda: pop.evaluate("[...document.querySelectorAll('details.sec')].map(d => d.open)")  # noqa: E731
    label = lambda: pop.text_content("#toggle-all")  # noqa: E731
    pop.click("#toggle-all") if any(state()) else None  # start from everything closed
    pop.wait_for_timeout(100)
    if any(state()) or label() != "Open all":
        errors.append(f"close all: {state()} {label()}")
    summaries = pop.query_selector_all("details.sec > summary")
    summaries[0].click()
    pop.wait_for_timeout(100)
    summaries[2].click()
    pop.wait_for_timeout(100)
    st = state()
    if st[0] or not st[2] or sum(st) != 1 or label() != "Close all":
        errors.append(f"one section at a time: {st} {label()}")
    pop.click("#toggle-all")
    pop.wait_for_timeout(100)
    if any(state()) or label() != "Open all":
        errors.append(f"close all after one open: {state()} {label()}")
    pop.click("#toggle-all")
    pop.wait_for_timeout(100)
    if not all(state()) or label() != "Close all":
        errors.append(f"open all: {state()} {label()}")
    summaries = pop.query_selector_all("details.sec > summary")
    summaries[1].click()  # a click on an open section closes only that one
    pop.wait_for_timeout(100)
    st = state()
    if st[1] or sum(st) != len(st) - 1:
        errors.append(f"closing one of all open: {st}")
    pop.reload()
    pop.wait_for_timeout(800)
    if state() != st:
        errors.append(f"not remembered after reopening: {state()} != {st}")
    pop.close()
    # while a page is still loading the panel redraws itself every second: clicks must survive the redraws
    slow = ctx.new_page()
    slow.goto(U("site.test", "/loading"), wait_until="domcontentloaded")
    slow.wait_for_timeout(1200)
    reports = read_reports(control)
    loading = [tid for tid, r in reports.items() if isinstance(r, dict) and r.get("loading")]
    if not loading:
        errors.append("no loading page to test the redraws")
    else:
        pop = ctx.new_page()
        pop.goto(f"chrome-extension://{sw.url.split('/')[2]}/popup.html?tabId={loading[0]}&url=" + U("site.test", "/loading"))
        pop.wait_for_timeout(600)
        if any(state()):
            pop.click("#toggle-all")
        pop.query_selector_all("details.sec > summary")[1].click()
        pop.wait_for_timeout(2500)  # two redraws
        st = state()
        if not st[1] or sum(st) != 1 or label() != "Close all":
            errors.append(f"a click undone by the redraw: {st} {label()}")
        pop.close()
    slow.close()
    print(f"chromium: panel sections ok={not errors}")
    return errors


def autoreject_scenario(ctx, control, browser: str, screenshot: str | None = None) -> list[str]:
    """"Reject banners" on: the OneTrust banner is rejected for the visitor (its reject handler runs), Lens shows its
    notice and the click is not counted as the visitor's; an "accept or pay" banner is left unanswered."""
    errors = []
    control.goto(U("control.test", "/?autoreject=on"), wait_until="load")
    control.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    tab = ctx.new_page()
    tab.goto(U("site.test", "/?auto=1"), wait_until="load")
    tab.wait_for_timeout(2500)
    notice = tab.evaluate("Boolean(document.getElementById('trackerwatch-lens-notice'))")
    if screenshot and browser == "chromium":
        tab.screenshot(path=screenshot.replace(".png", "-notice.png"))
    reps = [r for r in read_reports(control).values() if isinstance(r, dict) and r.get("page")
            and r["page"]["host"] == "site.test" and (r.get("consent") or {}).get("auto")]
    auto = reps[-1]["consent"]["auto"] if reps else None
    if auto != {"tool": "OneTrust", "outcome": "rejected"} or not notice or reps[-1]["page"]["interaction"] is not None             or "facebook.net" not in reps[-1]["page"]["trackingNewAfter"] + [x["service"] for o in reps[-1]["page"]["operators"] for x in o["services"]]:
        errors.append(f"auto reject: auto={auto} notice={notice} interaction={reps and reps[-1]['page']['interaction']}")
    tab.goto(U("site.test", "/es"), wait_until="load")
    tab.wait_for_timeout(8000)  # the last look is at 7 s
    reps = [r for r in read_reports(control).values() if isinstance(r, dict) and r.get("page")
            and r["page"]["host"] == "site.test" and (r.get("consent") or {}).get("banners") == ["Didomi"]]
    auto = reps[-1]["consent"].get("auto") if reps else None
    if auto != {"tool": "Didomi", "outcome": "payOrAccept"}:
        errors.append(f"accept or pay must stay unanswered: {auto}")
    control.goto(U("control.test", "/?autoreject=off"), wait_until="load")
    control.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    tab.close()
    print(f"{browser}: reject banners ok={not errors}")
    return errors


def launch(p, browser: str, udd: Path):
    if browser == "chromium":
        ext = EXT / "dist" / "chrome-test"
        ctx = p.chromium.launch_persistent_context(
            str(udd), channel="chromium", headless=True, ignore_https_errors=True,
            args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}",
                  "--host-resolver-rules=MAP * 127.0.0.1", "--ignore-certificate-errors"])
        return ctx, None
    ctx = p.firefox.launch_persistent_context(
        str(udd), headless=True, ignore_https_errors=True, args=["-start-debugger-server", str(RDP_PORT)],
        firefox_user_prefs={"devtools.debugger.remote-enabled": True, "devtools.debugger.prompt-connection": False,
                            "devtools.chrome.enabled": True, "network.dns.localDomains": ",".join(HOSTS)})
    warnings = rdp_install(EXT / "dist" / "firefox-test")
    return ctx, warnings


def run(p, browser: str, screenshot: str | None) -> list[str]:
    errors = []
    udd = Path(tempfile.mkdtemp(prefix="lens-e2e-"))
    ctx, warnings = launch(p, browser, udd)
    if warnings and any(warnings):
        errors.append(f"manifest warnings: {warnings}")
    time.sleep(1.5)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(U("control.test", "/?mytests=on&summary=on"), wait_until="load")  # turn on "Your own banner test"
    page.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    page.goto(U("site.test", "/"), wait_until="load")
    page.wait_for_timeout(1200)
    page.click("#onetrust-reject-all-handler")
    page.wait_for_timeout(2500)  # the banner test is written at most every 1.5 s
    control = ctx.new_page()
    reports = read_reports(control)
    match = [(tid, r) for tid, r in reports.items() if r.get("page") and r["page"]["host"] == "site.test"]
    if not match:
        errors.append(f"no report for site.test: {list(reports)}")
        ctx.close()
        return errors
    tab_id, rep = match[0]
    s = rep["page"]
    got = {"trackingBefore": s["trackingBefore"], "trackingNewAfter": s["trackingNewAfter"],
           "thirdPartyDomains": s["thirdPartyDomains"], "band": s["band"]}
    if got != EXPECTED:
        errors.append(f"report {got} != expected {EXPECTED}")
    if s["interaction"] is None or s["interaction"]["kind"] != "click":
        errors.append(f"interaction not recorded: {s['interaction']}")
    consent = rep.get("consent") or {}
    if consent.get("banners") != ["OneTrust"] or consent.get("click") != {"tool": "OneTrust", "choice": "reject"} \
            or "OneTrust" not in consent.get("toolsContacted", []):
        errors.append(f"consent not recorded as expected: {consent}")
    ids = [r["id"] for r in rep.get("reasons") or []]
    if "whyClicked" not in ids or ids[-1] != "whyAuctions" or "whyRecognised" in ids:
        errors.append(f"reasons: {ids}")
    told = rep.get("told") or {}
    if not (told.get("self") or {}).get("userAgent"):
        errors.append(f"no self description: {told.get('self')}")
    if told.get("params") != {"gclid": 1}:
        errors.append(f"tracking parameters {told.get('params')} != {{'gclid': 1}}")
    if told.get("thirdWithCookies", 0) < 1 or "adnxs.com" not in told.get("cookieServices", {}):
        errors.append(f"third-party cookie not seen: {told.get('thirdWithCookies')} {told.get('cookieServices')}")
    if "SECRET" in json.dumps(reports):
        errors.append("a query string leaked into the report")
    gtm = [x for o in s["operators"] for x in o["services"] if x["service"] == "googletagmanager.com"]
    if not gtm or gtm[0]["tracking"]:
        errors.append("tag manager should be listed and not counted")
    mine = (rep.get("mytests") or {}).get("view") or {}
    runs = (mine.get("reject") or {}).get("runs") or []
    if len(runs) != 1 or not runs[0]["clean"] or runs[0]["after"] != EXPECTED["trackingNewAfter"]             or mine.get("next") != "accept" or rep["mytests"]["site"] != "site.test":
        errors.append(f"own banner test not recorded as expected: {mine}")
    elif "http" in json.dumps(runs):
        errors.append("an address leaked into the stored banner test")
    adnxs = [x for o in s["operators"] for x in o["services"] if x["service"] == "adnxs.com"]
    if not adnxs or not any("canvas" in ph for ph in adnxs[0]["phrases"]):
        errors.append(f"canvas reading not attributed to adnxs.com: {adnxs}")
    if s.get("behavioursOther") != [{"domain": "cdn.unknown.test", "kinds": ["webrtc_connection"]}]:
        errors.append(f"unlisted script behaviour: {s.get('behavioursOther')}")
    print(f"{browser}: {json.dumps(got)} size={s['bytes']}")
    if s.get("frames") != [{"site": "youtube.com", "services": ["doubleclick.net"], "requests": 1}]:
        errors.append(f"embedded frame attribution: {s.get('frames')}")
    errors += search_scenario(ctx, control, browser, screenshot)
    b = s.get("blocking") or {}
    if (b.get("full") or {}).get("services") != 3 or (b.get("verified") or {}).get("requests", 0) < 1:
        errors.append(f"blocking simulation: {b}")
    journeys = [r["journey"] for r in read_reports(control).values() if r.get("journey")]
    if not any(j["previous"] == "google.com" and j["rows"][-1].get("current") for j in journeys):
        errors.append(f"no journey from google.com: {journeys}")
    elif "SECRET" in json.dumps(journeys) or "private" in json.dumps(journeys):
        errors.append("a query leaked into the journey")
    errors += custom_banner_and_prerender(ctx, control, browser)
    errors += clean_scenario(ctx, control, browser, screenshot)
    errors += ads_scenario(ctx, control, browser)
    errors += learn_scenario(ctx, control, browser)
    errors += referrer_scenario(ctx, control, browser)
    errors += autoreject_scenario(ctx, control, browser, screenshot)
    if browser == "chromium":
        errors += panel_scenario(ctx, control, tab_id)
    if screenshot and browser == "chromium":
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
        ext_id = sw.url.split("/")[2]
        popup = ctx.new_page()
        popup.set_viewport_size({"width": 380, "height": 900})
        popup.goto(f"chrome-extension://{ext_id}/popup.html?tabId={tab_id}&url=" + U("site.test", "/"))
        popup.wait_for_timeout(800)
        popup.screenshot(path=screenshot, full_page=True)
        options = ctx.new_page()
        options.set_viewport_size({"width": 800, "height": 900})
        options.goto(f"chrome-extension://{ext_id}/options.html")
        options.wait_for_timeout(800)
        options.screenshot(path=screenshot.replace(".png", "-options.png"), full_page=True)
        print("screenshot:", screenshot)
    page.goto(U("control.test", "/"), wait_until="load")  # a page is counted in "your week" when it is left
    page.wait_for_timeout(1500)
    week = read_reports(control).get("_summary") or {}
    if week.get("pages", 0) < 2 or "Google" not in [o["name"] for o in week.get("operators", [])]:
        errors.append(f"your week not counted: {week}")
    elif "site.test" in json.dumps(week):
        errors.append("a site name leaked into your week")
    ctx.close()
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", default="all", choices=["chromium", "firefox", "all"])
    ap.add_argument("--screenshot")
    args = ap.parse_args()
    built = subprocess.run([sys.executable, str(EXT / "tools" / "build.py"), "--test"])
    if built.returncode:
        return built.returncode
    start_server(Path(tempfile.mkdtemp(prefix="lens-cert-")))
    failures = {}
    with sync_playwright() as p:
        for browser in (["chromium", "firefox"] if args.browser == "all" else [args.browser]):
            try:
                errs = run(p, browser, args.screenshot)
            except Exception as e:  # noqa: BLE001
                errs = [repr(e)[:500]]
            if errs:
                failures[browser] = errs
    for browser, errs in failures.items():
        print(f"FAIL {browser}:", *errs, sep="\n  ")
    print("e2e ok" if not failures else "e2e FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
