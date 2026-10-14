"""Delete one of the account's own Bluesky posts, on the maintainer's explicit request.

Deliberately narrow: it deletes a single post of the account that signs in (never another record type, never another
account), and only if the post exists and its text starts with the text the maintainer expects. Run from the
delete-post workflow, which has the Bluesky secrets; nothing is read from or written to the repository.

Usage: python -m traceguard.bsky_delete RKEY --expect "start of the post text"
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse

from .publish import BLUESKY_SERVICE, Bluesky, PublishError, urllib_http

RKEY_RE = re.compile(r"^[a-z0-9]{13}$")
COLLECTION = "app.bsky.feed.post"


def delete_post(client: Bluesky, rkey: str, expect: str, log=print) -> bool:
    if not RKEY_RE.fullmatch(rkey):
        raise PublishError("the post key must be 13 lowercase letters and digits")
    if not expect.strip():
        raise PublishError("say how the post text starts (--expect), so the wrong post cannot be deleted")
    found = client._call("com.atproto.repo.getRecord", method="GET",
                         query={"repo": client.did, "collection": COLLECTION, "rkey": rkey})
    text = str(found.get("value", {}).get("text", ""))
    if not text.startswith(expect):
        raise PublishError(f"the post {rkey} does not start with the expected text; nothing was deleted")
    client._call("com.atproto.repo.deleteRecord", {"repo": client.did, "collection": COLLECTION, "rkey": rkey})
    log(f"deleted {rkey}: {text[:70]}")
    return True


def main(argv: list[str] | None = None, *, env=None, http=urllib_http) -> int:
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog="traceguard.bsky_delete", description=__doc__)
    parser.add_argument("rkey")
    parser.add_argument("--expect", required=True)
    args = parser.parse_args(argv)
    missing = [n for n in ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD") if not env.get(n)]
    if missing:
        print(f"missing environment variable(s): {', '.join(missing)}", file=sys.stderr)
        return 2
    client = Bluesky(http, env["BLUESKY_HANDLE"], env["BLUESKY_APP_PASSWORD"], env.get("BLUESKY_SERVICE") or BLUESKY_SERVICE)
    try:
        client.login()
        delete_post(client, args.rkey, args.expect)
    except PublishError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
