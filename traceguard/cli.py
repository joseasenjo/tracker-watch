"""Command line: scan one or more public URLs and write one JSON report per site."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
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
    parser.add_argument("--out", default="data/runs", help="output directory (default data/runs)")
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

    from .scanner import scan_site  # heavy import (Playwright) only when scanning

    tracker_list = TrackerList.load(args.tracker_list)
    out_dir = Path(args.out) / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    rejected = 0
    for site in sites:
        try:
            report = scan_site(site["url"], name=site["name"], first_party_domains=site["first_party_domains"],
                               passes=args.passes, observe_seconds=args.observe, vantage=args.vantage,
                               locale=args.locale, timezone=args.timezone, tracker_list=tracker_list)
        except UnsafeURL as exc:
            print(f"REJECTED {site['url']}: {exc}", file=sys.stderr)
            rejected += 1
            continue
        path = out_dir / f"{_slug(report['site']['url'])}.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        summary = report["summary"]
        metrics = summary.get("metrics", {})
        print(f"{report['site']['name']}: {summary['status']}"
              + (f" | third-party requests {metrics['third_party_requests']}, "
                 f"tracking services {metrics['tracking_services']}" if metrics else "")
              + f" -> {path}")
    return 2 if rejected else 0
