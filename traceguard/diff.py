"""Week-over-week comparison of two reports for the same site.

Only changes seen in a majority of passes (`stable`) are counted, so one-off ad rotation does
not show up as news. Reports measured under different conditions are never compared.

Usage: python -m traceguard.diff data/runs
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

COMPARABLE_FIELDS = ("vantage", "locale", "timezone", "tracker_list", "interaction", "blocklist")
METRICS = ("third_party_requests", "third_party_domains", "tracking_services",
           "third_party_cookies", "cookies_total", "storage_items")


def _stable(items: list[dict], key: str, only_tracking: bool = False) -> dict[str, dict]:
    return {i[key]: i for i in items if i["stable"] and (i.get("tracking") or not only_tracking)}


def compare_reports(previous: dict, current: dict) -> dict:
    result = {"site": current["site"]["name"], "url": current["site"]["url"],
              "previous_at": previous["generated_at"], "current_at": current["generated_at"]}
    reasons = []
    if previous["site"]["url"] != current["site"]["url"]:
        reasons.append("different site URL")
    for field in COMPARABLE_FIELDS:
        if previous["measurement"].get(field) != current["measurement"].get(field):
            reasons.append(f"different {field}: {previous['measurement'].get(field)!r} vs "
                           f"{current['measurement'].get(field)!r}")
    for label, report in (("previous", previous), ("current", current)):
        if report["summary"]["status"] != "ok":
            reasons.append(f"{label} measurement status is {report['summary']['status']}")
        elif report["summary"].get("confidence") == "low":
            reasons.append(f"{label} measurement has low confidence")
    if reasons:
        return {**result, "comparable": False, "reasons": reasons}

    old, new = previous["summary"], current["summary"]
    old_services = _stable(old["services"], "service", only_tracking=True)
    new_services = _stable(new["services"], "service", only_tracking=True)
    old_domains = _stable(old["third_party_domains"], "domain")
    new_domains = _stable(new["third_party_domains"], "domain")

    added_services = [new_services[s] for s in sorted(new_services.keys() - old_services.keys())]
    removed_services = [old_services[s] for s in sorted(old_services.keys() - new_services.keys())]
    return {
        **result, "comparable": True, "reasons": [],
        "added_tracking_services": added_services,
        "removed_tracking_services": removed_services,
        "added_domains": sorted(new_domains.keys() - old_domains.keys()),
        "removed_domains": sorted(old_domains.keys() - new_domains.keys()),
        "metrics": {m: {"previous": old["metrics"][m], "current": new["metrics"][m],
                        "delta": new["metrics"][m] - old["metrics"][m]} for m in METRICS},
        "notable": bool(added_services or removed_services),
    }


def compare_directories(runs_dir: Path | str) -> list[dict]:
    """Compare every site in the newest dated folder with its most recent earlier report."""
    runs_dir = Path(runs_dir)
    dates = sorted(p for p in runs_dir.iterdir() if p.is_dir())
    if len(dates) < 2:
        return []
    *earlier, latest = dates
    results = []
    for path in sorted(latest.glob("*.json")):
        current = json.loads(path.read_text(encoding="utf-8"))
        previous_path = next((d / path.name for d in reversed(earlier) if (d / path.name).exists()), None)
        if previous_path is None:
            continue
        results.append(compare_reports(json.loads(previous_path.read_text(encoding="utf-8")), current))
    return results


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    results = compare_directories(argv[0] if argv else "data/runs")
    if not results:
        print("Nothing to compare: need at least two dated folders with a common site.")
        return 0
    for r in results:
        if not r["comparable"]:
            print(f"{r['site']}: not comparable ({'; '.join(r['reasons'])})")
            continue
        added = ", ".join(s["service"] for s in r["added_tracking_services"]) or "-"
        removed = ", ".join(s["service"] for s in r["removed_tracking_services"]) or "-"
        print(f"{r['site']}: tracking services +[{added}] -[{removed}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
