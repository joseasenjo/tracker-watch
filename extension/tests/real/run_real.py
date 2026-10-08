"""Lens on real news sites (Chromium, test build): speed with everything on, Reject banners, learning.

    python extension/tests/real/run_real.py [--sites N] [--wait SECONDS]

Three phases, each in a fresh, empty browser profile (deleted afterwards):
  off       Lens only observes (the reference);
  all       Block trackers (extended) + Block ads + Reject banners + Learn trackers;
  siteonly  Block trackers (site only) + Block ads + Reject banners.
Every site is opened once per phase and left alone for --wait seconds (Reject banners looks at 0.3, 1.5, 4 and
7 s). Measured in the page: DOMContentLoaded and load times, total time of long tasks (the page frozen for more
than 50 ms at a time), number of requests. From Lens: tracking services counted, stopped, what Reject banners did.

One visit per site and phase: an indication, not a benchmark (ads change on every visit). Results:
extension/tests/real/results-<date>.json and a summary on screen. The browser identifies itself as headless
Chromium; a site that refuses it is reported as such. Needs the internet; only control.test is local.
"""
from __future__ import annotations

import argparse
import datetime
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "e2e"))
import run as e2e  # noqa: E402  (local control page and test build)

SITES = [
    "https://elpais.com/", "https://www.20minutos.es/", "https://www.elmundo.es/", "https://www.marca.com/",
    "https://www.abc.es/", "https://www.lavanguardia.com/", "https://www.eldiario.es/", "https://as.com/",
    "https://www.bbc.co.uk/news", "https://www.theguardian.com/uk", "https://www.spiegel.de/",
    "https://www.lemonde.fr/", "https://edition.cnn.com/",
]
PHASES = {
    "off": "/?clean=off&ads=off&autoreject=off",
    "all": "/?clean=extended&ads=on&learn=on&autoreject=on",
    "siteonly": "/?clean=siteonly&ads=on&autoreject=on",
}
# long tasks are only reported to an observer: record them from the start of every page
LONGTASKS = """(() => { window.__lensLong = 0; window.__lensLongN = 0;
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) { window.__lensLong += e.duration; window.__lensLongN++; } })
    .observe({ type: 'longtask', buffered: true }); } catch (e) {} })();"""
METRICS = """(() => { const n = performance.getEntriesByType('navigation')[0] || {};
  return { dcl: Math.round(n.domContentLoadedEventEnd || 0), load: Math.round(n.loadEventEnd || 0),
    longTasksMs: Math.round(window.__lensLong || 0), longTasks: window.__lensLongN || 0,
    requests: performance.getEntriesByType('resource').length, notice: Boolean(document.getElementById('trackerwatch-lens-notice')) }; })()"""


def launch(p, udd: Path):
    ext = e2e.EXT / "dist" / "chrome-test"
    return p.chromium.launch_persistent_context(
        str(udd), channel="chromium", headless=True, ignore_https_errors=True, viewport={"width": 1280, "height": 800},
        args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}",
              "--host-resolver-rules=MAP control.test 127.0.0.1", "--ignore-certificate-errors"])


def host_of(url: str) -> str:
    return url.split("//", 1)[1].split("/", 1)[0]


