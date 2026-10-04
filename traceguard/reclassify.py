"""Re-apply a (new) classification list to reports that were already measured, without scanning again.

Each pass stores the host of every request, so the summary, findings and bands can be recomputed from the
raw log. Everything else (requests, cookies, observations, time of measurement) is kept as it was.

Usage:
  python -m traceguard.reclassify data/runs/2026-10-04 --out /some/other/folder
  python -m traceguard.reclassify data/runs/2026-10-04 --in-place
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .classify import TrackerList
from .report import build_report


def reclassify_report(report: dict, tracker_list: TrackerList, now: datetime | None = None) -> dict:
    report = copy.deepcopy(report)
    for run in report["runs"]:
        for request in run["requests"]:
            if "host" in request:
                request["tracker"] = tracker_list.lookup(request["host"])
    measurement = dict(report["measurement"], tracker_list=tracker_list.source)
    new = build_report(report["site"], measurement, report["runs"], tracker_list)
    new["generated_at"] = report["generated_at"]  # the measurement itself is unchanged
    new["reclassified_at"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return new


def reclassify_folder(source: Path, tracker_list: TrackerList, target: Path) -> list[tuple[str, int | None, int | None]]:
    """Returns (site, tracking services before, after) for every report."""
    target.mkdir(parents=True, exist_ok=True)
    changes = []
    for path in sorted(source.glob("*.json")):
        old = json.loads(path.read_text(encoding="utf-8"))
        new = reclassify_report(old, tracker_list)
        (target / path.name).write_text(json.dumps(new, indent=2, ensure_ascii=False), encoding="utf-8")
        before = old["summary"].get("metrics", {}).get("tracking_services")
        after = new["summary"].get("metrics", {}).get("tracking_services")
        changes.append((old["site"]["name"], before, after))
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.reclassify", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", help="dated folder with reports, e.g. data/runs/2026-10-04")
    parser.add_argument("--tracker-list", help="classification list (default: data/trackers.seed.json)")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--out", help="write the recomputed reports to this folder")
    where.add_argument("--in-place", action="store_true", help="overwrite the reports in the folder")
    args = parser.parse_args(argv)
    source = Path(args.folder)
    target = source if args.in_place else Path(args.out)
    changes = reclassify_folder(source, TrackerList.load(args.tracker_list), target)
    for name, before, after in changes:
        if before != after:
            print(f"{name}: {before} -> {after}")
    print(f"{len(changes)} reports written to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
