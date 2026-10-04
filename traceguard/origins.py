"""Compare the same sites measured from different places.

The main weekly series lives in data/runs/<date>/. Additional origins live in
data/extra/<vantage>/<date>/<site>.json (same report format), for example a scan run on a home PC:

  python -m traceguard --sites-file data/sites.json --vantage local-windows-spain --out data/extra/local-windows-spain
"""
from __future__ import annotations

import json
import statistics
from datetime import date as Date
from pathlib import Path

MAX_DAYS_APART = 7
CONSENT_CATEGORY = "consent_management"


def load_extra(extra_dir: Path | str) -> dict[str, dict[str, dict[str, dict]]]:
    """vantage -> date -> stem -> report."""
    extra = Path(extra_dir)
    result: dict[str, dict[str, dict[str, dict]]] = {}
    if not extra.exists():
        return result
    for vantage_dir in sorted(p for p in extra.iterdir() if p.is_dir()):
        dates = {}
        for day in sorted(p for p in vantage_dir.iterdir() if p.is_dir()):
            dates[day.name] = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(day.glob("*.json"))}
        if dates:
            result[vantage_dir.name] = dates
    return result


def _closest_date(target: str, dates: list[str]) -> str | None:
    base = Date.fromisoformat(target)
    best = min(dates, key=lambda d: abs((Date.fromisoformat(d) - base).days), default=None)
    if best is None or abs((Date.fromisoformat(best) - base).days) > MAX_DAYS_APART:
        return None
    return best


def _cell(report: dict | None, day: str | None) -> dict:
    if report is None:
        return {"status": "none", "tracking": None, "consent": None, "date": day}
    s = report["summary"]
    if s["status"] != "ok":
        return {"status": s["status"], "tracking": None, "consent": None, "date": day}
    consent = any(x["category"] == CONSENT_CATEGORY and x["stable"] for x in s["services"])
    return {"status": "ok", "tracking": s["metrics"]["tracking_services"], "consent": consent, "date": day}


def compare(primary_date: str, primary: dict[str, dict], primary_label: str,
            extra: dict[str, dict[str, dict[str, dict]]], labels: dict[str, str],
            meta: dict[str, dict] | None = None) -> dict:
    """Rows for every site, one cell per origin (the primary one first)."""
    meta = meta or {}
    origins = [primary_label] + [labels.get(v, v) for v in extra]
    rows = []
    for stem, report in primary.items():
        cells = {primary_label: _cell(report, primary_date)}
        for vantage, by_date in extra.items():
            day = _closest_date(primary_date, list(by_date))
            cells[labels.get(vantage, vantage)] = _cell(by_date[day].get(stem) if day else None, day)
        counts = [c["tracking"] for c in cells.values() if c["tracking"] is not None]
        gap = (max(counts) - min(counts)) if len(counts) >= 2 else None
        rows.append({"stem": stem, "name": report["site"]["name"],
                     "group": meta.get(report["site"]["url"], {}).get("group", "Other"),
                     "cells": cells, "gap": gap})
    rows.sort(key=lambda r: (r["gap"] is None, -(r["gap"] or 0), r["name"]))
    compared = [r for r in rows if r["gap"] is not None]
    higher_first = 0
    if len(origins) >= 2:
        higher_first = sum(1 for r in compared if (r["cells"][origins[0]]["tracking"] or 0) > (r["cells"][origins[1]]["tracking"] or 0))
    return {
        "origins": origins, "rows": rows, "compared": len(compared), "has_data": len(origins) >= 2 and bool(compared),
        "median_gap": statistics.median(r["gap"] for r in compared) if compared else None,
        "max_gap": max((r["gap"] for r in compared), default=None),
        "higher_in_first": higher_first,
        "differ": sum(1 for r in compared if r["gap"] > 0),
        "dates": {label: sorted({c["date"] for r in rows for l, c in r["cells"].items() if l == label and c["date"]})
                  for label in origins},
    }


def paired_bars(rows: list[dict], origins: list[str], *, width: int = 640, label_width: int = 150) -> str:
    """Inline SVG with one pair of bars per row (first two origins). Colours come from CSS classes."""
    from markupsafe import Markup, escape
    rows = [r for r in rows if r["gap"] is not None][:10]
    if not rows or len(origins) < 2:
        return ""
    first, second = origins[0], origins[1]
    top = max(max(r["cells"][first]["tracking"], r["cells"][second]["tracking"]) for r in rows) or 1
    bar_w = width - label_width - 40
    row_h, pad = 34, 6
    height = len(rows) * row_h + 28
    parts = [f'<text class="bars-legend" x="{label_width}" y="14"><tspan class="bars-key-a">■</tspan> {escape(first)}   '
             f'<tspan class="bars-key-b">■</tspan> {escape(second)}</text>']
    for i, r in enumerate(rows):
        y = 24 + i * row_h
        a, b = r["cells"][first]["tracking"], r["cells"][second]["tracking"]
        parts.append(f'<text class="bars-label" x="0" y="{y + 14}">{escape(r["name"][:22])}</text>')
        parts.append(f'<rect class="bars-a" x="{label_width}" y="{y}" width="{max(2, bar_w * a / top):.1f}" height="11" rx="3"/>')
        parts.append(f'<text class="bars-num" x="{label_width + max(2, bar_w * a / top) + 5:.1f}" y="{y + 10}">{a}</text>')
        parts.append(f'<rect class="bars-b" x="{label_width}" y="{y + 13}" width="{max(2, bar_w * b / top):.1f}" height="11" rx="3"/>')
        parts.append(f'<text class="bars-num" x="{label_width + max(2, bar_w * b / top) + 5:.1f}" y="{y + 23}">{b}</text>')
    label = "Tracking services contacted from each origin: " + "; ".join(
        f"{r['name']}: {first} {r['cells'][first]['tracking']}, {second} {r['cells'][second]['tracking']}" for r in rows)
    return Markup(f'<svg class="bars" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">{"".join(parts)}</svg>')
