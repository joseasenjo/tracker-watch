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
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path

from . import autonomy
from .diff import compare_directories

LIMITS = {"bluesky": 300, "mastodon": 500}
VANTAGE_LABELS = {"github-actions-us": "GitHub servers in the US", "local-windows-spain": "a PC in Spain"}
USABLE_CONFIDENCE = ("high", "medium")
SPAIN_GROUP = "ES"
SPAIN_MAX_AGE_DAYS = 7  # a Spain measurement older than this (against the main one) is left out of the thread
ROOT_TAGS = ("Automated post. #privacy #adtech #GDPR", "Automated post.", "")  # tried in this order, longest first
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


def _some(items: list[dict]) -> str:
    """Two examples and how many more there are: a site that dropped 52 services must not read as if it dropped 2."""
    names = ", ".join(s["service"] for s in items[:2])
    return f"{names} (+{len(items) - 2} more)" if len(items) > 2 else names


def _change_line(d: dict) -> str:
    bits = []
    if d["added_tracking_services"]:
        bits.append("added " + _some(d["added_tracking_services"]))
    if d["removed_tracking_services"]:
        bits.append("dropped " + _some(d["removed_tracking_services"]))
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


def _extension(report_url: str, limit: int) -> str:
    """The last post of the weekly thread: the free extension, with its page (the one place the thread points to it)."""
    link = f"{report_url.rstrip('/')}/extension.html"
    for text in (f"Want to see this on the pages you read? Tracker Watch Lens is a free browser extension (Edge, Firefox): it shows "
                 f"which companies track you on a page and lets you block them. {link}",
                 f"See this on the pages you read: Tracker Watch Lens, a free extension for Edge and Firefox. {link}"):
        if len(text) <= limit:
            return text
    return link


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
    if report_url.startswith("http"):
        posts.append(_extension(report_url, limit))
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


def previous_reports(runs_dir: Path | str, date: str) -> list[dict]:
    """The reports of the scan before `date` (empty if there is none), to say how the figures moved since then."""
    folders = sorted(p for p in Path(runs_dir).iterdir() if p.is_dir() and p.name < date)
    if not folders:
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folders[-1].glob("*.json"))]


def _move(now: int, before: int | None) -> str:
    """' (+2)', ' (-1)' or ' (±0)' against the previous scan; nothing when there is no previous figure."""
    if before is None:
        return ""
    delta = now - before
    return f" ({delta:+d})" if delta else " (±0)"


def build_midweek(date: str, reports: list[dict], *, platform: str, report_url: str,
                  previous: list[dict] | None = None) -> list[str]:
    """One mid-week post from the same measurement: which companies' tracking services reach the most sites.
    `previous` (the reports of the scan before) adds how many sites more or fewer each company reached since then.
    Names companies, never outlets; counts, not verdicts."""
    limit, usable = LIMITS[platform], len(eligible(reports))
    reach = company_reach(reports)
    if not reach:
        raise ValueError("no company contacted on any measured site: nothing to post")
    before = dict(company_reach(previous)) if previous else None
    # Too long: the third company goes first, then the second; the movement against the previous scan (what makes the
    # post different each week) stays as long as one company fits with it. "Automated post." is dropped only if even
    # one company without movement does not fit.
    for with_move in ((True, False) if before is not None else (False,)):
        for top in (3, 2, 1):
            listing = "; ".join(f"{c} {n} of {usable}" + (_move(n, before.get(c, 0)) if with_move else "")
                                for c, n in reach[:top])
            tail = " vs last scan" if with_move else ""
            text = (f"Midweek: companies whose tracking services were contacted before any click on the most sites "
                    f"({date}, {usable} news sites measured with enough confidence). {listing}{tail}. "
                    f"Counts, not verdicts. Data: {report_url}")
            last_resort = top == 1 and not with_move
            for tags in ROOT_TAGS[:2] + (ROOT_TAGS[2:] if last_resort else ()):
                candidate = f"{text} {tags}" if tags else text
                if len(candidate) <= limit:
                    return [candidate]
    raise PostTooLong("the midweek post does not fit the limit")


