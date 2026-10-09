"""Store screenshots (1280x800) of the real extension on a real page.

    python extension/store/make_screenshots.py --site https://www.example-news.com/ --out extension/store/screenshots

Uses the same machinery as the end-to-end and real-site tests (extension/tests/e2e/run.py): the test build of the
extension in Chromium, a local control page that sets the extension's options, and the real site loaded with no clicks.
The test build differs from the release only by a hook the interface never shows. The panel is opened as an ordinary
page for that tab (popup.html?tabId=...), because a toolbar popup cannot be photographed from outside the browser; the
page and the panel are then composed side by side. Counts, names and buttons come from the extension itself.
"""
from __future__ import annotations

import argparse
import base64
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

EXT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXT / "tests" / "e2e"))
import run as e2e  # noqa: E402

SIZE = {"width": 1280, "height": 800}
PANEL_WIDTH = 400
SCENES = [
    # file, control-page options, caption
    ("01-panel", "/?clean=off&ads=off&autoreject=off", "See which trackers a page contacts before you click anything"),
    ("02-blocking", "/?clean=extended&ads=on&autoreject=off", "Block trackers and ads with one click, and see what was stopped"),
]


def data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def compose(ctx, page_png: bytes, panel_png: bytes | None, caption: str, out: Path) -> None:
    """Page behind, panel on the right with a shadow (when there is one), a one-line caption at the bottom."""
    panel = "" if panel_png is None else f"""<img src="{data_uri(panel_png)}" style="position:absolute;right:28px;top:22px;width:{PANEL_WIDTH}px;max-height:690px;object-fit:cover;object-position:top;border-radius:10px;box-shadow:0 12px 40px rgba(0,0,0,.45)">"""
    html = f"""<html><body style="margin:0;width:1280px;height:800px;position:relative;overflow:hidden;font-family:system-ui,sans-serif">
<img src="{data_uri(page_png)}" style="position:absolute;left:0;top:0;width:1280px;height:800px;{'filter:brightness(.78)' if panel_png else ''}">
{panel}
<div style="position:absolute;left:0;right:0;bottom:0;padding:18px 32px;background:rgba(15,19,24,.92);color:#fff;font-size:26px;font-weight:600">{caption}</div>
</body></html>"""
    page = ctx.new_page()
    page.set_viewport_size(SIZE)
    page.set_content(html)
    page.wait_for_timeout(500)
    page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": 1280, "height": 800})
    page.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", required=True)
    parser.add_argument("--out", default=str(EXT / "store" / "screenshots"))
    parser.add_argument("--wait", type=float, default=12.0, help="seconds to let the page load before the panel is read")
    parser.add_argument("--raw", action="store_true", help="also keep the raw page and panel images")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if subprocess.run([sys.executable, str(EXT / "tools" / "build.py"), "--test", "--browser", "chrome"]).returncode:
        return 1
    srv = e2e.start_server(Path(tempfile.mkdtemp(prefix="lens-shots-srv-")))
    try:
        with sync_playwright() as p:
            for name, options, caption in SCENES:
                udd = Path(tempfile.mkdtemp(prefix="lens-shots-"))
                ext = EXT / "dist" / "chrome-test"
                ctx = p.chromium.launch_persistent_context(
                    str(udd), channel="chromium", headless=True, ignore_https_errors=True, viewport=SIZE, locale="en-US",
                    args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}",
                          "--host-resolver-rules=MAP control.test 127.0.0.1", "--ignore-certificate-errors"])
                try:
                    import time
                    time.sleep(1.5)
                    control = ctx.pages[0] if ctx.pages else ctx.new_page()
                    control.goto(e2e.U("control.test", options), wait_until="load")
                    control.wait_for_selector("#lens-reports", state="attached", timeout=15000)
                    tab = ctx.new_page()
                    tab.goto(args.site, wait_until="load", timeout=60000)
                    tab.wait_for_timeout(int(args.wait * 1000))
                    page_png = tab.screenshot()
                    url = tab.url
                    host = url.split("//", 1)[1].split("/", 1)[0]
                    reports = e2e.read_reports(control)
                    mine = [(tid, r) for tid, r in reports.items() if isinstance(r, dict) and r.get("page") and r["page"]["host"] == host]
                    if not mine:
                        raise SystemExit(f"no report for {host}")
                    tab_id, report = mine[-1]
                    sw = next(w for w in ctx.service_workers if w.url.startswith("chrome-extension://"))  # not the site's own
                    ext_id = sw.url.split("/")[2]
                    shot = ctx.new_page()
                    shot.set_viewport_size({"width": PANEL_WIDTH, "height": 1000})
                    shot.goto(f"chrome-extension://{ext_id}/popup.html?tabId={tab_id}&url={quote(url, safe='')}")
                    shot.wait_for_timeout(2500)
                    panel_png = shot.screenshot(full_page=True)
                    if args.raw:
                        (out / f"raw-{name}-page.png").write_bytes(page_png)
                        (out / f"raw-{name}-panel.png").write_bytes(panel_png)
                    compose(ctx, page_png, panel_png, caption, out / f"{name}.png")
                    print(f"{name}: {host} trackers before first click={report['page']['trackingBefore']} stopped={report['page']['stopped']}")
                    if name == SCENES[0][0]:
                        shot.set_viewport_size(SIZE)
                        shot.goto(f"chrome-extension://{ext_id}/options.html")
                        shot.wait_for_timeout(1500)
                        settings_png = shot.screenshot(clip={"x": 0, "y": 0, "width": 1280, "height": 800})
                        compose(ctx, settings_png, None, "Everything stays in your browser: you choose what is on, and it sends nothing anywhere",
                                out / "03-settings.png")
                finally:
                    ctx.close()
                    shutil.rmtree(udd, ignore_errors=True)
    finally:
        srv.shutdown()
    print(f"saved to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
