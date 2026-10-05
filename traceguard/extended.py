"""Site views of the two optional measurements: the consent test and the blocking-list test.

Both live in their own folders, never in the main weekly series:
  data/consent/<origin>/<date>/<site>.json     python -m traceguard --consent reject,accept ...
  data/protected/<origin>/<date>/<site>.json   python -m traceguard --blocklist easyprivacy.txt ...
"""
from __future__ import annotations

import statistics
from pathlib import Path

from .origins import _closest_date, load_extra

MODES = ("reject", "accept")


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _mode_cell(consent: dict | None) -> dict:
    if not consent:
        return {"outcome": "not_run"}
    cell = {"outcome": consent["outcome"], "cmp": consent.get("cmp"), "button": consent.get("button_text"),
            "paid": consent.get("paid_option")}
    if consent["outcome"] == "clicked":
        m = consent["metrics"]
        cell.update(before=m["tracking_services_before"], after=m["tracking_services_after"],
                    new=m["tracking_services_new_after"], cookies_before=m["third_party_cookies_before"],
                    cookies_after=m["third_party_cookies_after"],
                    services_new=[s["service"] for s in consent["services_new_after"]],
                    services_after=[s["service"] for s in consent["services_after"]],
                    seconds=consent.get("after_seconds"))
    return cell


def consent_view(consent_dir: Path | str, labels: dict[str, str], meta: dict[str, dict]) -> dict:
    """Latest consent measurement of every origin, one row per site."""
    data = load_extra(consent_dir)
    origins, by_stem = [], {}
    for vantage, by_date in data.items():
        date = max(by_date)
        label = labels.get(vantage, vantage)
        rows = []
        for stem, report in sorted(by_date[date].items(), key=lambda kv: kv[1]["site"]["name"]):
            summary = report["summary"]
            consent = summary.get("consent", {})
            row = {"stem": stem, "name": report["site"]["name"], "status": summary["status"],
                   "group": meta.get(report["site"]["url"], {}).get("group", "Other"),
                   "passive": summary.get("metrics", {}).get("tracking_services"),
                   **{mode: _mode_cell(consent.get(mode)) for mode in MODES}}
            row["cmp"] = row["reject"].get("cmp") or row["accept"].get("cmp")
            row["banner"] = any(row[m]["outcome"] not in ("no_banner", "not_run", "not_measured") for m in MODES)
            rows.append(row)
            by_stem.setdefault(stem, []).append({"label": label, "date": date, **row})
        ok = [r for r in rows if r["status"] == "ok"]
        rejected = [r for r in ok if r["reject"]["outcome"] == "clicked"]
        accepted = [r for r in ok if r["accept"]["outcome"] == "clicked"]
        origins.append({
            "vantage": vantage, "label": label, "date": date, "rows": rows,
            "modes": [m for m in MODES if any(r[m]["outcome"] != "not_run" for r in rows)],
            "stats": {
                "measured": len(ok), "banner": sum(1 for r in ok if r["banner"]),
                "reject_clicked": len(rejected),
                "reject_not_found": sum(1 for r in ok if r["reject"]["outcome"] == "no_button"),
                "pay_or_accept": sum(1 for r in ok if r["reject"]["outcome"] == "no_button" and r["reject"].get("paid")),
                "still_tracking_after_reject": sum(1 for r in rejected if r["reject"]["after"]),
                "new_after_reject": sum(1 for r in rejected if r["reject"]["new"]),
                "median_after_reject": _median(r["reject"]["after"] for r in rejected),
                "accept_clicked": len(accepted),
                "median_after_accept": _median(r["accept"]["after"] for r in accepted),
                "median_new_after_accept": _median(r["accept"]["new"] for r in accepted),
            }})
    return {"origins": origins, "by_stem": by_stem, "has_data": any(o["stats"]["measured"] for o in origins)}


def _figures(report: dict | None) -> dict | None:
    if not report or report["summary"]["status"] != "ok":
        return None
    m = report["summary"]["metrics"]
    return {"tracking": m["tracking_services"], "requests": m["third_party_requests"],
            "domains": m["third_party_domains"], "bytes": m.get("third_party_bytes")}


def _reduction(before, after):
    if before is None or after is None or not before:
        return None
    return round(100 * (before - after) / before)


def protection_view(protected_dir: Path | str, baselines: dict[str, dict[str, dict[str, dict]]],
                    labels: dict[str, str], meta: dict[str, dict]) -> dict:
    """Latest blocking-list measurement of every origin, paired with the passive measurement of the same
    origin taken closest in time (at most a week apart). baselines: vantage -> date -> stem -> report."""
    data = load_extra(protected_dir)
    origins, by_stem = [], {}
    for vantage, by_date in data.items():
        date = max(by_date)
        label = labels.get(vantage, vantage)
        base_dates = baselines.get(vantage, {})
        base_date = _closest_date(date, list(base_dates)) if base_dates else None
        base = base_dates.get(base_date, {}) if base_date else {}
        rows, blocklist = [], None
        for stem, report in sorted(by_date[date].items(), key=lambda kv: kv[1]["site"]["name"]):
            blocklist = blocklist or report["measurement"].get("blocklist")
            protected, passive = _figures(report), _figures(base.get(stem))
            row = {"stem": stem, "name": report["site"]["name"],
                   "group": meta.get(report["site"]["url"], {}).get("group", "Other"),
                   "protected": protected, "passive": passive,
                   "blocked": report["summary"].get("protection", {}).get("blocked_requests"),
                   "paired": bool(protected and passive)}
            if row["paired"]:
                row["requests_cut"] = _reduction(passive["requests"], protected["requests"])
                row["bytes_cut"] = _reduction(passive["bytes"], protected["bytes"])
                row["tracking_cut"] = passive["tracking"] - protected["tracking"]
            rows.append(row)
            by_stem.setdefault(stem, []).append({"label": label, "date": date, "base_date": base_date, **row})
        paired = [r for r in rows if r["paired"]]
        origins.append({
            "vantage": vantage, "label": label, "date": date, "base_date": base_date, "rows": rows,
            "blocklist": blocklist or {},
            "stats": {
                "paired": len(paired),
                "median_requests_cut": _median(r["requests_cut"] for r in paired),
                "median_bytes_cut": _median(r["bytes_cut"] for r in paired),
                "median_tracking_passive": _median(r["passive"]["tracking"] for r in paired),
                "median_tracking_protected": _median(r["protected"]["tracking"] for r in paired),
                "zero_tracking_protected": sum(1 for r in paired if r["protected"]["tracking"] == 0),
            }})
    return {"origins": origins, "by_stem": by_stem, "has_data": any(o["stats"]["paired"] for o in origins)}
