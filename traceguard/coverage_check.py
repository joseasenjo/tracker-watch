"""Emails the owner when a weekly scan measured too few of the sites.

The weekly thread is only stopped when fewer than 60% of the sites were measured (`--min-measured 0.6`), which is far below
the usual 38 to 40 of 48. A slow slide (GitHub's addresses getting blocked by more outlets) would never trip that, so this
check warns earlier: below COVERAGE_MIN usable sites (default 33) it names the sites that were lost since the scan before.

It never stops anything: the scan and the posts go on, and the e-mail is the whole effect.
"""
from __future__ import annotations

import os
import sys
from email.message import EmailMessage
from pathlib import Path

from . import autonomy, notify
from .posts import eligible, load_latest_reports

DEFAULT_MINIMUM = 33


def usable_names(reports: list[dict]) -> set[str]:
    return {r["site"]["name"] for r in eligible(reports)}


def previous_reports(runs_dir: Path | str, date: str) -> list[dict]:
    from .posts import previous_reports as earlier
    return earlier(runs_dir, date)


def assess(runs_dir: Path | str, minimum: int = DEFAULT_MINIMUM) -> dict:
    date, reports = load_latest_reports(runs_dir)
    now_ok = usable_names(reports)
    before_ok = usable_names(previous_reports(runs_dir, date))
    return {"date": date, "measured": len(now_ok), "total": len(reports), "minimum": minimum,
            "lost": sorted(before_ok - now_ok), "regained": sorted(now_ok - before_ok) if before_ok else [],
            "unmeasured": sorted(r["site"]["name"] for r in reports if r["site"]["name"] not in now_ok),
            "low": len(now_ok) < minimum}


def build_message(result: dict, run_url: str, config: dict) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = (f"[Tracker Watch] LOW COVERAGE: {result['measured']} of {result['total']} sites measured "
                          f"on {result['date']} (minimum {result['minimum']})")
    message["From"], message["To"] = config["sender"], config["to"]
    lines = [f"The scan of {result['date']} measured {result['measured']} of {result['total']} sites well; the warning level is "
             f"{result['minimum']}.", ""]
    if result["lost"]:
        lines += ["Measured the week before and not now: " + ", ".join(result["lost"]) + ".", ""]
    lines += ["Not measured this week: " + (", ".join(result["unmeasured"]) or "none") + ".", "",
              "Usually an outlet started refusing the scan (the tool identifies itself and does not try to get around blocks). "
              "The scan and the posts went on as normal; the posts say how many sites were measured.",
              "If the number keeps falling, a second place to measure from (or a different site list) is worth considering.", "",
              f"Log: {' '.join((run_url or '?').split())[:200]}", "", "This notice comes from the Tracker Watch coverage check."]
    message.set_content("\n".join(lines))
    return message


def main(argv: list[str] | None = None, env=None, *, runs_dir: str = "data/runs") -> int:
    env = os.environ if env is None else env
    try:
        minimum = int((env.get("COVERAGE_MIN") or "").strip() or DEFAULT_MINIMUM)
    except ValueError:
        print("COVERAGE_MIN must be a whole number", file=sys.stderr)
        return 2
    result = assess(runs_dir, minimum)
    print(f"{result['date']}: {result['measured']} of {result['total']} sites measured (warning below {minimum})")
    if not result["low"]:
        return 0
    if env.get("COVERAGE_RECORD_DIR"):
        autonomy.note(result["date"], "coverage", f"only {result['measured']} of {result['total']} sites were measured well "
                      f"(warning level {minimum})", env["COVERAGE_RECORD_DIR"])
    config = notify.mail_config(env)
    if config is None:
        print("not configured: no coverage notice sent", file=sys.stderr)
        return 1
    try:
        notify.send(config, build_message(result, env.get("COVERAGE_RUN_URL") or "", config))
    except Exception as exc:
        print(f"coverage notice NOT sent ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"coverage notice sent to {notify.mask(config['to'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
