"""On-demand analysis of one address requested through a GitHub issue (designed to run in GitHub Actions).

GitHub never shows a visitor's IP address to a workflow, only the account that opened the issue, so the
limits are per account: a maximum number of requests per rolling window, a minimum account age and a daily
cap for the whole site. The workflow supplies issue data through files and environment variables, never by
pasting it into a shell command.

Usage (from the workflow):
  python -m traceguard.ondemand --body-file body.txt --author LOGIN --account-created 2020-01-01T00:00:00Z \
      --history-file history.json --out comment.md --result-file result.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .safety import UnsafeURL, check_target

PER_ACCOUNT = 2          # requests per account in the window
WINDOW_HOURS = 24
MIN_ACCOUNT_AGE_DAYS = 7
GLOBAL_PER_DAY = 30
PASSES = 2
OBSERVE_SECONDS = 10.0
URL_PATTERN = re.compile(r"https?://[^\s<>\"'`)\]]+", re.IGNORECASE)


def extract_url(body: str) -> str | None:
    """First address in an issue-form body, preferring the 'Site address' field."""
    section = body
    match = re.search(r"###\s*Site address\s*\n+(.*?)(?:\n###|\Z)", body, re.S | re.I)
    if match:
        section = match.group(1)
    found = URL_PATTERN.search(section)
    return found.group(0).rstrip(".,;") if found else None


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def check_limits(author: str, account_created: str, history: list[dict], now: datetime, *,
                 per_account: int = PER_ACCOUNT, window_hours: int = WINDOW_HOURS,
                 min_age_days: int = MIN_ACCOUNT_AGE_DAYS, global_per_day: int = GLOBAL_PER_DAY) -> tuple[bool, str]:
    """history: every request issue (including the current one) as {'author', 'created_at'}."""
    if now - _parse(account_created) < timedelta(days=min_age_days):
        return False, f"Accounts must be at least {min_age_days} days old to request an analysis."
    own = [h for h in history if h["author"].lower() == author.lower()
           and now - _parse(h["created_at"]) < timedelta(hours=window_hours)]
    if len(own) > per_account:
        return False, (f"Limit reached: at most {per_account} requests per account every {window_hours} hours. "
                       "Please try again later.")
    today = [h for h in history if now - _parse(h["created_at"]) < timedelta(hours=24)]
    if len(today) > global_per_day:
        return False, "The daily capacity for on-demand analyses has been reached. Please try again tomorrow."
    return True, ""


def _safe_host(value: str) -> str:
    return re.sub(r"[^a-z0-9.-]", "", value.lower())


def render_comment(report: dict) -> str:
    """Markdown reply. Only fixed text, numbers and sanitised host names are written."""
    s, m = report["summary"], report["measurement"]
    host = _safe_host(report["site"]["url"].split("/")[2]) if "//" in report["site"]["url"] else "the site"
    head = f"**Analysis of `{host}`**, measured from {m['vantage']} with {m['passes']} passes of {m['observe_seconds']:g} s."
    foot = ("\n\nThese are counts of what the page contacted before any interaction, not legal conclusions or "
            "statements of intent. The classification list is small and may be incomplete. This result is a "
            "single measurement, is **not** added to the public ranking, and is kept only in this issue.")
    if s["status"] != "ok":
        reason = {"blocked": "the site refused the automated browser",
                  "incomplete": "the page served an error or challenge page instead of its content"}.get(
                      s["status"], "the page could not be loaded")
        return f"{head}\n\nNo figures: {reason}. We do not try to bypass blocks.{foot}"
    from .bands import band_for
    metrics = s["metrics"]
    band = band_for(metrics["tracking_services"], s.get("confidence"))
    lines = [head, "",
             f"- Tracking services contacted: **{metrics['tracking_services']}**" + (f" (band {band})" if band else ""),
             f"- Third-party domains: {metrics['third_party_domains']}",
             f"- Third-party requests: {metrics['third_party_requests']}",
             f"- Third-party cookies: {metrics['third_party_cookies']} of {metrics['cookies_total']}",
             f"- Confidence: {s.get('confidence', 'n/a')} ({s['passes_ok']} of {s['passes_total']} passes measured)"]
    services = [x for x in s["services"] if x["tracking"] and x["stable"]]
    if services:
        lines += ["", "Tracking services seen:"]
        lines += [f"- `{_safe_host(x['service'])}`: {x['entity']}, {x['category'].replace('_', ' ')}" for x in services[:15]]
    return "\n".join(lines) + foot


def refusal_comment(reason: str) -> str:
    return f"This request was not processed. {reason}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.ondemand", description=__doc__)
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--author", required=True)
    parser.add_argument("--account-created", required=True)
    parser.add_argument("--history-file", required=True)
    parser.add_argument("--out", required=True, help="markdown comment to post")
    parser.add_argument("--result-file", help="JSON with the outcome (processed, reason)")
    parser.add_argument("--dry-run", action="store_true", help="validate and apply limits but do not scan")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    history = json.loads(Path(args.history_file).read_text(encoding="utf-8"))
    body = Path(args.body_file).read_text(encoding="utf-8")

    def finish(comment: str, processed: bool, reason: str = "") -> int:
        Path(args.out).write_text(comment, encoding="utf-8")
        if args.result_file:
            Path(args.result_file).write_text(json.dumps({"processed": processed, "reason": reason}), encoding="utf-8")
        return 0

    allowed, reason = check_limits(args.author, args.account_created, history, now)
    if not allowed:
        return finish(refusal_comment(reason), False, "limit")
    url = extract_url(body)
    if not url:
        return finish(refusal_comment("No web address was found in the request."), False, "no-url")
    try:
        url = check_target(url)
    except UnsafeURL as exc:
        return finish(refusal_comment(f"That address cannot be analysed ({exc})."), False, "unsafe-url")
    if args.dry_run:
        return finish(f"Dry run: `{url}` passed validation and limits.", True, "dry-run")

    from .scanner import scan_site  # imported here so tests run without a browser
    report = scan_site(url, passes=PASSES, observe_seconds=OBSERVE_SECONDS, vantage="github-actions-us")
    return finish(render_comment(report), True)


if __name__ == "__main__":
    sys.exit(main())
