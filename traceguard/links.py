"""Short links that show where they lead before the visitor continues.

There is no server: each link is a static page (go/<code>/) with an interstitial that shows where the link
leads and, when available, what TraceGuard measured about the target. The visitor must click to continue;
nothing redirects automatically. Anyone with a GitHub account can ask for a link through an issue; a workflow
checks it (public address only, no other shorteners, blocked domains, per-account limits from data/limits.json),
measures the target, adds it to data/links.json and republishes the site. The administrator can still add or
remove links by hand and change the limits from the local dashboard.

Usage:
  python -m traceguard.links add CODE URL [--note TEXT] [--scan]
  python -m traceguard.links remove CODE
  python -m traceguard.links list
  python -m traceguard.links pending [--repo OWNER/NAME]
  python -m traceguard.links request --body-file body.txt --author LOGIN --account-created ISO \
      --history-file history.json --site-url URL --out comment.md --result-file result.json   (used by the workflow)
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .classify import registrable_domain
from .limits import LimitsError, check_account, load_limits
from .safety import Resolver, UnsafeURL, check_target, system_resolver

DEFAULT_LINKS = Path("data/links.json")
DEFAULT_BLOCKLIST = Path("data/blocklist.txt")
DEFAULT_REPO = "joseasenjo/tracker-watch"
CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,31}$")
RESERVED_CODES = {"admin", "api", "assets", "data", "sites", "go", "about", "contact", "privacy", "method",
                  "changes", "share", "index", "unmeasured", "origins", "feed", "links", "short", "static"}
MAX_URL_LENGTH = 2000
MAX_CHECK_DOMAINS = 12


class LinkError(ValueError):
    """The link cannot be added (invalid code, unsafe or blocked target, duplicate)."""


def load_blocklist(path: Path | str = DEFAULT_BLOCKLIST) -> set[str]:
    path = Path(path)
    if not path.exists():
        return set()
    lines = (line.split("#")[0].strip().lower() for line in path.read_text(encoding="utf-8").splitlines())
    return {line for line in lines if line}


def load_links(path: Path | str = DEFAULT_LINKS) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("links", [])


def save_links(links: list[dict], path: Path | str = DEFAULT_LINKS) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"links": sorted(links, key=lambda x: x["code"])}, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def validate_code(code: str) -> str:
    code = code.strip().lower()
    if not CODE_PATTERN.match(code):
        raise LinkError("the code must be 3-32 characters: lowercase letters, digits and hyphens, not starting with a hyphen")
    if code in RESERVED_CODES:
        raise LinkError(f"'{code}' is reserved")
    return code


def validate_target(url: str, blocklist: set[str], resolver: Resolver = system_resolver) -> str:
    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise LinkError("the address is too long")
    try:
        url = check_target(url, resolver)
    except UnsafeURL as exc:
        raise LinkError(f"the address is not allowed: {exc}") from None
    host = (urlsplit(url).hostname or "").lower()
    parts = host.split(".")
    for i in range(len(parts) - 1):
        if ".".join(parts[i:]) in blocklist:
            raise LinkError("the address belongs to a blocked domain (for example another link shortener)")
    return url


def summarize_check(report: dict) -> dict:
    """Compact, factual summary of a scan to show on the interstitial."""
    from .bands import band_for
    s = report["summary"]
    base = {"status": s["status"], "vantage": report["measurement"]["vantage"],
            "date": report["generated_at"][:10], "tracking": None, "band": None, "confidence": s.get("confidence"),
            "domains": []}
    if s["status"] != "ok":
        return base
    tracking = s["metrics"]["tracking_services"]
    base.update(tracking=tracking, band=band_for(tracking, s.get("confidence")))
    base["domains"] = [{"domain": x["service"], "entity": x["entity"], "category": x["category"]}
                       for x in s["services"] if x["tracking"] and x["stable"]][:MAX_CHECK_DOMAINS]
    return base


def add_link(path: Path | str, code: str, url: str, *, note: str = "", check: dict | None = None,
             blocklist: set[str] | None = None, resolver: Resolver = system_resolver,
             now: datetime | None = None) -> dict:
    code = validate_code(code)
    blocklist = load_blocklist() if blocklist is None else blocklist
    url = validate_target(url, blocklist, resolver)
    links = load_links(path)
    if any(link["code"] == code for link in links):
        raise LinkError(f"the code '{code}' already exists")
    record = {"code": code, "url": url, "host": (urlsplit(url).hostname or "").lower(),
              "note": note.strip()[:200], "created": (now or datetime.now(timezone.utc)).date().isoformat(),
              "check": check}
    save_links(links + [record], path)
    return record


CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alikes (0/o, 1/l/i)


def generate_code(existing: set[str], length: int = 7) -> str:
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))
        if code not in existing and code not in RESERVED_CODES:
            return code


def _scan_target(url: str) -> dict | None:
    """Measure the target for the interstitial. None when the browser is not available."""
    try:
        from .scanner import scan_site  # imported here so the module works without a browser
        return summarize_check(scan_site(url, passes=2, observe_seconds=10.0, vantage="github-actions-us"))
    except Exception:
        return None


def process_request(body: str, author: str, account_created: str, history: list[dict], limits: dict,
                    links_path: Path | str, site_url: str, *, now: datetime | None = None,
                    blocklist: set[str] | None = None, resolver: Resolver = system_resolver,
                    scan: Callable[[str], dict | None] | None = _scan_target) -> dict:
    """Handle one short-link request end to end. There is no manual approval: the limits and the checks decide.

    Returns {"created": bool, "reason": short code for logs, "comment": markdown reply, "code": str|None}.
    """
    now = now or datetime.now(timezone.utc)
    site_url = site_url if site_url.endswith("/") else site_url + "/"

    def refuse(reason: str, text: str) -> dict:
        return {"created": False, "reason": reason, "code": None, "comment": f"This request was not processed. {text}"}

    allowed, why = check_account(limits["links"], limits["blocked_accounts"], author, account_created, history, now,
                                 "a short link")
    if not allowed:
        return refuse("limit", why)
    found = re.search(r"https?://[^\s<>\"'`)\]]+", _field(body, "Target address"))
    if not found:
        return refuse("no-url", "No web address was found in the request.")
    wanted = (_field(body, "Preferred short code").lower().split() or [""])[0]
    blocklist = load_blocklist() if blocklist is None else blocklist
    try:
        url = validate_target(found.group(0).rstrip(".,;"), blocklist, resolver)
        code = validate_code(wanted) if wanted else ""
    except LinkError as exc:
        return refuse("invalid", f"That request cannot be accepted: {exc}.")
    links = load_links(links_path)
    if len(links) >= limits["links"]["max_total"]:
        return refuse("full", "The site has reached its maximum number of short links. Please try again later.")
    same = next((l for l in links if l["url"] == url), None)
    if same and not code:
        return {"created": False, "reason": "exists", "code": same["code"], "comment": (
            f"That address already has a short link: {site_url}go/{same['code']}/")}
    if code and any(l["code"] == code for l in links):
        return refuse("code-taken", f"The code `{code}` is already taken. Choose another or leave it empty.")
    check = scan(url) if scan else None
    if check and check.get("status") == "error":
        return refuse("unreachable", "We could not load that address, so no link was created.")
    code = code or generate_code({l["code"] for l in links})
    add_link(links_path, code, url, check=check, blocklist=blocklist, resolver=resolver, now=now)
    shown = (f"; it contacted {check['tracking']} tracking services before any interaction"
             if check and check.get("status") == "ok" else "")
    return {"created": True, "reason": "created", "code": code, "comment": (
        f"Your short link: **{site_url}go/{code}/**\n\nIt will be live in a couple of minutes, after the site is "
        f"republished. The page shows where it leads and a button to continue; it never redirects by itself{shown}. "
        "The link can be removed at any time if it breaks the rules on the Short links page.")}


def remove_link(path: Path | str, code: str) -> bool:
    links = load_links(path)
    kept = [link for link in links if link["code"] != code.strip().lower()]
    if len(kept) == len(links):
        return False
    save_links(kept, path)
    return True


def _field(body: str, heading: str) -> str:
    match = re.search(rf"###\s*{re.escape(heading)}\s*\n+(.*?)(?:\n###|\Z)", body, re.S | re.I)
    value = match.group(1).strip() if match else ""
    return "" if value.lower() in ("_no response_", "no response") else value


def parse_request(issue: dict) -> dict:
    body = issue.get("body") or ""
    url = _field(body, "Target address")
    found = re.search(r"https?://[^\s<>\"'`)\]]+", url)
    return {"number": issue.get("number"), "author": (issue.get("author") or {}).get("login", ""),
            "created": (issue.get("createdAt") or "")[:10], "url": found.group(0) if found else "",
            "code": _field(body, "Preferred short code").lower().split()[0] if _field(body, "Preferred short code") else "",
            "reason": _field(body, "Why do you need it?")[:200]}


def pending_requests(repo: str = DEFAULT_REPO, runner: Callable = subprocess.run) -> list[dict]:
    """Open link-request issues, read with the GitHub CLI. Returns [] when the CLI is not available."""
    try:
        result = runner(["gh", "issue", "list", "--repo", repo, "--label", "link-request", "--state", "open",
                         "--json", "number,title,body,author,createdAt", "--limit", "50"],
                        capture_output=True, text=True, encoding="utf-8", timeout=30)
        if result.returncode != 0:
            return []
        return [parse_request(issue) for issue in json.loads(result.stdout or "[]")]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.links", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default=str(DEFAULT_LINKS))
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("code")
    add.add_argument("url")
    add.add_argument("--note", default="")
    add.add_argument("--scan", action="store_true", help="measure the target first and store the summary")
    rem = sub.add_parser("remove")
    rem.add_argument("code")
    sub.add_parser("list")
    pend = sub.add_parser("pending")
    pend.add_argument("--repo", default=DEFAULT_REPO)
    req = sub.add_parser("request", help="process one issue-form request (used by the workflow)")
    for name in ("--body-file", "--author", "--account-created", "--history-file", "--site-url", "--out"):
        req.add_argument(name, required=True)
    req.add_argument("--limits", default="data/limits.json")
    req.add_argument("--result-file")
    req.add_argument("--dry-run", action="store_true", help="validate and apply limits but do not measure the target")
    args = parser.parse_args(argv)

    if args.command == "request":
        try:
            limits = load_limits(args.limits)
        except LimitsError as exc:  # never run unlimited because the file is broken
            outcome = {"created": False, "code": None, "reason": f"limits-file: {exc}",
                       "comment": "This request was not processed. Short links are paused while the limits are being fixed."}
        else:
            outcome = process_request(
                Path(args.body_file).read_text(encoding="utf-8"), args.author, args.account_created,
                json.loads(Path(args.history_file).read_text(encoding="utf-8")), limits, args.file, args.site_url,
                scan=None if args.dry_run else _scan_target)
        Path(args.out).write_text(outcome["comment"], encoding="utf-8")
        if args.result_file:
            Path(args.result_file).write_text(json.dumps({k: outcome[k] for k in ("created", "code", "reason")}),
                                              encoding="utf-8")
        return 0

    if args.command == "list":
        for link in load_links(args.file):
            check = link["check"]
            print(f"{link['code']:<24} {link['host']:<32} {link['created']}  "
                  + (f"tracking {check['tracking']}" if check and check["tracking"] is not None else "not measured"))
        return 0
    if args.command == "remove":
        print("removed" if remove_link(args.file, args.code) else "no such code")
        return 0
    if args.command == "pending":
        requests = pending_requests(args.repo)
        for r in requests:
            print(f"#{r['number']} @{r['author']} {r['created']}  {r['code'] or '(no code)'}  {r['url'] or '(no address)'}")
        if not requests:
            print("no pending requests (or the GitHub CLI is not available)")
        return 0
    try:
        check = None
        url = validate_target(args.url, load_blocklist())
        if args.scan:
            from .scanner import scan_site  # imported here so the module works without a browser
            check = summarize_check(scan_site(url, passes=2, observe_seconds=10.0, vantage="local-windows-spain"))
        record = add_link(args.file, args.code, url, note=args.note, check=check)
    except LinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"added {record['code']} -> {record['host']}. Rebuild the site to publish it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
