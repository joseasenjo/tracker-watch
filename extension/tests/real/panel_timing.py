"""How long the panel takes to show its number while a heavy real page is still loading (Chromium, test build).

    python extension/tests/real/panel_timing.py [URL] [--after SECONDS] [--times N]

Opens the page, waits --after seconds, then opens the panel (as a tab, like the e2e tests) N times one after
another and prints, for each, the time until the number appears and whether "Counting..." is shown.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "e2e"))
sys.path.insert(0, str(HERE))
import run as e2e  # noqa: E402
from run_real import launch  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default="https://www.elmundo.es/")
    ap.add_argument("--after", type=float, default=2.0)
    ap.add_argument("--times", type=int, default=4)
    args = ap.parse_args()
    srv = e2e.start_server(Path(tempfile.mkdtemp(prefix="lens-timing-srv-")))
    udd = Path(tempfile.mkdtemp(prefix="lens-timing-"))
    try:
        with sync_playwright() as p:
            ctx = launch(p, udd)
            time.sleep(1.5)
            control = ctx.pages[0] if ctx.pages else ctx.new_page()
            reports = e2e.read_reports(control)
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            ext_id = sw.url.split("/")[2]
            page = ctx.new_page()
            page.goto(args.url, wait_until="commit", timeout=45000)
            page.wait_for_timeout(int(args.after * 1000))
            reports = e2e.read_reports(control)
            host = args.url.split("//", 1)[1].split("/", 1)[0]
            tab_id = next(t for t, r in reports.items() if isinstance(r, dict) and r.get("page") and r["page"]["host"] == host)
            for i in range(args.times):
                pop = ctx.new_page()
                t0 = time.perf_counter()
                pop.goto(f"chrome-extension://{ext_id}/popup.html?tabId={tab_id}&url={args.url}")
                first_paint = pop.evaluate("Boolean(document.querySelector('#app').textContent.trim())")
                pop.wait_for_selector(".big", timeout=30000)
                ms = (time.perf_counter() - t0) * 1000
                number = pop.text_content(".big")
                counting = pop.evaluate("Boolean(document.querySelector('.big.counting'))")
                badge = sw.evaluate(f"chrome.action.getBadgeText({{tabId: {tab_id}}})")
                print(f"open {i + 1}: number {number} after {ms:.0f} ms; something on screen at once: {first_paint}; "
                      f"counting: {counting}; toolbar badge: {badge!r}", flush=True)
                pop.close()
                page.wait_for_timeout(6000)
            ctx.close()
    finally:
        srv.shutdown()
        shutil.rmtree(udd, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
