"""Package Tracker Watch Lens for each browser: one clean manifest per browser, same code.

    python extension/tools/build.py [--browser chrome|firefox|all] [--edition analysis] [--test]

Output: extension/dist/<browser>/ (or <browser>-test/), ready for "Load unpacked" (Chrome/Edge) or
about:debugging (Firefox). --test also enables a hook that hands all tab reports to a local test page;
test builds are never published. Editions "full" and "clean" (spec §10) arrive with the clean mode (M7).
"""
from __future__ import annotations

import argparse
import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mdpage import markdown_page  # noqa: E402
from rules import rulesets  # noqa: E402
from vendor_rules import cname_trackers, easyprivacy_rules  # noqa: E402
from easylist import easylist  # noqa: E402

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
        "permissions": ["webRequest", "webNavigation", "storage", "cookies", "declarativeNetRequestWithHostAccess", "scripting"],
        "host_permissions": ["<all_urls>"],
        "optional_permissions": ["browsingData"],
        "icons": {str(n): f"icons/icon-{n}.png" for n in (16, 32, 48, 128)},
        "action": {"default_popup": "popup.html", "default_title": "__MSG_extName__",
                   "default_icon": {str(n): f"icons/icon-{n}.png" for n in (16, 32)}},
        "content_scripts": [{"matches": ["<all_urls>"], "js": ["content-data.js", "content.js"],
                             "run_at": "document_start", "all_frames": True},
                            {"matches": ["<all_urls>"], "js": ["content-main.js"], "run_at": "document_start",
                             "all_frames": True, "world": "MAIN"}],
        "options_ui": {"page": "options.html", "open_in_tab": True},
        # clean mode (F14): shipped disabled, turned on by the user in the settings page
        "declarative_net_request": {"rule_resources": [
            {"id": rid, "enabled": False, "path": f"rules/{rid}.json"} for rid in ("verified", "full", "params", "easyprivacy", "siteonly", "easylist")]},
        "content_security_policy": {"extension_pages": "script-src 'self'; object-src 'none'"},
    }
    if browser == "firefox":
        m["permissions"].append("dns")  # Firefox only: resolve names to spot trackers disguised as the site itself
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


GUIDE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="popup.css"><link rel="stylesheet" href="options.css">
<title>How to browse with less tracking</title></head>
<body><main class="privacy">
{{BODY}}</main>
<footer class="credit"><p>Designed by jlasenjo</p>
<p><a href="mailto:asenjo.jose@hotmail.com">asenjo.jose@hotmail.com</a></p></footer></body></html>
"""

TESTHOOK = """// Test builds only: hand every tab report to the local test page.
const api = globalThis.browser ?? globalThis.chrome;
const first = location.search.includes('mytests=on') ? api.runtime.sendMessage({ type: 'test:mytests-on' }) : null;
if (location.search.includes('summary=on')) api.runtime.sendMessage({ type: 'test:summary-on' });
const clean = new URLSearchParams(location.search).get('clean');
const cleaning = clean ? api.runtime.sendMessage({ type: 'test:clean', blocking: clean === 'off' ? null : clean, params: clean !== 'off',
  ads: new URLSearchParams(location.search).get('ads') === 'on' }) : null;
