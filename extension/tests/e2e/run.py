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
         "cdn.unknown.test", "connect.facebook.net"]
U = lambda host, path: f"https://{host}:{PORT}{path}"  # noqa: E731

PAGE = f"""<!doctype html><meta charset="utf-8"><title>e2e</title>
<script src="{U('www.googletagmanager.com', '/gtm.js')}"></script>
<script src="{U('ib.adnxs.com', '/ut.js')}"></script>
<script src="{U('cdn.unknown.test', '/x.js')}"></script>
<img src="{U('stats.g.doubleclick.net', '/collect?gclid=SECRET')}">
<button id="go">Accept</button>
<script>
document.getElementById('go').addEventListener('click', () => {{
  const s = document.createElement('script'); s.src = "{U('connect.facebook.net', '/sdk.js')}"; document.head.appendChild(s);
}});
</script>"""

EXPECTED = {"trackingBefore": 2, "trackingNewAfter": ["facebook.net"], "thirdPartyDomains": 5, "band": "A"}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        path = self.path.split("?")[0]
        if host == "site.test" and path == "/":
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
        elif host == "control.test":
            body, ctype = b"<!doctype html><title>control</title><p>control</p>", "text/html"
        else:
            body, ctype = b"/* ok */", "application/javascript"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


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
    page.goto(U("site.test", "/"), wait_until="load")
    page.wait_for_timeout(1200)
    page.click("#go")
    page.wait_for_timeout(1200)
    control = ctx.new_page()
    control.goto(U("control.test", "/"), wait_until="load")
    control.wait_for_selector("#lens-reports", state="attached", timeout=10000)
    reports = json.loads(control.text_content("#lens-reports"))
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
    if "SECRET" in json.dumps(reports):
        errors.append("a query string leaked into the report")
    gtm = [x for o in s["operators"] for x in o["services"] if x["service"] == "googletagmanager.com"]
    if not gtm or gtm[0]["tracking"]:
        errors.append("tag manager should be listed and not counted")
    print(f"{browser}: {json.dumps(got)} size={s['bytes']}")
    if screenshot and browser == "chromium":
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
        ext_id = sw.url.split("/")[2]
        popup = ctx.new_page()
        popup.set_viewport_size({"width": 380, "height": 900})
        popup.goto(f"chrome-extension://{ext_id}/popup.html?tabId={tab_id}&url=" + U("site.test", "/"))
        popup.wait_for_timeout(800)
        popup.screenshot(path=screenshot, full_page=True)
        print("screenshot:", screenshot)
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
