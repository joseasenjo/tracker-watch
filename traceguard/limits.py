"""Limits for the two public request features: address analysis and short links.

One file, data/limits.json, read by the request workflows (so a change applies as soon as it is committed),
by the site builder (public pages state the current limits) and edited from the local dashboard
(`python -m traceguard.admin`). GitHub never shows a visitor's IP address to a workflow, only the account
that opened the issue, so every limit is per GitHub account, plus a daily cap for the whole site and a list of
blocked accounts.

  python -m traceguard.limits show
  python -m traceguard.limits set scan.per_account=1 links.enabled=false
  python -m traceguard.limits block SOMEACCOUNT      (or: unblock)
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_PATH = Path("data/limits.json")

DEFAULTS: dict = {
    "scan": {"enabled": True, "per_account": 2, "window_hours": 24, "min_account_age_days": 7, "global_per_day": 30},
    "links": {"enabled": True, "per_account": 3, "window_hours": 24, "min_account_age_days": 7,
              "global_per_day": 20, "max_total": 500},
    "blocked_accounts": [],
}
# (minimum, maximum) accepted for each number
BOUNDS = {"per_account": (0, 50), "window_hours": (1, 168), "min_account_age_days": (0, 3650),
          "global_per_day": (0, 1000), "max_total": (0, 5000)}
LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
MAX_BLOCKED = 200


class LimitsError(ValueError):
    pass


def validate(data: dict) -> dict:
    """Return a complete, checked copy of `data` (missing values take the defaults). Raises LimitsError."""
    if not isinstance(data, dict):
        raise LimitsError("the limits must be a JSON object")
    result = copy.deepcopy(DEFAULTS)
    for kind in ("scan", "links"):
        section = data.get(kind, {})
        if not isinstance(section, dict):
            raise LimitsError(f"'{kind}' must be an object")
        for key, value in section.items():
            if key not in DEFAULTS[kind]:
                raise LimitsError(f"unknown setting {kind}.{key}")
            if key == "enabled":
                if not isinstance(value, bool):
                    raise LimitsError(f"{kind}.enabled must be true or false")
            else:
                low, high = BOUNDS[key]
                if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                    raise LimitsError(f"{kind}.{key} must be a whole number from {low} to {high}")
            result[kind][key] = value
    blocked = data.get("blocked_accounts", [])
    if not isinstance(blocked, list) or len(blocked) > MAX_BLOCKED or any(
            not isinstance(b, str) or not LOGIN.match(b) for b in blocked):
        raise LimitsError("blocked_accounts must be a list of GitHub account names (up to 200)")
    result["blocked_accounts"] = sorted({b.lower() for b in blocked})
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise LimitsError(f"unknown section {sorted(unknown)[0]}")
    return result


def load_limits(path: Path | str = DEFAULT_PATH) -> dict:
    """The limits in force. A missing file means the defaults; a broken file is an error, never silently ignored."""
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    try:
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as exc:
        raise LimitsError(f"{path}: {exc}") from None


def save_limits(data: dict, path: Path | str = DEFAULT_PATH) -> dict:
    checked = validate(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(checked, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return checked


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def check_account(section: dict, blocked: list[str], author: str, account_created: str, history: list[dict],
                  now: datetime, what: str = "an analysis") -> tuple[bool, str]:
    """Apply one section of the limits. history: every request of this kind, including the current one, as
    {'author', 'created_at'}. Returns (allowed, reason shown to the requester)."""
    if author.lower() in {b.lower() for b in blocked}:
        return False, "This account cannot use this feature."
    if not section["enabled"]:
        return False, "This feature is switched off for now. Please try again later."
    if now - _parse(account_created) < timedelta(days=section["min_account_age_days"]):
        return False, f"Accounts must be at least {section['min_account_age_days']} days old to request {what}."
    window = timedelta(hours=section["window_hours"])
    own = [h for h in history if h["author"].lower() == author.lower() and now - _parse(h["created_at"]) < window]
    if len(own) > section["per_account"]:
        return False, (f"Limit reached: at most {section['per_account']} requests per account every "
                       f"{section['window_hours']} hours. Please try again later.")
    today = [h for h in history if now - _parse(h["created_at"]) < timedelta(hours=24)]
    if len(today) > section["global_per_day"]:
        return False, "The daily capacity for this feature has been reached. Please try again tomorrow."
    return True, ""


def _set(data: dict, assignment: str) -> None:
    key, _, raw = assignment.partition("=")
    kind, _, name = key.partition(".")
    if kind not in ("scan", "links") or name not in DEFAULTS[kind] or not raw:
        raise LimitsError(f"cannot understand {assignment!r}; use scan.per_account=2, links.enabled=false, ...")
    data[kind][name] = ({"true": True, "false": False}.get(raw.lower(), raw) if name == "enabled" else
                        int(raw) if raw.lstrip("-").isdigit() else raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.limits", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default=str(DEFAULT_PATH))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    setter = sub.add_parser("set")
    setter.add_argument("assignments", nargs="+", help="e.g. scan.per_account=1 links.enabled=false")
    for name in ("block", "unblock"):
        sub.add_parser(name).add_argument("account")
    args = parser.parse_args(argv)
    try:
        data = load_limits(args.file)
        if args.command == "set":
            for assignment in args.assignments:
                _set(data, assignment)
        elif args.command in ("block", "unblock"):
            accounts = set(data["blocked_accounts"])
            (accounts.add if args.command == "block" else accounts.discard)(args.account.lower())
            data["blocked_accounts"] = sorted(accounts)
        if args.command != "show":
            data = save_limits(data, args.file)
            print(f"saved {args.file}. Commit and push it for the workflows to use it.")
    except LimitsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