const allow = new URLSearchParams(location.search).get('allow'); // site:domain, for "site only" mode
const allowing = allow ? api.runtime.sendMessage({ type: 'test:allow', site: allow.split(':')[0], domain: allow.split(':')[1] }) : null;
const learning = location.search.includes('learn=on') ? api.runtime.sendMessage({ type: 'test:learn-on' }) : null;
Promise.all([first, cleaning, allowing, learning]).then(() => api.runtime.sendMessage({ type: 'test:reports' })).then((all) => {
  const pre = document.createElement('pre');
  pre.id = 'lens-reports';
  pre.textContent = JSON.stringify(all);
  document.body.appendChild(pre);
});
"""


def content_data() -> str:
    """Tables the content script needs, inlined (content scripts cannot import modules)."""
    glossary = json.loads((EXT / "data" / "glossary.json").read_text(encoding="utf-8"))
    engines = json.loads((EXT / "profiles" / "search_engines.json").read_text(encoding="utf-8"))["engines"]
    data = {"consent": glossary["consent_tools"], "buttons": glossary["consent_buttons"],
            "engines": [{k: e[k] for k in ("id", "hosts", "results_paths", "redirect_paths")} for e in engines]}
    return ("// Generated by tools/build.py from data/glossary.json and profiles/search_engines.json.\n"
            f"globalThis.__lensData = {json.dumps(data, ensure_ascii=False, sort_keys=True)};\n")


def build(browser: str, test: bool) -> Path:
    out = DIST / (browser + ("-test" if test else ""))
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    src = EXT / "src"
    shutil.copytree(src / "core", out / "core")
    for name in ("content.js", "content-main.js"):
        shutil.copy2(src / name, out / name)
    for name in ("popup.html", "popup.css", "popup.js", "options.html", "options.css", "options.js"):
        shutil.copy2(src / "ui" / name, out / name)
    background = (src / "background.js").read_text(encoding="utf-8")
    if test:
        background = background.replace("const TEST_HOOKS = false;", "const TEST_HOOKS = true;", 1)
        (out / "testhook.js").write_text(TESTHOOK, encoding="utf-8", newline="\n")
    (out / "background.js").write_text(background, encoding="utf-8", newline="\n")
    shutil.copytree(EXT / "data", out / "data")
    shutil.copy2(EXT / "profiles" / "search_engines.json", out / "data" / "search_engines.json")
    (out / "content-data.js").write_text(content_data(), encoding="utf-8", newline="\n")
    shutil.copytree(EXT / "_locales", out / "_locales")
    # build stamp shown in the panel footer (Firefox rejects the manifest's version_name)
    (out / "data" / "build.json").write_text(json.dumps({"built": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}),
                                             encoding="utf-8", newline="\n")
    shutil.copytree(EXT / "icons", out / "icons")
    shutil.copy2(EXT / "THIRD_PARTY.md", out / "THIRD_PARTY.md")
    shutil.copy2(EXT / "PRIVACY.md", out / "PRIVACY.md")
    if (EXT / "vendor" / "LICENSE-cname-trackers.txt").exists():
        shutil.copy2(EXT / "vendor" / "LICENSE-cname-trackers.txt", out / "LICENSE-cname-trackers.txt")
    (out / "rules").mkdir()
    ep_rules, _ = easyprivacy_rules()
    (out / "rules" / "easyprivacy.json").write_text(json.dumps(ep_rules) + "\n", encoding="utf-8", newline="\n")
    el_rules, el_cosmetic, _ = easylist()  # ads: network rules and element hiding
    (out / "rules" / "easylist.json").write_text(json.dumps(el_rules) + "\n", encoding="utf-8", newline="\n")
    (out / "data" / "cosmetic.json").write_text(json.dumps(el_cosmetic) + "\n", encoding="utf-8", newline="\n")
    (out / "data" / "cname_trackers.json").write_text(json.dumps(cname_trackers(), indent=1) + "\n", encoding="utf-8", newline="\n")
    for rid, rules in rulesets(EXT).items():
        (out / "rules" / f"{rid}.json").write_text(json.dumps(rules, indent=1) + "\n", encoding="utf-8", newline="\n")
    if (EXT / "GUIDE.md").exists():
        guide = markdown_page((EXT / "GUIDE.md").read_text(encoding="utf-8"))
        (out / "guide.html").write_text(GUIDE_PAGE.replace("{{BODY}}", guide), encoding="utf-8", newline="\n")
    (out / "privacy.html").write_text(markdown_page((EXT / "PRIVACY.md").read_text(encoding="utf-8")),
                                      encoding="utf-8", newline="\n")
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
    import time
    started = time.time()
    print("Checking the data snapshot...", flush=True)
    check = subprocess.run([sys.executable, str(EXT / "tools" / "build_data.py"), "--check"])
    if check.returncode:
        print("STOPPED: the data is out of date (run extension/tools/build_data.py first).")
        return check.returncode
    for browser in (["chrome", "firefox"] if args.browser == "all" else [args.browser]):
        print(f"Building {browser}...", flush=True)
        print("  built", build(browser, args.test).relative_to(EXT.parent))
    print(f"DONE in {time.time() - started:.1f} s. In chrome://extensions press the reload arrow of Tracker Watch Lens.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
