"""Reach by operator: how many of the measured sites contact a given company's tracking services.

Only services classified as tracking (advertising, analytics, social, session replay, audience
measurement) that were seen in most passes count. Operators are named as in the classification list;
subsidiaries are not merged into their parents. A site that was not measured (or measured with low
confidence) is left out of the denominator, so the share is "of the sites we could measure".
"""
from __future__ import annotations

USABLE_CONFIDENCE = ("high", "medium")


def reach(reports: dict[str, dict], meta: dict[str, dict], inferred_only: set[str] | None = None) -> dict:
    """reports: stem -> report for one dated measurement. meta: sites.json entries by URL."""
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
        })
    rows.sort(key=lambda r: (-r["n"], r["entity"]))
    return {"measured": len(measured), "groups": groups, "rows": rows,
            "operators": len(rows), "top": rows[:10]}


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