def run_phase(p, phase: str, sites: list[str], wait: float, shots: Path | None = None) -> dict:
    udd = Path(tempfile.mkdtemp(prefix=f"lens-real-{phase}-"))
    ctx = launch(p, udd)
    out = {"sites": {}}
    try:
        time.sleep(1.5)
        ctx.add_init_script(LONGTASKS)
        control = ctx.pages[0] if ctx.pages else ctx.new_page()
        control.goto(e2e.U("control.test", PHASES[phase]), wait_until="load")
        control.wait_for_selector("#lens-reports", state="attached", timeout=15000)
        for url in sites:
            tab = ctx.new_page()
            row: dict = {"url": url}
            try:
                resp = tab.goto(url, wait_until="load", timeout=45000)
                row["status"] = resp.status if resp else None
            except Exception as exc:  # noqa: BLE001  (timeouts and refusals are results too)
                row["error"] = type(exc).__name__ + ": " + str(exc).splitlines()[0][:160]
            tab.wait_for_timeout(int(wait * 1000))
            try:
                row.update(tab.evaluate(METRICS))
            except Exception as exc:  # noqa: BLE001
                row["metricsError"] = str(exc)[:120]
            if shots and phase == "all":  # what the visitor sees after Reject banners had its chance
                try:
                    tab.screenshot(path=str(shots / f"{host_of(url).replace('.', '-')}.png"))
                except Exception:  # noqa: BLE001
                    pass
            reports = e2e.read_reports(control)
            host = host_of(tab.url) if tab.url.startswith("http") else host_of(url)
            mine = [r for r in reports.values() if isinstance(r, dict) and r.get("page") and r["page"]["host"] == host]
            if mine:
                r = mine[-1]
                s, c = r["page"], r.get("consent") or {}
                row.update(trackingBefore=s["trackingBefore"], trackingTotal=s["trackingTotal"],
                           stopped=s["stopped"], thirdPartyDomains=s["thirdPartyDomains"],
                           banners=c.get("banners"), payOrAccept=c.get("payOrAccept"), auto=c.get("auto"),
                           siteonlyStopped=len(((r.get("clean") or {}).get("siteonly") or {}).get("blocked", [])))
            tab.close()
            out["sites"][url] = row
            print(f"  {phase:8} {host:24} load={row.get('load')} ms long={row.get('longTasksMs')} ms "
                  f"trackers={row.get('trackingBefore')} auto={(row.get('auto') or {}).get('outcome')}", flush=True)
        out["learn"] = e2e.read_reports(control).get("_learn")
    finally:
        ctx.close()
        shutil.rmtree(udd, ignore_errors=True)
    return out


def summary(results: dict) -> dict:
    def med(phase, key):
        vals = [r[key] for r in results[phase]["sites"].values() if isinstance(r.get(key), (int, float)) and not r.get('error')]
        return round(statistics.median(vals)) if vals else None
    out = {phase: {k: med(phase, k) for k in ("dcl", "load", "longTasksMs", "requests", "trackingBefore")}
           for phase in results}
    autos = [r.get("auto") for r in results.get("all", {}).get("sites", {}).values()]
    out["rejectBanners"] = {k: sum(1 for a in autos if a and a["outcome"] == k) for k in ("rejected", "payOrAccept", "noReject")}
    out["rejectBanners"]["nothing"] = sum(1 for a in autos if not a)
    learn = (results.get("all") or {}).get("learn") or {}
    out["learned"] = [d["domain"] for d in learn.get("learned", [])]
    out["watching"] = learn.get("watching")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", type=int, default=len(SITES))
    ap.add_argument("--wait", type=float, default=8.0)
    ap.add_argument("--only", help="comma-separated parts of site addresses to keep (e.g. guardian,spiegel)")
    ap.add_argument("--phases", default=",".join(PHASES), help="comma-separated phases to run")
    ap.add_argument("--shots", help="folder for a screenshot of each site in the 'all' phase")
    args = ap.parse_args()
    import subprocess
    if subprocess.run([sys.executable, str(e2e.EXT / "tools" / "build.py"), "--test", "--browser", "chrome"]).returncode:
        return 1
    srv = e2e.start_server(Path(tempfile.mkdtemp(prefix="lens-real-srv-")))
    results = {}
    try:
        with sync_playwright() as p:
            sites = [u for u in SITES if not args.only or any(x in u for x in args.only.split(","))][: args.sites]
            for phase in args.phases.split(","):
                print(f"phase {phase}", flush=True)
                shots = Path(args.shots) if args.shots else None
                if shots:
                    shots.mkdir(parents=True, exist_ok=True)
                results[phase] = run_phase(p, phase, sites, args.wait, shots)
    finally:
        srv.shutdown()
    report = {"date": datetime.datetime.now().isoformat(timespec="seconds"), "wait_s": args.wait,
              "note": "one visit per site and phase in a fresh profile; an indication, not a benchmark",
              "summary": summary(results), "phases": results}
    path = HERE / (f"results-{datetime.date.today().isoformat()}" + (f"-{args.only.replace(',', '-')}" if args.only else "") + ".json")
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report["summary"], indent=1))
    print("written", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
