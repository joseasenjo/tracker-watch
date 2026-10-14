"""Weekly thread drafts for Bluesky and Mastodon. Drafts only: nothing is ever published here.

The drafts are written to disk so a person can review them (approval gate) before any posting.
Posts never @-mention outlets and state measured counts, not verdicts.

Usage: python -m traceguard.posts data/runs --platform bluesky --report-url https://example.org/report
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date as _date, datetime, timezone
from pathlib import Path

from .diff import compare_directories

LIMITS = {"bluesky": 300, "mastodon": 500}
VANTAGE_LABELS = {"github-actions-us": "GitHub servers in the US", "local-windows-spain": "a PC in Spain"}
USABLE_CONFIDENCE = ("high", "medium")
SPAIN_GROUP = "ES"
SPAIN_MAX_AGE_DAYS = 7  # a Spain measurement older than this (against the main one) is left out of the thread
ROOT_TAGS = ("Automated post. #privacy #adtech #GDPR", "Automated post.", "")  # tried in this order, longest first
ALERT_MIN_DELTA = 15  # a site's tracking services must change by at least this many ...
ALERT_MIN_RATIO = 0.5  # ... and by at least this share of the larger of the two counts, to be worth an alert
CHART_FILE = "chart.png"
MAX_IMAGE_BYTES = 950_000  # Bluesky refuses images over about 1 MB
MAX_ALT_CHARS = 1500  # Mastodon's default limit for an image description


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


def measured(reports: list[dict]) -> list[dict]:
    """Sites that could be measured at all (the website's "sites measured"), whatever the confidence."""
    return [r for r in reports if r["summary"]["status"] == "ok"]


def ranking(reports: list[dict]) -> list[dict]:
    """Sites that contacted tracking services, most first. Ties broken by third-party requests, then name."""
    rows = [{"name": r["site"]["name"], "tracking_services": r["summary"]["metrics"]["tracking_services"],
             "third_party_requests": r["summary"]["metrics"]["third_party_requests"]} for r in eligible(reports)]
    rows.sort(key=lambda x: (-x["tracking_services"], -x["third_party_requests"], x["name"]))
    return [row for row in rows if row["tracking_services"] > 0]


def _vantage(reports: list[dict]) -> str:
    raw = reports[0]["measurement"]["vantage"] if reports else "unspecified"
    return VANTAGE_LABELS.get(raw, raw)


def _headline(date: str, reports: list[dict], top: int, tags: str = "") -> str:
    rows = ranking(reports)[:top]
    leaders = ", ".join(f"{r['name']} {r['tracking_services']}" for r in rows) or "none contacted any"
    text = (f"Weekly check ({date}): news sites and the third-party tracking services they contact before "
            f"you click anything. {len(measured(reports))} of {len(reports)} sites measured, from "
            f"{_vantage(reports)}. Most: {leaders}. Thread below.")
    return f"{text} {tags}" if tags else text


def _change_line(d: dict) -> str:
    bits = []
    if d["added_tracking_services"]:
        bits.append("added " + ", ".join(s["service"] for s in d["added_tracking_services"][:2]))
    if d["removed_tracking_services"]:
        bits.append("dropped " + ", ".join(s["service"] for s in d["removed_tracking_services"][:2]))
    return f"{d['site']}: {'; '.join(bits)}"


def _changes(diffs: list[dict], limit: int, report_url: str | None = None) -> str:
    if not diffs:
        return "Changes since last week: this is the first measurement, so there is nothing to compare yet."
    notable = [d for d in diffs if d["comparable"] and d["notable"]]
    if not notable:
        comparable = sum(1 for d in diffs if d["comparable"])
        if not comparable:
            return "Changes since last week: not comparable (measurement conditions differed)."
        return "Changes since last week: no stable change in tracking services on the sites we could compare."
    link = f"{report_url.rstrip('/')}/changes.html" if report_url else None
    for shown in range(len(notable), 0, -1):
        more = len(notable) - shown
        if link:
            tail = f"All {len(notable)} changes: {link}" if more else f"Detail: {link}"
        else:
            tail = f"(+{more} more)" if more else ""
        lines = ["Changes since last week (stable in most passes):",
                 *(_change_line(d) for d in notable[:shown]), *([tail] if tail else [])]
        text = "\n".join(lines)
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
            f"measured in brackets: {listing}. All measured from {_vantage(reports)}: sites that show their "
            "consent banner only to European visitors look worse here.")