EXPLAINERS_FILE = Path("data/explainers.json")


def load_explainers(path: Path | str = EXPLAINERS_FILE) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [e for e in data.get("explainers", []) if e.get("id") and e.get("text")]


def build_explainer(entry: dict, *, platform: str, report_url: str) -> list[str]:
    """One short explanation of how tracking works or how to read the figures. Not tied to a scan; a person approves it."""
    limit = LIMITS[platform]
    text = entry["text"].replace("{site}", report_url.rstrip("/") + "/")
    for tags in ROOT_TAGS[:2]:
        candidate = f"{text} {tags}"
        if len(candidate) <= limit:
            return [candidate]
    raise PostTooLong(f"the explainer {entry['id']!r} does not fit the limit")


def next_explainer(entries: list[dict], drafts_dir: Path | str) -> tuple[dict | None, str]:
    """The first explainer not yet drafted, or (None, why): one that still waits for approval blocks the next, so the
    approval list never fills up. When all have been drafted the first is offered again (they are evergreen)."""
    folders = sorted(Path(drafts_dir).glob("*-explainer")) if Path(drafts_dir).exists() else []
    used, waiting = [], False
    for folder in folders:
        try:
            used.append(json.loads((folder / "bluesky.json").read_text(encoding="utf-8")).get("explainer"))
        except (OSError, ValueError):
            continue
        waiting = waiting or not any((folder / f"{p}.posted.json").exists() for p in ("bluesky", "mastodon"))
    if waiting:
        return None, "an explainer draft is already waiting for approval"
    fresh = [e for e in entries if e["id"] not in used]
    return (fresh or entries or [None])[0], ""


def previous_month(today: _date | None = None) -> str:
    """The last complete calendar month, YYYY-MM (the monthly report talks about a month that is over)."""
    first = (today or datetime.now(timezone.utc).date()).replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def build_monthly(summary: dict, *, platform: str, report_url: str, month: str | None = None) -> list[str]:
    """One post on the trends of one month, from trends.summary(): `month` (YYYY-MM) when the data has it, otherwise the
    newest month. It compares only scans taken under the same conditions, with the month before it, and says so when there
    is no earlier month to compare with. Names companies, never outlets."""
    rows = summary["monthly"]
    if not rows:
        raise ValueError("no scan under comparable conditions: nothing to post")
    index = next((i for i, r in enumerate(rows) if r["month"] == month), len(rows) - 1)
    month, limit = rows[index], LIMITS[platform]
    change = None
    if index > 0 and rows[index - 1]["median_tracking"] is not None and month["median_tracking"] is not None:
        change = {"median_tracking": month["median_tracking"], "previous": rows[index - 1]["month"],
                  "delta_median": round(month["median_tracking"] - rows[index - 1]["median_tracking"], 1)}
    lead = month["leaders"][0] if month["leaders"] else None
    link = f"{report_url.rstrip('/')}/trends.html"
    if change:
        move = f"median tracking services per site {change['median_tracking']:g} ({change['delta_median']:+.1f} on {change['previous']})"
    else:
        move = f"median tracking services per site {month['median_tracking']:g} over {month['scans']} scans (first month)"
    leader = f" {lead['company']} reaches {lead['mean_pct_of_sites']:g}% of the sites." if lead else ""
    index = month["concentration_index"]
    for tail in (f"Concentration index {index} of 10,000 (low = spread over many companies). Counts, not verdicts. {link}",
                 f"Concentration index {index} of 10,000. Counts, not verdicts. {link}",
                 f"Concentration index {index}. {link}"):
        text = f"Monthly trends, {month['month']}: {move}.{leader} {tail}"
        for tags in ROOT_TAGS[:2] + ("",):
            candidate = f"{text} {tags}".strip()
            if len(candidate) <= limit:
                return [candidate]
    raise PostTooLong("the monthly post does not fit the limit")


