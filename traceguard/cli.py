"""Command line: scan one or more public URLs and write one JSON report per site."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .classify import TrackerList
from .safety import UnsafeURL


def _normalise(url: str) -> str:
    return url if "://" in url else f"https://{url}"


def _slug(url: str) -> str:
    parts = urlsplit(url)
    return re.sub(r"[^a-z0-9]+", "-", f"{parts.hostname or 'site'}{parts.path}".lower()).strip("-")


def _load_sites(args) -> list[dict]:
    sites = [{"name": None, "url": _normalise(u), "first_party_domains": list(args.first_party)} for u in args.urls]
    if args.sites_file:
        data = json.loads(Path(args.sites_file).read_text(encoding="utf-8"))
        for entry in data["sites"] if isinstance(data, dict) else data:
            sites.append({"name": entry.get("name"), "url": _normalise(entry["url"]),
                          "first_party_domains": entry.get("first_party_domains", [])})
    return sites


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard", description=__doc__)
    parser.add_argument("urls", nargs="*", help="public URLs to scan")
    parser.add_argument("--sites-file", help="JSON list of {name, url, first_party_domains}")
    parser.add_argument("--first-party", action="append", default=[],
                        help="extra first-party domain for the URLs given on the command line (repeatable)")
    parser.add_argument("--passes", type=int, default=3, help="passes per site (default 3)")
    parser.add_argument("--observe", type=float, default=12.0, help="observation window in seconds (default 12)")
    parser.add_argument("--min-requests", type=int, default=5,
                        help="a pass with fewer requests is marked 'incomplete' (default 5)")
    parser.add_argument("--pause", type=float, default=3.0,
                        help="seconds between passes and between sites (default 3)")
    parser.add_argument("--out", default=None,
                        help="output directory (default data/runs; data/consent/<vantage> with --consent; "
                             "data/protected/<vantage> with --blocklist)")
    parser.add_argument("--consent", default="",
                        help="optional consent measurement: 'reject', 'accept' or 'reject,accept'. After the passive "
                             "window, press that banner button once and record a second window")
    parser.add_argument("--after", type=float, default=None,
                        help="with --consent: seconds recorded after the click (default: same as --observe)")
    parser.add_argument("--blocklist", help="optional 'with protection' measurement: Adblock-style filter list file "
                                            "(domain rules are applied, matching requests are blocked)")
    parser.add_argument("--blocklist-name", default=None, help="public name of the list, e.g. EasyPrivacy")
    parser.add_argument("--blocklist-url", default="", help="where the list was downloaded from (recorded in reports)")
    parser.add_argument("--vantage", default=os.environ.get("TRACEGUARD_VANTAGE", "unspecified"),
                        help="where the scan runs, e.g. github-actions-us (recorded in the report)")
    parser.add_argument("--locale", default="en-US")
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--tracker-list", help="tracker list JSON (default: bundled seed list)")
    args = parser.parse_args(argv)

    sites = _load_sites(args)
    if not sites:
        parser.error("give at least one URL or --sites-file")
    if args.passes < 1:
        parser.error("--passes must be at least 1")
    modes = tuple(m.strip() for m in args.consent.split(",") if m.strip())
    if any(m not in ("reject", "accept") for m in modes) or len(set(modes)) != len(modes):
        parser.error("--consent takes 'reject', 'accept' or 'reject,accept'")
    if modes and args.blocklist:
        parser.error("--consent and --blocklist are separate measurements: run them one at a time")
    default_out = ("data/consent/" + args.vantage if modes else
                   "data/protected/" + args.vantage if args.blocklist else "data/runs")
    out_root = Path(args.out or default_out)
    if (modes or args.blocklist) and out_root.resolve() == Path("data/runs").resolve():
        parser.error("consent and protection reports must not go into the main series (data/runs)")
    blocker = None
    if args.blocklist:
        from .blocker import FilterList
        blocker = FilterList.load(args.blocklist, args.blocklist_name, args.blocklist_url)

    from .scanner import scan_site  # heavy import (Playwright) only when scanning

    tracker_list = TrackerList.load(args.tracker_list)
    out_dir = out_root / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    rejected = 0
    for index, site in enumerate(sites):
        if index:
            time.sleep(args.pause)
        try:
            report = scan_site(site["url"], name=site["name"], first_party_domains=site["first_party_domains"],
                               passes=args.passes, observe_seconds=args.observe,
                               min_requests=args.min_requests, pause_seconds=args.pause, vantage=args.vantage,
                               locale=args.locale, timezone=args.timezone, tracker_list=tracker_list,
                               consent_modes=modes, after_seconds=args.after, blocker=blocker)
        except UnsafeURL as exc:
            print(f"REJECTED {site['url']}: {exc}", file=sys.stderr)
            rejected += 1
            continue
        except Exception as exc:  # one failing site must not abort the whole batch
            print(f"FAILED {site['url']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            rejected += 1
            continue
        path = out_dir / f"{_slug(report['site']['url'])}.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        summary = report["summary"]
        metrics = summary.get("metrics", {})
        consent = "".join(f" | {m}: {c['outcome']}" + (f" ({c['metrics']['tracking_services_after']} after)"
                                                         if c.get("metrics") else "")
                          for m, c in summary.get("consent", {}).items())
        blocked = (f" | blocked by list {summary['protection']['blocked_requests']}"
                   if summary.get("protection") else "")
        print(f"{report['site']['name']}: {summary['status']}"
              + (f" | third-party requests {metrics['third_party_requests']}, "
                 f"tracking services {metrics['tracking_services']}" if metrics else "")
              + consent + blocked + f" -> {path}")
    return 2 if rejected else 0