def _spain(date: str, reports: list[dict], limit: int) -> str | None:
    """The Spanish outlets measured from Spain, where consent banners appear as a Spanish visitor sees them."""
    usable = eligible(reports)
    if not usable:
        return None
    rows = sorted(({"name": r["site"]["name"], "n": r["summary"]["metrics"]["tracking_services"]} for r in usable),
                  key=lambda x: (-x["n"], x["name"]))
    for top in (3, 2, 1):
        leaders = ", ".join(f"{r['name']} {r['n']}" for r in rows[:top])
        text = (f"Spanish outlets, measured from a PC in Spain ({date}): {len(measured(reports))} of {len(reports)} sites "
                f"measured. Most tracking services contacted before any click: {leaders}.")
        if len(text) <= limit:
            return text
    return None


def _method(reports: list[dict], report_url: str) -> str:
    m = reports[0]["measurement"] if reports else {}
    failed = len(reports) - len(measured(reports))
    unranked = len(measured(reports)) - len(eligible(reports))
    note = f"; {unranked} low-confidence not ranked" if unranked else ""
    return (f"How: pages load with no clicks for {m.get('observe_seconds', 12):g}s, {m.get('passes', 3)} passes, "
            f"median. Third party = other registered domain, matched to a limited list "
            f"(may be incomplete). {failed} sites blocked/unmeasured{note}. Counts, not legal verdicts. "
            f"Method and data: {report_url}")


def build_thread(date: str, reports: list[dict], diffs: list[dict], *, platform: str, report_url: str,
                 groups: dict[str, str] | None = None,
                 spain: tuple[str, list[dict]] | None = None) -> list[str]:
    """groups: site URL -> country group (adds a by-country line); spain: (date, reports) of the Spanish group
    measured from Spain (adds a post of its own). Without them the thread is the three basic posts."""
    limit = LIMITS[platform]
    headline = None
    for tags in ROOT_TAGS:
        for top in (3, 2, 1, 0):
            candidate = _headline(date, reports, top, tags)
            if len(candidate) <= limit:
                headline = candidate
                break
        if headline:
            break
    posts = [headline or _headline(date, reports, 0)]
    countries = _countries(reports, groups) if groups else None
    if countries:
        posts.append(countries)
    spanish = _spain(spain[0], spain[1], limit) if spain and spain[1] else None
    if spanish:
        posts.append(spanish)
    posts += [_changes(diffs, limit, report_url), _method(reports, report_url)]
    for index, post in enumerate(posts, 1):
        if len(post) > limit:
            raise PostTooLong(f"post {index} has {len(post)} characters (limit {limit})")
    return posts


