"""Export the tracking domains of the classification list as filter lists for ad and tracker blockers.

Two files, in Adblock syntax (understood by uBlock Origin, AdGuard and similar blockers):
- trackerwatch-verified.txt: tracking entries whose operator was verified one by one.
- trackerwatch-full.txt: all tracking entries, including those identified from general knowledge of the vendor
  and marked as unverified in the list (a wrong attribution would block a harmless domain).

Only the tracking categories are exported (advertising, analytics, social, session replay, audience
measurement). Tag managers, consent tools and paywalls are never exported: blocking them breaks sites.
Every rule ends in `$third-party`, so visiting a company's own site or using its own tools (for example the
analytics dashboard) is not affected, only requests that other sites make to it.

The file only does something when a blocker that the user already has reads it. It is not complete protection.

Usage: python -m traceguard.filterlist --out some/folder [--homepage https://example.org/tracker-watch/]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date as Date
from pathlib import Path

from .classify import DEFAULT_TRACKER_LIST

DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
FILES = {
    "trackerwatch-verified.txt": {
        "title": "Tracker Watch tracking domains (verified entries only)",
        "summary": "Tracking services whose operator was verified one by one. Smallest list, fewest mistakes.",
        "verified_only": True},
    "trackerwatch-full.txt": {
        "title": "Tracker Watch tracking domains (all entries)",
        "summary": "Every tracking service in the list, including entries identified from general knowledge of the "
                   "vendor and not verified one by one. Larger, with a small risk of a wrong attribution.",
        "verified_only": False},
}


def tracking_entries(data: dict) -> list[dict]:
    """Rows {domain, entity, category, verified} for the tracking categories, sorted by domain."""
    tracking = set(data["_meta"]["tracking_categories"])
    rows = []
    for domain, entry in data["domains"].items():
        if entry["category"] not in tracking:
            continue
        if not DOMAIN.match(domain):
            raise ValueError(f"not a valid domain name in the list: {domain!r}")
        rows.append({"domain": domain, "entity": entry["entity"], "category": entry["category"],
                     "verified": not entry.get("evidence")})
    return sorted(rows, key=lambda r: r["domain"])


def _header(spec: dict, version: str, count: int, homepage: str) -> list[str]:
    lines = ["[Adblock Plus 2.0]", f"! Title: {spec['title']}",
             f"! Description: {spec['summary']} Blocks third-party requests only.",
             f"! Version: {version}", "! Expires: 7 days", "! License: CC BY 4.0 (credit \"Tracker Watch\")",
             f"! Entries: {count}"]
    if homepage:
        lines.append(f"! Homepage: {homepage}filters.html")
    lines += ["!", "! This list does nothing by itself: a blocker that reads it decides what to block.",
              "! It is not complete protection, and blocking can change how a page works.",
              "! Only tracking categories are listed; tag managers, consent tools and paywalls never are.", "!"]
    return lines


def build(data: dict, today: str | None = None, homepage: str = "") -> dict[str, dict]:
    """filename -> {text, count, verified, unverified, title, summary}. `homepage` ends with a slash or is empty."""
    rows = tracking_entries(data)
    version = (today or Date.today().isoformat()).replace("-", ".")
    result = {}
    for name, spec in FILES.items():
        chosen = [r for r in rows if r["verified"] or not spec["verified_only"]]
        verified = [r for r in chosen if r["verified"]]
        unverified = [r for r in chosen if not r["verified"]]
        lines = _header(spec, version, len(chosen), homepage)
        lines += [f"||{r['domain']}^$third-party" for r in verified]
        if unverified:
            lines += ["!", "! Identified from general knowledge of the vendor, not verified one by one:"]
            lines += [f"||{r['domain']}^$third-party" for r in unverified]
        result[name] = {"text": "\n".join(lines) + "\n", "count": len(chosen), "verified": len(verified),
                        "unverified": len(unverified), "title": spec["title"], "summary": spec["summary"]}
    return result


def write(out_dir: Path | str, today: str | None = None, homepage: str = "",
          tracker_list: Path | str = DEFAULT_TRACKER_LIST) -> dict[str, dict]:
    data = json.loads(Path(tracker_list).read_text(encoding="utf-8"))
    files = build(data, today, homepage)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, info in files.items():
        (out / name).write_text(info["text"], encoding="utf-8")
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.filterlist", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="folder for the two .txt files")
    parser.add_argument("--homepage", default="", help="site address ending in /, written in the header")
    parser.add_argument("--list", default=str(DEFAULT_TRACKER_LIST), help="classification list (default: bundled)")
    args = parser.parse_args(argv)
    for name, info in write(args.out, homepage=args.homepage, tracker_list=args.list).items():
        print(f"{name}: {info['count']} entries ({info['unverified']} unverified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
