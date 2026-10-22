"""Trends over the weekly series, and how concentrated the tracking presence is among companies.

Everything here is a pure function of the reports in data/runs (a folder per scan date): nothing is measured and nothing
is invented. Only sites measured with high or medium confidence count, and a point of the series is only comparable with
another taken under the same conditions (vantage point, passes, observation window): the series says so instead of
silently mixing them. Counts, not verdicts.

Concentration index (what it is and is not): for each scan, every company that ran a tracking service on a site in every
stable pass is "present" on that site. A company's share is its presences divided by all presences; the index is the sum of
the squared shares times 10,000 (the Herfindahl-Hirschman form: 10,000 would be a single company, a low number means the
presence is spread over many). It describes how concentrated the presence on these news sites is. It is NOT an economic
market share and says nothing about what any company does with the data.

Usage:  python -m traceguard.trends data/runs --sites-file data/sites.json --out data/trends
        (writes series.csv and monthly.json; the site build computes the same numbers on its own)
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

from .bands import BANDS, band_for
from .posts import company_reach, eligible, load_groups, measured

SERIES_COLUMNS = ["date", "vantage", "passes", "observe_seconds", "sites", "measured", "usable", "median_tracking", "mean_tracking",
                  "median_US", "median_UK", "median_DE", "median_ES", "band_A", "band_B", "band_C", "band_D", "band_E",
                  "top_company", "top_company_sites", "top_company_pct", "top3_mean_pct", "companies", "concentration_index"]
GROUP_ORDER = ("US", "UK", "DE", "ES")


def conditions(report: dict) -> tuple:
    """What must be equal for two scans to be comparable."""
    m = report.get("measurement", {})
    return (m.get("vantage"), m.get("passes"), m.get("observe_seconds"))


def concentration(reports: list[dict]) -> dict:
    """Presence of companies on the usable sites of one scan. reach: [(company, sites)] widest first."""
    usable = len(eligible(reports))
    reach = company_reach(reports)
    total = sum(n for _, n in reach)
    index = round(sum((n / total) ** 2 for _, n in reach) * 10_000) if total else 0
    top = reach[0] if reach else ("", 0)
    top3 = [n for _, n in reach[:3]]
    return {
        "usable": usable, "companies": len(reach), "presences": total, "concentration_index": index,
        "top_company": top[0], "top_company_sites": top[1],
        "top_company_pct": round(100 * top[1] / usable, 1) if usable else 0.0,
        "top3_mean_pct": round(100 * (sum(top3) / len(top3)) / usable, 1) if top3 and usable else 0.0,
        "reach": reach,
    }


def _median(values: list[float]):
    return round(statistics.median(values), 1) if values else None


def _quartiles(values: list[float]) -> dict:
    """Q1, median, Q3 and the extremes of one group of sites (inclusive method: the quartiles are values of the data's range)."""
    if not values:
        return {"n": 0, "q1": None, "median": None, "q3": None, "min": None, "max": None}
    if len(values) == 1:
        v = values[0]
        return {"n": 1, "q1": v, "median": v, "q3": v, "min": v, "max": v}
    q1, med, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return {"n": len(values), "q1": round(q1, 1), "median": round(med, 1), "q3": round(q3, 1), "min": min(values), "max": max(values)}


def point(date: str, reports: list[dict], groups: dict[str, str] | None = None, stems: list[str] | None = None) -> dict:
    """One scan as one point of the series. `stems` (the report file names) identify each site; the site URL when absent."""
    use = eligible(reports)
    counts = [r["summary"]["metrics"]["tracking_services"] for r in use]
    first = reports[0]["measurement"] if reports else {}
    by_group: dict[str, list[int]] = {}
    for r in use:
        group = (groups or {}).get(r["site"]["url"].rstrip("/"))
        if group:
            by_group.setdefault(group, []).append(r["summary"]["metrics"]["tracking_services"])
    bands = {label: 0 for label, _, _ in BANDS}
    for r in use:
        label = band_for(r["summary"]["metrics"]["tracking_services"], r["summary"].get("confidence"))
        if label:
            bands[label] += 1
    c = concentration(reports)
    names = {id(r): (stems[i] if stems else r["site"]["url"]) for i, r in enumerate(reports)}
    site_rows, presence, categories = [], {}, {}
    for r in use:
        stem = names[id(r)]
        group = (groups or {}).get(r["site"]["url"].rstrip("/"), "")
        site_rows.append({"stem": stem, "name": r["site"]["name"], "group": group,
                          "tracking": r["summary"]["metrics"]["tracking_services"],
                          "requests": r["summary"]["metrics"]["third_party_requests"], "confidence": r["summary"].get("confidence")})
        seen_categories = set()
        for service in r["summary"].get("services", []):
            if service.get("tracking") and service.get("stable"):
                presence.setdefault(service["entity"], []).append(stem)
                seen_categories.add(service.get("category") or "other")
        for category in seen_categories:
            categories[category] = categories.get(category, 0) + 1
    quartiles = {g: _quartiles(v) for g, v in sorted(by_group.items())}
    quartiles["All"] = _quartiles(counts)
    return {
        "date": date, "vantage": first.get("vantage", ""), "passes": first.get("passes"), "observe_seconds": first.get("observe_seconds"),
        "conditions": conditions(reports[0]) if reports else (None, None, None),
        "sites": len(reports), "measured": len(measured(reports)), "usable": len(use),
        "median_tracking": _median(counts), "mean_tracking": round(statistics.mean(counts), 1) if counts else None,
        "by_group": {g: _median(v) for g, v in sorted(by_group.items())},
        "bands": bands, **{k: c[k] for k in ("top_company", "top_company_sites", "top_company_pct", "top3_mean_pct", "companies",
                                              "concentration_index")},
        "reach": c["reach"][:10], "reach_all": dict(c["reach"]),
        "site_rows": sorted(site_rows, key=lambda r: r["stem"]), "quartiles": quartiles, "categories": categories,
        "presence": {company: sorted(set(stems_)) for company, stems_ in sorted(presence.items())},
    }


def load_series(runs_dir: Path | str, groups: dict[str, str] | None = None) -> list[dict]:
    """One point per scan folder, oldest first. A folder with no usable site is left out."""
    series = []
    for folder in sorted(p for p in Path(runs_dir).iterdir() if p.is_dir()):
        files = sorted(folder.glob("*.json"))
        reports = [json.loads(p.read_text(encoding="utf-8")) for p in files]
        if reports and eligible(reports):
            series.append(point(folder.name, reports, groups, [p.stem for p in files]))
    return series


def comparable(series: list[dict]) -> list[dict]:
    """The part of the series taken under the conditions of the newest scan (the only part that can be compared)."""
    if not series:
        return []
    latest = series[-1]["conditions"]
    return [p for p in series if p["conditions"] == latest]


def monthly(series: list[dict]) -> list[dict]:
    """One row per calendar month, from the comparable scans only: how many scans, and the averages of their figures."""
    rows: dict[str, list[dict]] = {}
    for p in comparable(series):
        rows.setdefault(p["date"][:7], []).append(p)
    result = []
    for month, points in sorted(rows.items()):
        medians = [p["median_tracking"] for p in points if p["median_tracking"] is not None]
        groups: dict[str, list[float]] = {}
        for p in points:
            for g, v in p["by_group"].items():
                if v is not None:
                    groups.setdefault(g, []).append(v)
        reach: dict[str, list[int]] = {}
        for p in points:
            for company, n in p["reach"]:
                reach.setdefault(company, []).append(100 * n / p["usable"] if p["usable"] else 0)
        leaders = sorted(((c, statistics.mean(v)) for c, v in reach.items()), key=lambda kv: (-kv[1], kv[0]))
        result.append({
            "month": month, "scans": len(points), "first_scan": points[0]["date"], "last_scan": points[-1]["date"],
            "median_tracking": round(statistics.mean(medians), 1) if medians else None,
            "by_group": {g: round(statistics.mean(v), 1) for g, v in sorted(groups.items())},
            "concentration_index": round(statistics.mean(p["concentration_index"] for p in points)),
            "leaders": [{"company": c, "mean_pct_of_sites": round(v, 1)} for c, v in leaders[:3]],
            "usable_sites_mean": round(statistics.mean(p["usable"] for p in points), 1),
        })
    return result


def month_over_month(rows: list[dict]) -> dict | None:
    """The newest month against the one before it, or None when there is only one month."""
    if len(rows) < 2:
        return None
    new, old = rows[-1], rows[-2]
    delta = None if new["median_tracking"] is None or old["median_tracking"] is None else round(new["median_tracking"] - old["median_tracking"], 1)
    return {"month": new["month"], "previous": old["month"], "median_tracking": new["median_tracking"],
            "previous_median_tracking": old["median_tracking"], "delta_median": delta,
            "concentration_index": new["concentration_index"], "previous_concentration_index": old["concentration_index"],
            "delta_concentration": new["concentration_index"] - old["concentration_index"]}


def series_rows(series: list[dict]) -> list[list]:
    out = []
    for p in series:
        out.append([p["date"], p["vantage"], p["passes"], p["observe_seconds"], p["sites"], p["measured"], p["usable"],
                    p["median_tracking"], p["mean_tracking"], *[p["by_group"].get(g, "") for g in GROUP_ORDER],
                    *[p["bands"].get(label, 0) for label, _, _ in BANDS], p["top_company"], p["top_company_sites"],
                    p["top_company_pct"], p["top3_mean_pct"], p["companies"], p["concentration_index"]])
    return out


def write_series_csv(series: list[dict], path: Path | str) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SERIES_COLUMNS)
        writer.writerows(series_rows(series))


def summary(runs_dir: Path | str, groups: dict[str, str] | None = None) -> dict:
    """Everything the site, the monthly post and the data release need, in one dict."""
    series = load_series(runs_dir, groups)
    rows = monthly(series)
    return {"series": series, "comparable": comparable(series), "monthly": rows, "change": month_over_month(rows),
            "excluded": len(series) - len(comparable(series))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.trends", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs_dir", nargs="?", default="data/runs")
    parser.add_argument("--sites-file", default="data/sites.json")
    parser.add_argument("--out", default="data/trends")
    args = parser.parse_args(argv)
    result = summary(args.runs_dir, load_groups(args.sites_file))
    if not result["series"]:
        print("no usable scan in", args.runs_dir)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_series_csv(result["series"], out / "series.csv")
    (out / "monthly.json").write_text(json.dumps({"monthly": result["monthly"], "change": result["change"],
                                                  "scans": len(result["series"]), "excluded_scans": result["excluded"]},
                                                 indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(result['series'])} scans, {len(result['monthly'])} month(s) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