def build_hook(date: str, reports: list[dict], *, platform: str, report_url: str, streak: int | None = None) -> list[str]:
    """One standalone post on the single most-tracked outlet of the week (the rule fixed by the maintainer).
    `streak`: how many scans in a row that outlet has led (stable_leader decides when a hook is worth posting at all).
    It names an outlet, so only a leader that stable_leader vouches for gets one."""
    rows = ranking(reports)
    if not rows:
        raise ValueError("no measured site contacted a tracking service: nothing to post")
    top, limit = rows[0], LIMITS[platform]
    row = f", {streak} scans in a row" if streak and streak > 1 else ""
    text = (f"{top['name']}: {top['tracking_services']} third-party tracking services contacted before any click, "
            f"the most among the {len(eligible(reports))} news sites measured with enough confidence{row} ({date}, from "
            f"{_vantage(reports)}). Counts, not verdicts. Data: {report_url}")
    for tags in ROOT_TAGS[:2]:
        candidate = f"{text} {tags}"
        if len(candidate) <= limit:
            return [candidate]
    raise PostTooLong("the hook post does not fit the limit")


HOOK_EVERY = 4   # a leader that keeps leading gets a hook on every 4th scan in a row, not every week


def stable_leader(runs_dir: Path | str, sites_file: Path | str = "data/sites.json") -> tuple[dict | None, list[str]]:
    """The outlet that a hook may name, or None with the reasons. It must lead the newest scan with a full reading (every
    pass measured), have been steady in its last scans (a site that swings from week to week can top the list by chance),
    and be on a streak that is a multiple of HOOK_EVERY: the weekly thread already names the leader every week, so a second
    post is only worth it for something the thread does not say."""
    from . import insights, trends as trends_module
    try:
        points = trends_module.summary(runs_dir, load_groups(sites_file))["comparable"]
    except (OSError, ValueError, KeyError):
        return None, []

    def leader(point):
        rows = sorted((r for r in point["site_rows"] if r["tracking"] > 0),
                      key=lambda r: (-r["tracking"], -r["requests"], r["name"]))
        return rows[0] if rows else None

    top = leader(points[-1]) if points else None
    if top is None:
        return None, ["no measured site contacted a tracking service"]
    streak = 0
    for point in reversed(points):
        current = leader(point)
        if current is None or current["stem"] != top["stem"]:
            break
        streak += 1
    history = insights.site_histories(points)[top["stem"]]
    recent = [h["tracking"] for h in history][-MIN_STEADY_SCANS:]
    if top.get("confidence") != "high":
        return None, [f"{top['name']} leads, but its newest reading was not a full one (all passes measured)"]
    if len(recent) < MIN_STEADY_SCANS or max(recent) - min(recent) > insights.tolerance(statistics.median(recent)):
        return None, [f"{top['name']} leads, but it has not been steady in its last {MIN_STEADY_SCANS} scans ({recent})"]
    if streak % HOOK_EVERY:
        return None, [f"{top['name']} has led {streak} scan{'s' if streak != 1 else ''} in a row: a hook goes out every "
                      f"{HOOK_EVERY}th (the weekly thread already names the leader)"]
    return {"name": top["name"], "tracking": top["tracking"], "streak": streak}, []


MIN_STEADY_SCANS = 3   # scans up to and including the one before the jump that must stay within the usual range


