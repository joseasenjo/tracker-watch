"""Reach by operator: how many of the measured sites contact a given company's tracking services.

Only services classified as tracking (advertising, analytics, social, session replay, audience
measurement) that were seen in most passes count. Operators are named as in the classification list;
subsidiaries are not merged into their parents. A site that was not measured (or measured with low
confidence) is left out of the denominator, so the share is "of the sites we could measure".
"""
from __future__ import annotations

from .behaviour import operator_footprint

USABLE_CONFIDENCE = ("high", "medium")


def reach(reports: dict[str, dict], meta: dict[str, dict], inferred_only: set[str] | None = None,
          activities: dict[str, dict[str, dict]] | None = None) -> dict:
    """reports: stem -> report for one dated measurement. meta: sites.json entries by URL.
    activities: stem -> service -> activity (behaviour.service_activity), to describe what each operator did."""
    inferred_only = inferred_only or set()
    measured = {stem: r for stem, r in reports.items()
                if r["summary"]["status"] == "ok" and r["summary"].get("confidence") in USABLE_CONFIDENCE}
    groups: dict[str, int] = {}
    for stem, r in measured.items():
        group = meta.get(r["site"]["url"], {}).get("group", "Other")
        groups[group] = groups.get(group, 0) + 1

    operators: dict[str, dict] = {}
    for stem, r in measured.items():
        group = meta.get(r["site"]["url"], {}).get("group", "Other")
        for service in r["summary"]["services"]:
            if not (service["tracking"] and service["stable"]):
                continue
            op = operators.setdefault(service["entity"], {
                "entity": service["entity"], "sites": {}, "services": set(), "categories": set()})
            op["sites"][stem] = {"stem": stem, "name": r["site"]["name"], "group": group}
            op["services"].add(service["service"])
            op["categories"].add(service["category"])

    rows = []
    for op in operators.values():
        sites = sorted(op["sites"].values(), key=lambda s: s["name"])
        by_group = {g: sum(1 for s in sites if s["group"] == g) for g in groups}
        rows.append({
            "entity": op["entity"], "n": len(sites), "share": round(100 * len(sites) / len(measured)),
            "sites": sites, "services": sorted(op["services"]),
            "categories": sorted(op["categories"]), "by_group": by_group,
            "inferred": op["entity"] in inferred_only,
            "footprint": operator_footprint({s["stem"]: activities.get(s["stem"], {}) for s in sites}, op["services"])
            if activities is not None else None,
        })
    rows.sort(key=lambda r: (-r["n"], r["entity"]))
    return {"measured": len(measured), "groups": groups, "rows": rows,
            "operators": len(rows), "top": rows[:10]}

SPREAD_BELOW, CONCENTRATED_FROM = 1000, 2500  # a reading aid for the page, not a standard threshold


def index_reading(index: int) -> str:
    """A plain-words reading of the concentration index (see trends.py for what it measures)."""
    if index < SPREAD_BELOW:
        return "the presence is spread over many companies: the leaders stand out by reach, not because the rest do not count"
    if index < CONCENTRATED_FROM:
        return "the presence is moderately concentrated in a smaller group of companies"
    return "the presence is concentrated in a few companies"


def leader_story(reports: dict[str, dict], entities: dict, *, category_label=lambda c: c, top_services: int = 3) -> dict | None:
    """What stands behind the number of the company that reaches the most sites: through which services and in which
    categories, how it compares with the next ones, and the concentration index of the same scan. Everything comes from
    the reports of one measurement, so the explanation on the page changes with the data. None when no company was seen."""
    rows = entities["rows"]
    if not rows:
        return None
    from collections import Counter
    from .trends import concentration
    leader = rows[0]
    usable = {stem: r for stem, r in reports.items()
              if r["summary"]["status"] == "ok" and r["summary"].get("confidence") in USABLE_CONFIDENCE}
    services: Counter = Counter()
    categories: Counter = Counter()
    for site in leader["sites"]:
        seen = set()
        for service in usable[site["stem"]]["summary"]["services"]:
            if service["entity"] == leader["entity"] and service["tracking"] and service["stable"]:
                services[service["service"]] += 1
                seen.add(service["category"])
        categories.update(seen)
    ranked_categories = sorted(categories.items(), key=lambda kv: (-kv[1], kv[0]))
    index = concentration(list(reports.values()))["concentration_index"]
    return {
        "leader": leader["entity"], "sites": leader["n"], "measured": entities["measured"],
        "pct": round(100 * leader["n"] / entities["measured"], 1) if entities["measured"] else 0.0,
        "operators": entities["operators"],
        "next": [{"entity": r["entity"], "n": r["n"]} for r in rows[1:3]],
        "with_any_tracking": sum(1 for r in usable.values() if r["summary"]["metrics"]["tracking_services"] > 0),
        "services": [{"service": name, "sites": n} for name, n in sorted(services.items(), key=lambda kv: (-kv[1], kv[0]))[:top_services]],
        "categories": [{"key": key, "label": category_label(key), "sites": n} for key, n in ranked_categories],
        "dominant": category_label(ranked_categories[0][0]) if ranked_categories else "",
        "dominant_key": ranked_categories[0][0] if ranked_categories else "",
        "index": index, "reading": index_reading(index), "inferred": bool(leader.get("inferred")),
    }


def inferred_entities(tracker_rows: list[dict]) -> set[str]:
    """Entities whose every entry in the list is marked as inferred (no `evidence`-free entry)."""
    by_entity: dict[str, list[bool]] = {}
    for row in tracker_rows:
        by_entity.setdefault(row["entity"], []).append(bool(row.get("evidence")))
    return {entity for entity, flags in by_entity.items() if all(flags)}


def horizontal_bars(rows: list[dict], measured: int, *, width: int = 640, label_width: int = 170, limit: int = 12) -> str:
    """Inline SVG bar chart of the operators reaching the most sites. Colours come from CSS classes."""
    from markupsafe import Markup, escape
    rows = rows[:limit]
    if not rows or not measured:
        return ""
    bar_w = width - label_width - 70
    row_h = 24
    parts = []
    for i, r in enumerate(rows):
        y = 4 + i * row_h
        w = max(2, bar_w * r["n"] / measured)
        parts.append(f'<text class="bars-label" x="0" y="{y + 13}">{escape(r["entity"][:26])}</text>')
        parts.append(f'<rect class="bars-a" x="{label_width}" y="{y}" width="{w:.1f}" height="16" rx="3"/>')
        parts.append(f'<text class="bars-num" x="{label_width + w + 6:.1f}" y="{y + 12}">{r["n"]} of {measured}</text>')
    label = "Sites reached by each operator: " + "; ".join(f"{r['entity']} {r['n']} of {measured}" for r in rows)
    return Markup(f'<svg class="bars" viewBox="0 0 {width} {len(rows) * row_h + 8}" role="img" '
                  f'aria-label="{escape(label)}">{"".join(parts)}</svg>')
