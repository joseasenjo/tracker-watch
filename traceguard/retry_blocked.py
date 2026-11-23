"""Second attempt, on another day and another GitHub machine, for the sites the weekly scan could not measure.

Some outlets answer 403 to one GitHub machine and not to the next: AS, Associated Press, El País and The New York Times
swung between "blocked" and "measured" from one week to the next. A second attempt a day later reaches them from a different
address. This is the same honest browser asking again: it does not change its identity, send other headers or use proxies.

Rules:
  - only the newest scan folder, and only when it is at most MAX_AGE_DAYS old;
  - only sites that were not measured well (status other than ok, or low confidence);
  - sites that were not measured in the SKIP_AFTER scans before are not retried (Daily Mail, Sky News ...): asking again
    every week a site that always refuses is only noise for them and for us;
  - a retry replaces the report ONLY when it is usable (ok with high or medium confidence); the replaced report records
    what the first attempt was (`retry`), and the autonomy log says which sites were recovered, so nothing is hidden.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import autonomy

MAX_AGE_DAYS = 3
SKIP_AFTER = 4   # scans in a row without a usable reading before this one


def usable(report: dict) -> bool:
    summary = report.get("summary", {})
    return summary.get("status") == "ok" and summary.get("confidence", "high") in ("high", "medium")


def _load(folder: Path) -> dict[str, dict]:
    found = {}
    for path in sorted(folder.glob("*.json")):
        try:
            found[path.name] = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
    return found


def candidates(runs_dir: Path | str, today: date, *, max_age_days: int = MAX_AGE_DAYS, skip_after: int = SKIP_AFTER):
    """(scan folder, [(file name, report)]) of the sites worth a second attempt; (None, []) when nothing is due."""
    runs = Path(runs_dir)
    folders = sorted(p for p in runs.iterdir() if p.is_dir()) if runs.exists() else []
    if not folders:
        return None, []
    newest = folders[-1]
    try:
        age = (today - date.fromisoformat(newest.name)).days
    except ValueError:
        return None, []
    if age < 0 or age > max_age_days:
        return None, []
    earlier = [_load(p) for p in folders[-1 - skip_after:-1]]
    picked = []
    for name, report in _load(newest).items():
        if usable(report) or "retry" in report:
            continue
        if len(earlier) == skip_after and all(name in scan and not usable(scan[name]) for scan in earlier):
            continue   # refused again and again: not asked a fifth time
        picked.append((name, report))
    return newest, picked


def run(runs_dir: Path | str, autonomy_dir: Path | str, *, today: date | None = None, scan=None, log=print) -> int:
    today = today or datetime.now(timezone.utc).date()
    folder, picked = candidates(runs_dir, today)
    if folder is None:
        log("no scan from the last three days: nothing to retry")
        return 0
    if not picked:
        log(f"{folder.name}: every site that is worth a second attempt was already measured")
        return 0
    if scan is None:
        from .classify import TrackerList
        from .scanner import scan_site
        tracker_list = TrackerList.load(None)

        def scan(report: dict) -> dict:
            site, measure = report["site"], report["measurement"]
            return scan_site(site["url"], name=site["name"], first_party_domains=site["first_party_domains"],
                             passes=measure["passes"], observe_seconds=measure["observe_seconds"],
                             min_requests=measure["min_requests"], pause_seconds=measure["pause_seconds"],
                             vantage=measure["vantage"], locale=measure["locale"], timezone=measure["timezone"],
                             tracker_list=tracker_list)
    recovered, still = [], []
    for index, (name, old) in enumerate(picked):
        try:
            new = scan(old)
        except Exception as exc:   # one site failing must not stop the others
            log(f"{old['site']['name']}: the second attempt failed ({type(exc).__name__})")
            still.append(old["site"]["name"])
            continue
        if usable(new):
            old_summary = old["summary"]
            new["retry"] = {"first_attempt": {"status": old_summary.get("status"), "http_status": old_summary.get("http_status"),
                                              "confidence": old_summary.get("confidence")},
                            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            (folder / name).write_text(json.dumps(new, indent=2, ensure_ascii=False), encoding="utf-8")
            recovered.append(new["site"]["name"])
            log(f"{new['site']['name']}: measured on the second attempt")
        else:
            still.append(old["site"]["name"])
            log(f"{old['site']['name']}: still not measurable ({new['summary'].get('status')})")
    if recovered:
        autonomy.note(folder.name, "retry", "Measured on a second attempt the next day: " + ", ".join(sorted(recovered)), autonomy_dir)
    if still:
        autonomy.note(folder.name, "retry", "Still not measurable after a second attempt: " + ", ".join(sorted(still)), autonomy_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    runs_dir = args[0] if args else "data/runs"
    return run(runs_dir, Path(runs_dir).parent / "autonomy")


if __name__ == "__main__":
    raise SystemExit(main())