def confirmed_swings(runs_dir: Path | str, sites_file: Path | str = "data/sites.json") -> tuple[list[dict], list[str]]:
    """The big jumps that the newest scan has CONFIRMED: a site whose tracking-service count jumped between the two scans
    before the newest one (at least 15 services and half of the larger count) and stayed at the new level in the newest scan.

    A jump seen in one scan only is not announced: it is often the page loading differently (a consent wall, a bot check, a
    partial load), and an alert names an outlet. All three readings (before, after and the confirming scan) must be full
    ones (every pass measured), and the site must have been steady in the scans before the jump: a site that swings from week to
    week (one that serves a different page each time) makes no news by swinging again. Returns (swings, why each other jump
    was left out). Same verdicts as the Trends page."""
    from . import insights, trends as trends_module
    try:
        points = trends_module.summary(runs_dir, load_groups(sites_file))["comparable"]
    except (OSError, ValueError, KeyError):
        return [], []
    if len(points) < 3:
        return [], []
    jump_until, newest = points[-2]["date"], points[-1]["date"]
    histories = insights.site_histories(points)
    swings, reasons = [], []
    for a in insights.anomalies(points):
        if a["until"] != jump_until:
            continue
        if a["verdict"] != "confirmed":
            reasons.append(f"{a['name']}: {a['from']} to {a['to']} was not confirmed by the next scan ({a['explanation']})")
            continue
        history = histories[a["stem"]]
        before = [h["tracking"] for h in history if h["date"] <= a["since"]]
        if len(before) < MIN_STEADY_SCANS:
            reasons.append(f"{a['name']}: {a['from']} to {a['to']} held, but there are too few earlier scans to know its usual level")
            continue
        if max(before) - min(before) > insights.tolerance(statistics.median(before)):
            reasons.append(f"{a['name']}: {a['from']} to {a['to']} held, but the site already swung between {min(before)} and "
                           f"{max(before)} before: a jump is not news there")
            continue
        rows = {h["date"]: h for h in history}
        needed = (a["since"], a["until"], newest)
        if any(rows.get(day) is None or rows[day].get("confidence") != "high" for day in needed):
            reasons.append(f"{a['name']}: {a['from']} to {a['to']} held, but not every reading was a full one (all passes measured)")
            continue
        swings.append({"site": a["name"], "previous": a["from"], "current": a["to"], "previous_at": a["since"],
                       "current_at": a["until"], "held": rows[newest]["tracking"], "held_at": newest})
    return sorted(swings, key=lambda x: (-abs(x["current"] - x["previous"]), x["site"])), reasons


def vetted_changes(diffs: list[dict], runs_dir: Path | str, sites_file: Path | str = "data/sites.json") -> tuple[list[dict], list[str]]:
    """The week's per-site changes with the doubtful ones taken out of the public list (they stay on the changes page).
    A change is left out when either reading was not a full one (every pass measured); and, for a big move (at least 15
    services and half of the larger count), when the site was not steady before it or when its third-party requests moved
    the same way by half or more (the page probably loaded differently). Returns (diffs, one reason per site left out)."""
    from . import insights, trends as trends_module
    try:
        points = trends_module.summary(runs_dir, load_groups(sites_file))["comparable"]
    except (OSError, ValueError, KeyError):
        return diffs, []
    if len(points) < 2:
        return diffs, []
    old_rows = {r["name"]: r for r in points[-2]["site_rows"]}
    new_rows = {r["name"]: r for r in points[-1]["site_rows"]}
    histories = {h[-1]["name"]: h for h in insights.site_histories(points).values() if h}
    kept, reasons = [], []
    for d in diffs:
        if not (d.get("comparable") and d.get("notable")):
            kept.append(d)
            continue
        name, old, new = d["site"], old_rows.get(d["site"]), new_rows.get(d["site"])
        why = None
        if old is None or new is None or old.get("confidence") != "high" or new.get("confidence") != "high":
            why = "not every reading was a full one (all passes measured)"
        else:
            delta = new["tracking"] - old["tracking"]
            if abs(delta) >= insights.JUMP_MIN and abs(delta) >= insights.JUMP_RATIO * max(new["tracking"], old["tracking"]):
                before = [h["tracking"] for h in histories.get(name, []) if h["date"] <= points[-2]["date"]]
                moved = (new["requests"] - old["requests"]) / old["requests"] if old["requests"] else None
                if len(before) < MIN_STEADY_SCANS or max(before) - min(before) > insights.tolerance(statistics.median(before)):
                    why = "a big move on a site without a steady record, so it may be the page and not the trackers"
                elif moved is not None and abs(moved) >= insights.JUMP_RATIO and (delta < 0) == (moved < 0):
                    why = f"third-party requests moved {moved:+.0%} with it: the page probably loaded differently"
        if why:
            reasons.append(f"{name}: {why}")
            kept.append({**d, "notable": False, "left_out": why})
        else:
            kept.append(d)
    return kept, reasons


