"""On-demand analysis of one address requested through a GitHub issue (runs in GitHub Actions).

There is no approval step: anyone with a GitHub account can ask, and the limits in data/limits.json (editable
from the local dashboard) decide whether the request is processed. GitHub never shows a visitor's IP address
to a workflow, only the account that opened the issue, so the limits are per account: a maximum number of
requests per rolling window, a minimum account age, a daily cap for the whole site, a list of blocked
accounts and an on/off switch. The workflow supplies issue data through files and environment variables,
never by pasting it into a shell command.

Usage (from the workflow):
  python -m traceguard.ondemand --body-file body.txt --author LOGIN --account-created 2020-01-01T00:00:00Z \
      --history-file history.json --limits data/limits.json --out comment.md --result-file result.json
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .limits import DEFAULTS, LimitsError, check_account, contact_where, load_limits
from .safety import UnsafeURL, check_target

PER_ACCOUNT = DEFAULTS["scan"]["per_account"]  # kept for callers that use the defaults
WINDOW_HOURS = DEFAULTS["scan"]["window_hours"]
MIN_ACCOUNT_AGE_DAYS = DEFAULTS["scan"]["min_account_age_days"]
GLOBAL_PER_DAY = DEFAULTS["scan"]["global_per_day"]
PASSES = 2
OBSERVE_SECONDS = 10.0
RESULT_MARK = "tw-result"  # the hidden block the website reads to show a result on its own page
URL_PATTERN = re.compile(r"https?://[^\s<>\"'`)\]]+", re.IGNORECASE)


def extract_url(body: str) -> str | None:
    """First address in an issue-form body, preferring the 'Site address' field."""
    section = body
    match = re.search(r"###\s*Site address\s*\n+(.*?)(?:\n###|\Z)", body, re.S | re.I)
    if match:
        section = match.group(1)
    found = URL_PATTERN.search(section)
    return found.group(0).rstrip(".,;") if found else None


def check_limits(author: str, account_created: str, history: list[dict], now: datetime, *,
                 per_account: int = PER_ACCOUNT, window_hours: int = WINDOW_HOURS,
                 min_age_days: int = MIN_ACCOUNT_AGE_DAYS, global_per_day: int = GLOBAL_PER_DAY,
                 enabled: bool = True, blocked: list[str] = ()) -> tuple[bool, str]:
    """history: every request issue (including the current one) as {'author', 'created_at'}."""
    section = {"enabled": enabled, "per_account": per_account, "window_hours": window_hours,
               "min_account_age_days": min_age_days, "global_per_day": global_per_day}
    return check_account(section, list(blocked), author, account_created, history, now, "an analysis")


def _safe_host(value: str) -> str:
    return re.sub(r"[^a-z0-9.-]", "", value.lower())


def render_comment(report: dict) -> str:
    """Markdown reply. Only fixed text, numbers and sanitised host names are written."""
    s, m = report["summary"], report["measurement"]
    host = _safe_host(report["site"]["url"].split("/")[2]) if "//" in report["site"]["url"] else "the site"
    head = f"**Analysis of `{host}`**, measured from {m['vantage']} with {m['passes']} passes of {m['observe_seconds']:g} s."
    foot = ("\n\nThese are counts of what the page contacted before any interaction, not legal conclusions or "
            "statements of intent. The classification list is limited and may be incomplete. This result is a "
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


def result_block(report: dict, when: str) -> str:
    """The same figures as the reply, as a hidden, machine-readable block (base64 JSON in an HTML comment) that the
    website reads to show the result on its own page. Only sanitised host names, numbers and fixed labels go in."""
    s, m = report["summary"], report["measurement"]
    host = _safe_host(report["site"]["url"].split("/")[2]) if "//" in report["site"]["url"] else ""
    payload = {"schema": 1, "host": host, "measured_at": when, "vantage": m["vantage"], "passes": m["passes"],
               "observe_seconds": m["observe_seconds"], "status": s["status"], "confidence": s.get("confidence"),
               "passes_ok": s.get("passes_ok"), "passes_total": s.get("passes_total")}
    if s["status"] == "ok":
        from .bands import band_for
        metrics = s["metrics"]
        payload["band"] = band_for(metrics["tracking_services"], s.get("confidence"))
        payload["metrics"] = {k: metrics[k] for k in ("tracking_services", "third_party_domains", "third_party_requests",
                                                      "third_party_cookies", "cookies_total")}
        payload["services"] = [{"service": _safe_host(x["service"]), "company": re.sub(r"[^\w .()&+-]", "", str(x["entity"]))[:60],
                                "category": re.sub(r"[^a-z_ ]", "", str(x["category"]))[:30]}
                               for x in s["services"] if x["tracking"] and x["stable"]][:60]
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return f"\n\n<!-- {RESULT_MARK}:{base64.b64encode(raw).decode('ascii')} -->"


def decode_result_block(comment: str) -> dict | None:
    found = re.search(rf"<!-- {RESULT_MARK}:([A-Za-z0-9+/=]+) -->", comment)
    return json.loads(base64.b64decode(found.group(1))) if found else None


def contact_line(env=None) -> str:
    """Shown when a request is refused for the limit: where to turn for more, or for a project."""
    where = contact_where(env)
    if not where:
        return ""
    return ("\n\nNeed more analyses, or a project of your own, such as regular scans of your sites or a custom "
            f"report? Contact the developer: {where}")


def refusal_comment(reason: str, contact: str = "") -> str:
    return f"This request was not processed. {reason}{contact}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.ondemand", description=__doc__)
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--author", required=True)
    parser.add_argument("--account-created", required=True)
    parser.add_argument("--history-file", required=True)
    parser.add_argument("--limits", default="data/limits.json", help="limits file (default data/limits.json)")
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

    try:
        limits = load_limits(args.limits)
    except LimitsError as exc:  # never run unlimited because the file is broken
        return finish(refusal_comment("Analyses are paused while the limits are being fixed."), False, f"limits-file: {exc}")
    allowed, reason = check_account(limits["scan"], limits["blocked_accounts"], args.author, args.account_created,
                                    history, now, "an analysis")
    if not allowed:
        capacity = reason.startswith(("Limit reached", "The daily capacity"))
        return finish(refusal_comment(reason, contact_line() if capacity else ""), False, "limit")
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
    return finish(render_comment(report) + result_block(report, now.strftime("%Y-%m-%d %H:%M UTC")), True)


if __name__ == "__main__":
    sys.exit(main())
