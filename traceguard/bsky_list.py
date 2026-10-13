"""The Bluesky list of the news outlets Tracker Watch measures.

`resolve` (public API, no credentials) finds each outlet's account and writes data/bsky_accounts.json. Only an account
whose handle IS the outlet's own web domain is accepted: Bluesky verifies that the domain belongs to the account, so
it cannot be a look-alike. `sync` (needs BLUESKY_HANDLE and BLUESKY_APP_PASSWORD, run from the bluesky-list workflow)
creates the list if it does not exist and adds the accounts that are missing; it never removes or edits anything else.

Usage: python -m traceguard.bsky_list resolve | python -m traceguard.bsky_list sync [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from pathlib import Path

from .publish import BLUESKY_SERVICE, Bluesky, PublishError, _now, urllib_http

ACCOUNTS_FILE = Path("data/bsky_accounts.json")
LIST_NAME = "News outlets measured by Tracker Watch"
LIST_DESCRIPTION = ("The news sites whose third-party tracking Tracker Watch measures every week (counts, not verdicts). "
                    "Only accounts whose handle is the outlet's own website domain. Being listed is not a finding "
                    "about the outlet. Automated; data and method: https://joseasenjo.github.io/tracker-watch/")
PUBLIC_API = "https://public.api.bsky.app"


def candidate_handles(url: str) -> list[str]:
    host = (urllib.parse.urlparse(url).hostname or "").removeprefix("www.")
    parts = host.split(".")
    found = [host]
    if len(parts) > 2:
        found.append(".".join(parts[-2:]))
    return [h for h in found if h]


def resolve(sites_file: Path, http=urllib_http) -> list[dict]:
    sites = json.loads(sites_file.read_text(encoding="utf-8"))["sites"]
    rows = []
    for site in sites:
        for handle in candidate_handles(site["url"]):
            query = urllib.parse.urlencode({"actor": handle})
            status, _, raw = http("GET", f"{PUBLIC_API}/xrpc/app.bsky.actor.getProfile?{query}", headers={})
            if status != 200:
                continue
            profile = json.loads(raw)
            if profile.get("handle", "").lower() == handle.lower() and profile.get("did"):
                rows.append({"site": site["name"], "handle": profile["handle"], "did": profile["did"]})
                break
    return rows


def sync(client: Bluesky, accounts: list[dict], *, dry_run: bool = False, log=print) -> dict:
    existing = client._call("com.atproto.repo.listRecords", method="GET", auth=True,
                            query={"repo": client.did, "collection": "app.bsky.graph.list", "limit": 100})
    found = next((r for r in existing.get("records", []) if r["value"].get("name") == LIST_NAME), None)
    if found:
        list_uri = found["uri"]
        log(f"list already exists: {list_uri}")
    elif dry_run:
        list_uri = "(would be created)"
        log("would create the list")
    else:
        record = {"$type": "app.bsky.graph.list", "purpose": "app.bsky.graph.defs#curatelist", "name": LIST_NAME,
                  "description": LIST_DESCRIPTION, "createdAt": _now()}
        list_uri = client._call("com.atproto.repo.createRecord", {"repo": client.did, "collection": "app.bsky.graph.list",
                                                                  "record": record})["uri"]
        log(f"list created: {list_uri}")
    present: set[str] = set()
    if found:
        cursor = None
        while True:
            query = {"repo": client.did, "collection": "app.bsky.graph.listitem", "limit": 100}
            if cursor:
                query["cursor"] = cursor
            page = client._call("com.atproto.repo.listRecords", method="GET", query=query)
            present |= {r["value"]["subject"] for r in page.get("records", []) if r["value"].get("list") == list_uri}
            cursor = page.get("cursor")
            if not cursor or not page.get("records"):
                break
    added = []
    for account in accounts:
        if account["did"] in present:
            continue
        log(f"{'would add' if dry_run else 'adding'} {account['handle']} ({account['site']})")
        if not dry_run:
            client._call("com.atproto.repo.createRecord", {
                "repo": client.did, "collection": "app.bsky.graph.listitem",
                "record": {"$type": "app.bsky.graph.listitem", "subject": account["did"], "list": list_uri,
                           "createdAt": _now()}})
        added.append(account["handle"])
    return {"list": list_uri, "added": added, "already": len(present)}


def main(argv: list[str] | None = None, *, env=None, http=urllib_http) -> int:
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog="traceguard.bsky_list", description=__doc__)
    parser.add_argument("command", choices=("resolve", "sync"))
    parser.add_argument("--sites-file", default="data/sites.json")
    parser.add_argument("--accounts-file", default=str(ACCOUNTS_FILE))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    accounts_file = Path(args.accounts_file)
    if args.command == "resolve":
        rows = resolve(Path(args.sites_file), http)
        accounts_file.write_text(json.dumps({"_note": "Outlets whose Bluesky handle is their own web domain "
                                                      "(python -m traceguard.bsky_list resolve).", "accounts": rows},
                                            indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{len(rows)} of {len(json.loads(Path(args.sites_file).read_text(encoding='utf-8'))['sites'])} outlets found")
        return 0
    accounts = json.loads(accounts_file.read_text(encoding="utf-8"))["accounts"]
    missing = [n for n in ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD") if not env.get(n)]
    if missing:
        print(f"missing environment variable(s): {', '.join(missing)}", file=sys.stderr)
        return 2
    client = Bluesky(http, env["BLUESKY_HANDLE"], env["BLUESKY_APP_PASSWORD"], env.get("BLUESKY_SERVICE") or BLUESKY_SERVICE)
    try:
        client.login()
        result = sync(client, accounts, dry_run=args.dry_run)
    except PublishError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"done: {len(result['added'])} added, {result['already']} were already in the list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
