"""Weekly thread drafts for Bluesky and Mastodon. Drafts only: nothing is ever published here.

The drafts are written to disk so a person can review them (approval gate) before any posting.
Posts never @-mention outlets and state measured counts, not verdicts.

Usage: python -m traceguard.posts data/runs --platform bluesky --report-url https://example.org/report
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import date as _date, datetime, timezone
from pathlib import Path

from .diff import compare_directories

LIMITS = {"bluesky": 300, "mastodon": 500}
VANTAGE_LABELS = {"github-actions-us": "GitHub servers in the US", "local-windows-spain": "a PC in Spain"}
USABLE_CONFIDENCE = ("high", "medium")
SPAIN_GROUP = "ES"
SPAIN_MAX_AGE_DAYS = 7  # a Spain measurement older than this (against the main one) is left out of the thread


class PostTooLong(ValueError):
    pass


def load_latest_reports(runs_dir: Path | str) -> tuple[str, list[dict]]:
    runs_dir = Path(runs_dir)
    dates = sorted(p for p in runs_dir.iterdir() if p.is_dir())
    if not dates:
        raise FileNotFoundError(f"no dated report folders in {runs_dir}")
    latest = dates[-1]
    return latest.name, [json.loads(p.read_text(encoding="utf-8")) for p in sorted(latest.glob("*.json"))]


def eligible(reports: list[dict]) -> list[dict]:
    return [r for r in reports
            if r["summary"]["status"] == "ok" and r["summary"].get("confidence", "high") in USABLE_CONFIDENCE]


def ranking(reports: list[dict]) -> list[dict]:
    """Sites that contacted tracking services, most first. Ties broken by third-party requests, then name."""
    rows = [{"name": r["site"]["name"], "tracking_services": r["summary"]["metrics"]["tracking_services"],
             "third_party_requests": r["summary"]["metrics"]["third_party_requests"]} for r in eligible(reports)]
    rows.sort(key=lambda x: (-x["tracking_services"], -x["third_party_requests"], x["name"]))
    return [row for row in rows if row["tracking_services"] > 0]


def _vantage(reports: list[dict]) -> str:
    raw = reports[0]["measurement"]["vantage"] if reports else "unspecified"
    return VANTAGE_LABELS.get(raw, raw)


def _headline(date: str, reports: list[dict], top: int) -> str:
    rows = ranking(reports)[:top]
    leaders = ", ".join(f"{r['name']} {r['tracking_services']}" for r in rows) or "none contacted any"
    return (f"Weekly check ({date}): news sites and the third-party tracking services they contact before "
            f"you click anything. {len(eligible(reports))} of {len(reports)} sites measured, from "
            f"{_vantage(reports)}. Most: {leaders}. Thread below.")


def _changes(diffs: list[dict], limit: int) -> str:
    if not diffs:
        return "Changes since last week: this is the first measurement, so there is nothing to compare yet."
    notable = [d for d in diffs if d["comparable"] and d["notable"]]
    if not notable:
        comparable = sum(1 for d in diffs if d["comparable"])
        if not comparable:
            return "Changes since last week: not comparable (measurement conditions differed)."
        return "Changes since last week: no stable change in tracking services on the sites we could compare."
    for shown in range(len(notable), 0, -1):
        parts = []
        for d in notable[:shown]:
            bits = []
            if d["added_tracking_services"]:
                bits.append("+" + ", ".join(s["service"] for s in d["added_tracking_services"][:2]))
            if d["removed_tracking_services"]:
                bits.append("-" + ", ".join(s["service"] for s in d["removed_tracking_services"][:2]))
            parts.append(f"{d['site']} {' '.join(bits)}")
        extra = f" (+{len(notable) - shown} more)" if shown < len(notable) else ""
        text = "Changes since last week (stable in most passes): " + "; ".join(parts) + extra + "."
        if len(text) <= limit:
            return text
    return f"Changes since last week: {len(notable)} sites changed. See the report."


def group_medians(reports: list[dict], groups: dict[str, str]) -> list[tuple[str, int, int]]:
    """(group, median tracking services, sites measured) for each group with at least one usable site."""
    by_group: dict[str, list[int]] = {}
    for r in eligible(reports):
        group = groups.get(r["site"]["url"].rstrip("/"))
        if group:
            by_group.setdefault(group, []).append(r["summary"]["metrics"]["tracking_services"])
    return [(g, round(statistics.median(v)), len(v)) for g, v in sorted(by_group.items())]


def _countries(reports: list[dict], groups: dict[str, str]) -> str | None:
    rows = group_medians(reports, groups)
    if len(rows) < 2:
        return None
    listing = ", ".join(f"{g} {m} ({n})" for g, m, n in rows)
    return ("By country, the median number of tracking services contacted before any click, with the sites "
            f"measured in brackets: {listing}.")


def _spain(date: str, reports: list[dict], limit: int) -> str | None:
    """The Spanish outlets measured from Spain, where consent banners appear as a Spanish visitor sees them."""
    usable = eligible(reports)
    if not usable:
        return None
    rows = sorted(({"name": r["site"]["name"], "n": r["summary"]["metrics"]["tracking_services"]} for r in usable),
                  key=lambda x: (-x["n"], x["name"]))
    for top in (3, 2, 1):
        leaders = ", ".join(f"{r['name']} {r['n']}" for r in rows[:top])
        text = (f"Spanish outlets, measured from a PC in Spain ({date}): {len(usable)} of {len(reports)} sites "
                f"measured. Most tracking services contacted before any click: {leaders}.")
        if len(text) <= limit:
            return text
    return None


def _method(reports: list[dict], report_url: str) -> str:
    m = reports[0]["measurement"] if reports else {}
    failed = len(reports) - len(eligible(reports))
    return (f"How: pages load with no clicks for {m.get('observe_seconds', 12):g}s, {m.get('passes', 3)} passes, "
            f"median. Third party = other registered domain, checked against a limited hand-built list "
            f"(may be incomplete). {failed} sites blocked/unmeasured. Counts, not legal verdicts. "
            f"Method and data: {report_url}")


def build_thread(date: str, reports: list[dict], diffs: list[dict], *, platform: str, report_url: str,
                 groups: dict[str, str] | None = None,
                 spain: tuple[str, list[dict]] | None = None) -> list[str]:
    """groups: site URL -> country group (adds a by-country line); spain: (date, reports) of the Spanish group
    measured from Spain (adds a post of its own). Without them the thread is the three basic posts."""
    limit = LIMITS[platform]
    headline = None
    for top in (3, 2, 1, 0):
        candidate = _headline(date, reports, top)
        if len(candidate) <= limit:
            headline = candidate
            break
    posts = [headline or _headline(date, reports, 0)]
    countries = _countries(reports, groups) if groups else None
    if countries:
        posts.append(countries)
    spanish = _spain(spain[0], spain[1], limit) if spain and spain[1] else None
    if spanish:
        posts.append(spanish)
    posts += [_changes(diffs, limit), _method(reports, report_url)]
    for index, post in enumerate(posts, 1):
        if len(post) > limit:
            raise PostTooLong(f"post {index} has {len(post)} characters (limit {limit})")
    return posts


def alt_text(reports: list[dict]) -> str:
    rows = ranking(reports)[:10]
    if not rows:
        return "No measured site contacted a classified tracking service before interaction."
    listing = "; ".join(f"{r['name']}: {r['tracking_services']}" for r in rows)
    return f"Bar chart of the number of third-party tracking services contacted before any interaction. {listing}."


def render_markdown(date: str, platform: str, posts: list[str], alt: str) -> str:
    limit = LIMITS[platform]
    lines = [f"# DRAFT thread for {platform} ({date}): NOT PUBLISHED, needs human approval", ""]
    for index, post in enumerate(posts, 1):
        lines += [f"## Post {index} ({len(post)}/{limit})", "", post, ""]
    lines += ["## Image alt text (chart not generated yet)", "", alt, ""]
    return "\n".join(lines)


def load_groups(sites_file: Path | str) -> dict[str, str]:
    """site URL (without a trailing slash) -> group; empty when the file is missing."""
    path = Path(sites_file)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {s["url"].rstrip("/"): s["group"] for s in data.get("sites", []) if s.get("group")}


def load_spain(spain_dir: Path | str, groups: dict[str, str], main_date: str) -> tuple[str, list[dict]] | None:
    """The newest folder of reports measured from Spain, only its Spanish-group sites, and only when it is not
    more than SPAIN_MAX_AGE_DAYS older than the main measurement."""
    path = Path(spain_dir)
    if not path.is_dir():
        return None
    dates = sorted(p for p in path.iterdir() if p.is_dir())
    if not dates:
        return None
    latest = dates[-1]
    try:
        age = (_date.fromisoformat(main_date) - _date.fromisoformat(latest.name)).days
    except ValueError:
        return None
    if age > SPAIN_MAX_AGE_DAYS:
        return None
    reports = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(latest.glob("*.json"))]
    reports = [r for r in reports if groups.get(r["site"]["url"].rstrip("/")) == SPAIN_GROUP]
    return (latest.name, reports) if reports else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.posts", description=__doc__)
    parser.add_argument("runs_dir", nargs="?", default="data/runs")
    parser.add_argument("--platform", choices=sorted(LIMITS), default="bluesky")
    parser.add_argument("--report-url", default="https://example.org/report", help="link to the full report")
    parser.add_argument("--sites-file", default="data/sites.json", help="list of sites (gives each its country group)")
    parser.add_argument("--spain-dir", default="data/extra/local-windows-spain",
                        help="reports of the Spanish group measured from Spain (left out if missing or too old)")
    parser.add_argument("--out", default="data/drafts", help="where draft files are written")
    parser.add_argument("--dry-run", action="store_true", help="print the draft and write nothing")
    args = parser.parse_args(argv)

    date, reports = load_latest_reports(args.runs_dir)
    diffs = compare_directories(args.runs_dir)
    groups = load_groups(args.sites_file)
    spain = load_spain(args.spain_dir, groups, date)
    posts = build_thread(date, reports, diffs, platform=args.platform, report_url=args.report_url,
                         groups=groups, spain=spain)
    markdown = render_markdown(date, args.platform, posts, alt_text(reports))
    print(markdown)
    if not args.dry_run:
        out_dir = Path(args.out) / date
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{args.platform}.md").write_text(markdown, encoding="utf-8")
        (out_dir / f"{args.platform}.json").write_text(json.dumps({
            "date": date, "platform": args.platform, "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "draft", "posts": posts, "alt_text": alt_text(reports)}, indent=2, ensure_ascii=False),
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