def build_alert(swings: list[dict], *, platform: str, report_url: str) -> list[str]:
    """One post on the biggest swing. It names an outlet, so it is only announced once the next scan has confirmed it."""
    if not swings:
        raise ValueError("no notable swing: nothing to post")
    limit, w = LIMITS[platform], swings[0]
    link = f"{report_url.rstrip('/')}/changes.html"
    still = f", still {w['held']} on {w['held_at']}" if w.get("held_at") else ""
    for note in ((" A consent banner or measurement difference could explain it.", "") if not still else ("",)):
        text = (f"Notable change: tracking services contacted by {w['site']} before any click went from "
                f"{w['previous']} ({w['previous_at']}) to {w['current']} ({w['current_at']}){still}. "
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


class Groups(dict):
    """site URL (without a trailing slash) -> group, exactly like the plain dict it replaces, that also carries `panels`:
    site URL -> "core" (the 48 outlets of the original list: the headline series is computed on them only) or "extended"
    (outlets added later: everywhere else, never in the headline). Code that only reads groups sees an ordinary dict."""
    panels: dict


def groups_from_entries(entries) -> Groups:
    groups = Groups()
    groups.panels = {}
    for s in entries:
        url = s["url"].rstrip("/")
        if s.get("group"):
            groups[url] = s["group"]
        groups.panels[url] = s.get("panel", "core")
    return groups


def load_groups(sites_file: Path | str) -> Groups:
    """site URL (without a trailing slash) -> group (with .panels); empty when the file is missing."""
    path = Path(sites_file)
    if not path.exists():
        return groups_from_entries([])
    data = json.loads(path.read_text(encoding="utf-8"))
    return groups_from_entries(data.get("sites", []))


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
    saved = dict(LIMITS)
    try:
        return _main(argv)
    finally:
        LIMITS.update(saved)   # --max-chars lowers a limit for this run only


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.posts", description=__doc__)
    parser.add_argument("runs_dir", nargs="?", default="data/runs")
    parser.add_argument("--platform", choices=sorted(LIMITS), default="bluesky")
    parser.add_argument("--report-url", default="https://example.org/report", help="link to the full report")
    parser.add_argument("--sites-file", default="data/sites.json", help="list of sites (gives each its country group)")
    parser.add_argument("--spain-dir", default="data/extra/local-windows-spain",
                        help="reports of the Spanish group measured from Spain (left out if missing or too old)")
    parser.add_argument("--out", default="data/drafts", help="where draft files are written")
    parser.add_argument("--no-image", action="store_true", help="do not draw the chart image")
    parser.add_argument("--max-chars", type=int, default=0, metavar="N",
                        help="write every post within N characters (never above the platform limit); the workflows pass 300 "
                             "for Mastodon so that both accounts publish the same text, inside Bluesky's limit")
    parser.add_argument("--month", default="", metavar="YYYY-MM",
                        help="monthly kind only: the month to talk about (default: the last complete month; the newest "
                             "month with data when that one has none)")
    parser.add_argument("--explainers-file", default=str(EXPLAINERS_FILE), help="explainer kind only: the list of explanations")
    parser.add_argument("--dry-run", action="store_true", help="print the draft and write nothing (no image either)")
    parser.add_argument("--min-measured", type=float, default=0.0, metavar="FRACTION",
                        help="stop (exit 2, no draft) when fewer than this share of the sites were measured well "
                             "(a broken scan must not become a post)")
    parser.add_argument("--kind", choices=("weekly", "midweek", "alert", "hook", "monthly", "explainer"), default="weekly",
                        help="weekly: the full thread; midweek: one post on the companies reaching the most sites "
                             "(written to <date>-midweek, no image); alert: one post on the biggest jump that the "
                             "newest scan has confirmed (written to <date>-alert; none when no jump is confirmed); hook: one post "
                             "on the most-tracked outlet, only when it is a steady leader on every 4th scan in a row (written to <date>-hook). Alerts and hooks are published on "
                             "their own only when the workflow switch ALERTS_AUTO is on; otherwise they wait for approval")
    args = parser.parse_args(argv)
    if args.max_chars:
        LIMITS[args.platform] = min(LIMITS[args.platform], args.max_chars)  # the builders read the limit at call time

    date, reports = load_latest_reports(args.runs_dir)
    if args.kind == "monthly":  # imported here: trends itself imports this module
        from . import trends as trends_module
        posts = build_monthly(trends_module.summary(args.runs_dir, load_groups(args.sites_file)), platform=args.platform,
                              report_url=args.report_url, month=args.month or previous_month())
        markdown = render_markdown(date, args.platform, posts, "", None)
        print(markdown)
        if not args.dry_run:
            out_dir = Path(args.out) / f"{date}-monthly"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{args.platform}.md").write_text(markdown, encoding="utf-8")
            (out_dir / f"{args.platform}.json").write_text(json.dumps({
                "date": date, "platform": args.platform, "generated_at": datetime.now(timezone.utc).isoformat(),
                "status": "draft", "posts": posts, "image": None, "alt_text": ""}, indent=2, ensure_ascii=False),
                encoding="utf-8")
        return 0
    if args.kind == "explainer":
        entries = load_explainers(args.explainers_file)
        today_draft = Path(args.out) / f"{date}-explainer" / "bluesky.json"
        chosen = None
        if args.platform != "bluesky" and today_draft.exists():   # the same explanation on every network
            try:
                chosen = next((e for e in entries if e["id"] == json.loads(today_draft.read_text(encoding="utf-8")).get("explainer")), None)
            except (OSError, ValueError):
                chosen = None
        entry, why = (chosen, "") if chosen else next_explainer(entries, args.out)
        if entry is None:
            print(f"No explainer drafted: {why or 'the explainers file is empty'}.")
            return 0
        posts = build_explainer(entry, platform=args.platform, report_url=args.report_url)
        markdown = render_markdown(date, args.platform, posts, "", None)
        print(markdown)
        if not args.dry_run:
            out_dir = Path(args.out) / f"{date}-explainer"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{args.platform}.md").write_text(markdown, encoding="utf-8")
            (out_dir / f"{args.platform}.json").write_text(json.dumps({
                "date": date, "platform": args.platform, "generated_at": datetime.now(timezone.utc).isoformat(),
                "status": "draft", "posts": posts, "image": None, "alt_text": "", "explainer": entry["id"]},
                indent=2, ensure_ascii=False), encoding="utf-8")
        return 0
    if args.kind == "midweek":
        posts = build_midweek(date, reports, platform=args.platform, report_url=args.report_url,
                              previous=previous_reports(args.runs_dir, date))
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
            swings, dropped = confirmed_swings(args.runs_dir, args.sites_file)
            for reason in dropped:
                print(f"Not alerted: {reason}")
                if not args.dry_run:
                    autonomy.note(date, "alert", reason, Path(args.out).parent / "autonomy")
            if not swings:
                print("No jump confirmed by the newest scan: no alert drafted.")
                return 0
            posts = build_alert(swings, platform=args.platform, report_url=args.report_url)
        else:
            leader, why = stable_leader(args.runs_dir, args.sites_file)
            for reason in why:
                print(f"No hook: {reason}")
                if not args.dry_run:
                    autonomy.note(date, "hook", reason, Path(args.out).parent / "autonomy")
            if leader is None:
                print("No outlet qualifies for a hook this week: no hook drafted.")
                return 0
            posts = build_hook(date, reports, platform=args.platform, report_url=args.report_url, streak=leader["streak"])
            if not posts[0].startswith(f"{leader['name']}:"):
                raise ValueError("the leader of the series is not the leader of the newest scan: no hook drafted")
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
    diffs, left_out = vetted_changes(diffs, args.runs_dir, args.sites_file)
    for reason in left_out:
        print(f"Left out of the changes post: {reason}")
        if not args.dry_run:
            autonomy.note(date, "changes", reason, Path(args.out).parent / "autonomy")
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
