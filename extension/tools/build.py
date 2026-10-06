"""Package Tracker Watch Lens for each browser: one clean manifest per browser, same code.

    python extension/tools/build.py [--browser chrome|firefox|all] [--edition analysis] [--test]

Output: extension/dist/<browser>/ (or <browser>-test/), ready for "Load unpacked" (Chrome/Edge) or
about:debugging (Firefox). --test also enables a hook that hands all tab reports to a local test page;
test builds are never published. Editions "full" and "clean" (spec §10) arrive with the clean mode (M7).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

EXT = Path(__file__).resolve().parents[1]
DIST = EXT / "dist"
VERSION = json.loads((EXT / "package.json").read_text(encoding="utf-8"))["version"]
GECKO_ID = "lens@trackerwatch.github.io"


def manifest(browser: str, test: bool) -> dict:
    m = {
        "manifest_version": 3,
        "name": "__MSG_extName__",
        "description": "__MSG_extDescription__",
        "version": VERSION if VERSION != "0.0.0" else "0.1.0",
        "default_locale": "en",
        "permissions": ["webRequest", "storage", "cookies"],
        "host_permissions": ["<all_urls>"],
        "icons": {str(n): f"icons/icon-{n}.png" for n in (16, 32, 48, 128)},
        "action": {"default_popup": "popup.html", "default_title": "__MSG_extName__",
                   "default_icon": {str(n): f"icons/icon-{n}.png" for n in (16, 32)}},
        "content_scripts": [{"matches": ["<all_urls>"], "js": ["content.js"], "run_at": "document_start"}],
        "content_security_policy": {"extension_pages": "script-src 'self'; object-src 'none'"},
    }
    if browser == "chrome":
        m["background"] = {"service_worker": "background.js", "type": "module"}
        m["minimum_chrome_version"] = "121"
    else:
        m["background"] = {"scripts": ["background.js"], "type": "module"}
        m["browser_specific_settings"] = {"gecko": {
            "id": GECKO_ID, "strict_min_version": "128.0",
            "data_collection_permissions": {"required": ["none"]}}}
    if test:
        m["name"] += " (test build)"
        m["content_scripts"].append({"matches": ["*://control.test/*"], "js": ["testhook.js"], "run_at": "document_end"})
    return m


TESTHOOK = """// Test builds only: hand every tab report to the local test page.
const api = globalThis.browser ?? globalThis.chrome;
Promise.resolve(api.runtime.sendMessage({ type: 'test:reports' })).then((all) => {
  const pre = document.createElement('pre');
  pre.id = 'lens-reports';
  pre.textContent = JSON.stringify(all);
  document.body.appendChild(pre);
});
"""


def build(browser: str, test: bool) -> Path:
    out = DIST / (browser + ("-test" if test else ""))
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    src = EXT / "src"
    shutil.copytree(src / "core", out / "core")
    for name in ("content.js",):
        shutil.copy2(src / name, out / name)
    for name in ("popup.html", "popup.css", "popup.js"):
        shutil.copy2(src / "ui" / name, out / name)
    background = (src / "background.js").read_text(encoding="utf-8")
    if test:
        background = background.replace("const TEST_HOOKS = false;", "const TEST_HOOKS = true;", 1)
        (out / "testhook.js").write_text(TESTHOOK, encoding="utf-8", newline="\n")
    (out / "background.js").write_text(background, encoding="utf-8", newline="\n")
    shutil.copytree(EXT / "data", out / "data")
    shutil.copytree(EXT / "_locales", out / "_locales")
    shutil.copytree(EXT / "icons", out / "icons")
    shutil.copy2(EXT / "THIRD_PARTY.md", out / "THIRD_PARTY.md")
    shutil.copy2(EXT.parent / "LICENSE", out / "LICENSE")
    (out / "manifest.json").write_text(json.dumps(manifest(browser, test), indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8", newline="\n")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--browser", default="all", choices=["chrome", "firefox", "all"])
    ap.add_argument("--edition", default="analysis", choices=["analysis"])
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args(argv)
    check = subprocess.run([sys.executable, str(EXT / "tools" / "build_data.py"), "--check"])
    if check.returncode:
        return check.returncode
    for browser in (["chrome", "firefox"] if args.browser == "all" else [args.browser]):
        print("built", build(browser, args.test).relative_to(EXT.parent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
