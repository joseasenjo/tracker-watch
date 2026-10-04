"""Curated short links, approved one by one by the administrator.

There is no server: each approved link becomes a static page (go/<code>/) with an interstitial that shows
where the link leads and, when available, what TraceGuard measured about the target. The visitor must click
to continue; nothing redirects automatically. Anyone can ask for a link through an issue; only the
administrator adds it, with `python -m traceguard.links add`.

Usage:
  python -m traceguard.links add CODE URL [--note TEXT] [--scan]
  python -m traceguard.links remove CODE
  python -m traceguard.links list
  python -m traceguard.links pending [--repo OWNER/NAME]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .classify import registrable_domain
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
    args = parser.parse_args(argv)

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
