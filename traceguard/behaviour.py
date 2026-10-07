"""What each tracking service did in the browser that loaded the page, taken from the raw measurement.

Nothing here is written by hand about any company: it is counted from the request log of the passes that
worked (what kind of request each service made, what it sent back to set, which script APIs its scripts
called). It describes what happened in the browser, not what the service does with the data afterwards,
which a passive measurement cannot see. Works on old reports too, because it reads the raw log.
"""
from __future__ import annotations

import statistics
from collections import Counter

from .classify import TrackerList, registrable_domain
from .findings import SCRIPT_BEHAVIOURS
from .report import passive_requests

# Playwright resource type -> plain-language kind
KIND_OF = {
    "script": "script", "image": "image",
    "xhr": "background", "fetch": "background", "ping": "background", "websocket": "background",
    "eventsource": "background", "document": "frame",
}
KINDS = ("script", "image", "background", "frame", "other")
PHRASES = {
    "script": ("script", "scripts", "ran {n} {noun} in the browser"),
    "image": ("image request", "image requests", "made {n} {noun} (often tiny “pixels”)"),
    "background": ("background request", "background requests", "made {n} {noun} to send or fetch data"),
    "frame": ("embedded frame", "embedded frames", "loaded {n} {noun}"),
    "other": ("other resource", "other resources", "loaded {n} {noun} (styles, fonts, media)"),
}


def _median(values: list[int]) -> int:
    return int(statistics.median_low(values)) if values else 0


def service_activity(report: dict, tracker_list: TrackerList) -> dict[str, dict]:
    """service -> activity, for the tracking services seen in most passes of one report."""
    summary = report["summary"]
    if summary["status"] != "ok":
        return {}
    stable = {s["service"] for s in summary["services"] if s["tracking"] and s["stable"]}
    runs = [r for r in report["runs"] if r["status"] == "ok"]
    if not stable or not runs:
        return {}
    per_run_kinds: dict[str, list[Counter]] = {s: [] for s in stable}
    per_run_bytes: dict[str, list[int]] = {s: [] for s in stable}
    cookie_passes: dict[str, Counter] = {s: Counter() for s in stable}
    cookie_info: dict[tuple[str, str], dict] = {}
    for run in runs:
        kinds = {s: Counter() for s in stable}
        sizes = {s: 0 for s in stable}
        for request in passive_requests(run):
            tracker = request.get("tracker")
            if request.get("party") != "third" or not tracker or tracker["service"] not in stable:
                continue
            kinds[tracker["service"]][KIND_OF.get(request.get("resource_type"), "other")] += 1
            sizes[tracker["service"]] += request.get("bytes") or 0
        for service in stable:
            per_run_kinds[service].append(kinds[service])
            per_run_bytes[service].append(sizes[service])
        for cookie in run["cookies"]:
            if cookie.get("party") != "third":
                continue
            owner = tracker_list.lookup(cookie["domain"])
            if owner and owner["service"] in stable:
                key = (owner["service"], cookie["name"])
                cookie_passes[owner["service"]][cookie["name"]] += 1
                cookie_info[key] = cookie
    observations = [o for o in summary.get("observations", [])
                    if o["party"] == "third" and o["stable"] and o["kind"] in SCRIPT_BEHAVIOURS]
    result = {}
    for service in sorted(stable):
        counts = {k: _median([c[k] for c in per_run_kinds[service]]) for k in KINDS}
        cookies = []
        for name, seen in sorted(cookie_passes[service].items()):
            if seen * 2 > len(runs):
                info = cookie_info[(service, name)]
                cookies.append({"name": name, "persistent": not info.get("session", False),
                                "days": info.get("lifetime_days")})
        domain = registrable_domain(service)
        result[service] = {
            "requests": counts, "total": sum(counts.values()), "bytes": _median(per_run_bytes[service]) or None,
            "cookies": cookies,
            "behaviours": sorted({o["kind"] for o in observations if o["script_domain"] == domain}),
        }
    return result


def phrases(activity: dict) -> list[str]:
    """Plain-language list of what one service did, most significant first."""
    out = []
    for kind in KINDS:
        n = activity["requests"][kind]
        if n:
            singular, plural, text = PHRASES[kind]
            out.append(text.format(n=n, noun=singular if n == 1 else plural))
    cookies = activity["cookies"]
    if cookies:
        persistent = [c for c in cookies if c["persistent"]]
        text = f"set {len(cookies)} cookie{'s' if len(cookies) != 1 else ''}"
        if persistent:
            days = [c["days"] for c in persistent if c.get("days")]
            longest = f", the longest lasting about {max(days)} days" if days else ""
            text += f" ({len(persistent)} that stay{'s' if len(persistent) == 1 else ''} after you close the browser{longest})"
        else:
            text += " (all end when you close the browser)"
        out.append(text)
    for kind in activity["behaviours"]:
        out.append(f"a script from this domain {SCRIPT_BEHAVIOURS[kind]}")
    return out


def totals(activities: dict[str, dict]) -> dict:
    return {"services": len(activities), "scripts": sum(a["requests"]["script"] for a in activities.values()),
            "requests": sum(a["total"] for a in activities.values()),
            "cookies": sum(len(a["cookies"]) for a in activities.values()),
            "persistent_cookies": sum(1 for a in activities.values() for c in a["cookies"] if c["persistent"])}


def operator_footprint(activities_by_site: dict[str, dict[str, dict]], services: set[str]) -> dict:
    """For one operator: on how many of its sites it ran scripts, made image requests, background requests,
    set cookies. activities_by_site: stem -> service -> activity."""
    footprint = {"script": 0, "image": 0, "background": 0, "frame": 0, "cookies": 0, "sites": 0}
    for activities in activities_by_site.values():
        mine = [a for s, a in activities.items() if s in services]
        if not mine:
            continue
        footprint["sites"] += 1
        for kind in ("script", "image", "background", "frame"):
            footprint[kind] += any(a["requests"][kind] for a in mine)
        footprint["cookies"] += any(a["cookies"] for a in mine)
    return footprint