def company_reach(reports: list[dict]) -> list[tuple[str, int]]:
    """(company, sites where one of its tracking services was contacted in every stable pass), widest first."""
    counts: dict[str, int] = {}
    for r in eligible(reports):
        companies = {s.get("entity") for s in r["summary"].get("services", []) if s.get("tracking") and s.get("stable")}
        for company in companies - {None, ""}:
            counts[company] = counts.get(company, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def build_midweek(date: str, reports: list[dict], *, platform: str, report_url: str) -> list[str]:
    """One mid-week post from the same measurement: which companies' tracking services reach the most sites.
    Names companies, never outlets; counts, not verdicts."""
    limit, usable = LIMITS[platform], len(eligible(reports))
    reach = company_reach(reports)
    if not reach:
        raise ValueError("no company contacted on any measured site: nothing to post")
    # "Automated post." is kept before a longer company list: only if even one company does not fit is it dropped.
    for top in (3, 2, 1):
        listing = "; ".join(f"{c} {n} of {usable}" for c, n in reach[:top])
        text = (f"Midweek: companies whose tracking services were contacted before any click on the most of the "
                f"{usable} news sites measured with enough confidence ({date}). {listing}. Counts of sites, not verdicts. Data: {report_url}")
        for tags in ROOT_TAGS[:2] + (ROOT_TAGS[2:] if top == 1 else ()):
            candidate = f"{text} {tags}" if tags else text
            if len(candidate) <= limit:
                return [candidate]
    raise PostTooLong("the midweek post does not fit the limit")


def build_hook(date: str, reports: list[dict], *, platform: str, report_url: str) -> list[str]:
    """One standalone post on the single most-tracked outlet of the week (the rule fixed by the maintainer).
    It names an outlet, so it is only ever a draft that a person approves."""
    rows = ranking(reports)
    if not rows:
        raise ValueError("no measured site contacted a tracking service: nothing to post")
    top, limit = rows[0], LIMITS[platform]
    text = (f"{top['name']}: {top['tracking_services']} third-party tracking services contacted before any click, "
            f"the most of the {len(eligible(reports))} news sites measured with enough confidence ({date}, from "
            f"{_vantage(reports)}). Counts, not verdicts. Data: {report_url}")
    for tags in ROOT_TAGS[:2]:
        candidate = f"{text} {tags}"
        if len(candidate) <= limit:
            return [candidate]
    raise PostTooLong("the hook post does not fit the limit")


def _day(stamp: str) -> str:
    return str(stamp)[:10]


def notable_swings(diffs: list[dict]) -> list[dict]:
    """Comparable sites whose tracking-service count moved a lot between two measurements, biggest first."""
    swings = []
    for d in diffs:
        if not d.get("comparable"):
            continue
        m = d["metrics"]["tracking_services"]
        top = max(m["previous"], m["current"])
        if abs(m["delta"]) >= ALERT_MIN_DELTA and top and abs(m["delta"]) / top >= ALERT_MIN_RATIO:
            swings.append({"site": d["site"], "previous": m["previous"], "current": m["current"],
                           "previous_at": _day(d["previous_at"]), "current_at": _day(d["current_at"])})
    return sorted(swings, key=lambda x: (-abs(x["current"] - x["previous"]), x["site"]))


def build_alert(swings: list[dict], *, platform: str, report_url: str) -> list[str]:
    """One post on the biggest swing. It names an outlet, so it is only ever a draft that a person approves."""
    if not swings:
        raise ValueError("no notable swing: nothing to post")
    limit, w = LIMITS[platform], swings[0]
    link = f"{report_url.rstrip('/')}/changes.html"
    for note in (" A consent banner or measurement difference could explain it.", ""):
        text = (f"Notable change: tracking services contacted by {w['site']} before any click went from "
                f"{w['previous']} ({w['previous_at']}) to {w['current']} ({w['current_at']}). "
                f"Same method and vantage point.{note} "
                f"Counts, not verdicts. Data: {link}")
        for tags in ROOT_TAGS[:2]:
            candidate = f"{text} {tags}"
            if len(candidate) <= limit:
                return [candidate]
    raise PostTooLong("the alert post does not fit the limit")


def alt_text(reports: list[dict]) -> str:
    rows = ranking(reports)[:10]
    if not rows:
        return "No measured site contacted a classified tracking service before interaction."
    listing = "; ".join(f"{r['name']}: {r['tracking_services']}" for r in rows)
    return f"Bar chart of the number of third-party tracking services contacted before any interaction. {listing}."


def render_chart(runs_dir, sites_file, path):
    """Draw the ranking card (the same 1200x630 chart the website shares) and return its alt text, or None when it
    cannot be made (no Chromium, or the file is too large for Bluesky). Nothing is published here."""
    from .cards import alt_text as card_alt, ranking_card_html, render_png
    from .site import build_context
    sites = str(sites_file) if sites_file and Path(sites_file).exists() else None
    ctx = build_context(runs_dir, sites)
    if not render_png(ranking_card_html(ctx), path):
        print("warning: the chart could not be drawn (Playwright and Chromium are needed)", file=sys.stderr)
        return None
    if path.stat().st_size > MAX_IMAGE_BYTES:
        print(f"warning: the chart is {path.stat().st_size} bytes, over the limit; left out", file=sys.stderr)
        path.unlink()
        return None
    return card_alt(ctx)[:MAX_ALT_CHARS]


def render_markdown(date: str, platform: str, posts: list[str], alt: str, image: str | None = None) -> str:
    limit = LIMITS[platform]
    lines = [f"# DRAFT thread for {platform} ({date}): NOT PUBLISHED, needs human approval", ""]
    for index, post in enumerate(posts, 1):
        lines += [f"## Post {index} ({len(post)}/{limit})", ""]
        if index == 1 and image:
            lines += [f"[Image attached to this post: {image}]", ""]
        lines += [post, ""]
    where = "attached to post 1" if image else "no image was drawn"
    lines += [f"## Image alt text ({where})", "", alt, ""]
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
    parser.add_argument("--no-image", action="store_true", help="do not draw the chart image")
    parser.add_argument("--dry-run", action="store_true", help="print the draft and write nothing (no image either)")
    parser.add_argument("--min-measured", type=float, default=0.0, metavar="FRACTION",
                        help="stop (exit 2, no draft) when fewer than this share of the sites were measured well "
                             "(a broken scan must not become a post)")
    parser.add_argument("--kind", choices=("weekly", "midweek", "alert", "hook"), default="weekly",
                        help="weekly: the full thread; midweek: one post on the companies reaching the most sites "
                             "(written to <date>-midweek, no image); alert: one post on the biggest swing since the "
                             "last measurement (written to <date>-alert); hook: one post on the most-tracked outlet of the "
                             "week (written to <date>-hook). Alerts and hooks are never published automatically")
    args = parser.parse_args(argv)

    date, reports = load_latest_reports(args.runs_dir)
    if args.kind == "midweek":
        posts = build_midweek(date, reports, platform=args.platform, report_url=args.report_url)
        markdown = render_markdown(date, args.platform, posts, "", None)
        print(markdown)
        if not args.dry_run:
            out_dir = Path(args.out) / f"{date}-midweek"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{args.platform}.md").write_text(markdown, encoding="utf-8")
            (out_dir / f"{args.platform}.json").write_text(json.dumps({
                "date": date, "platform": args.platform, "generated_at": datetime.now(timezone.utc).isoformat(),
                "status": "draft", "posts": posts, "image": None, "alt_text": ""}, indent=2, ensure_ascii=False),
                encoding="utf-8")
        return 0
    if args.kind in ("alert", "hook"):
        if args.kind == "alert":
            swings = notable_swings(compare_directories(args.runs_dir))
            if not swings:
                print("No notable swing since the last measurement: no alert drafted.")
                return 0
            posts = build_alert(swings, platform=args.platform, report_url=args.report_url)
        else:
            posts = build_hook(date, reports, platform=args.platform, report_url=args.report_url)
        markdown = render_markdown(date, args.platform, posts, "", None)
        print(markdown)
        if not args.dry_run:
            out_dir = Path(args.out) / f"{date}-{args.kind}"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{args.platform}.md").write_text(markdown, encoding="utf-8")
            (out_dir / f"{args.platform}.json").write_text(json.dumps({
                "date": date, "platform": args.platform, "generated_at": datetime.now(timezone.utc).isoformat(),
                "status": "draft", "posts": posts, "image": None, "alt_text": ""}, indent=2, ensure_ascii=False),
                encoding="utf-8")
        return 0
    share = len(eligible(reports)) / len(reports) if reports else 0.0
    if share < args.min_measured:
        print(f"NO DRAFT: only {len(eligible(reports))} of {len(reports)} sites were measured well ({share:.0%}, "
              f"the minimum is {args.min_measured:.0%}).", file=sys.stderr)
        return 2
    diffs = compare_directories(args.runs_dir)
    groups = load_groups(args.sites_file)
    spain = load_spain(args.spain_dir, groups, date)
    posts = build_thread(date, reports, diffs, platform=args.platform, report_url=args.report_url,
                         groups=groups, spain=spain)
    out_dir = Path(args.out) / date
    image, alt = None, alt_text(reports)
    if not args.dry_run and not args.no_image:
        out_dir.mkdir(parents=True, exist_ok=True)
        chart_alt = render_chart(args.runs_dir, args.sites_file, out_dir / CHART_FILE)
        if chart_alt:
            image, alt = CHART_FILE, chart_alt
    markdown = render_markdown(date, args.platform, posts, alt, image)
    print(markdown)
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{args.platform}.md").write_text(markdown, encoding="utf-8")
        (out_dir / f"{args.platform}.json").write_text(json.dumps({
            "date": date, "platform": args.platform, "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "draft", "posts": posts, "image": image, "alt_text": alt}, indent=2, ensure_ascii=False),
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
