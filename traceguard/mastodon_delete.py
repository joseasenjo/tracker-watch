"""Delete one of the account's own Mastodon posts, on the maintainer's explicit request.

Deliberately narrow, like bsky_delete: it deletes a single status of the account the token belongs to (the server refuses
anyone else's), and only if the status text starts with the text the maintainer
expects. Run from the delete-mastodon-post workflow, which has the Mastodon secrets; nothing is read from or written to
the repository.

Usage: python -m traceguard.mastodon_delete STATUS_ID --expect "start of the post text"
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys

from .publish import Mastodon, PublishError, _check, urllib_http

ID_RE = re.compile(r"^[0-9]{1,30}$")
TAGS = re.compile(r"<[^>]+>")


def plain(content: str) -> str:
    """The status text as a person reads it: Mastodon sends HTML."""
    return html.unescape(TAGS.sub("", content.replace("<br />", "\n").replace("<br>", "\n").replace("</p><p>", "\n\n")))


def delete_status(client: Mastodon, status_id: str, expect: str, log=print) -> bool:
    if not ID_RE.fullmatch(status_id):
        raise PublishError("the status id must be digits only")
    if not expect.strip():
        raise PublishError("say how the post text starts (--expect), so the wrong post cannot be deleted")
    status, headers, raw = client.http("GET", f"{client.base}/api/v1/statuses/{status_id}", headers=client._headers())
    found = json.loads(_check(status, raw, "read status", headers))
    # The token's scopes do not include reading the profile, so "whose is it" is not asked: the server itself refuses to
    # delete a status that is not the token owner's, and the text must match what the maintainer expects.
    text = plain(str(found.get("content", "")))
    if not text.startswith(expect):
        raise PublishError(f"the status {status_id} does not start with the expected text; nothing was deleted")
    status, headers, raw = client.http("DELETE", f"{client.base}/api/v1/statuses/{status_id}", headers=client._headers())
    _check(status, raw, "delete status", headers)
    log(f"deleted {status_id}: {text[:70]}")
    return True


def main(argv: list[str] | None = None, *, env=None, http=urllib_http) -> int:
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog="traceguard.mastodon_delete", description=__doc__)
    parser.add_argument("status_id")
    parser.add_argument("--expect", required=True)
    args = parser.parse_args(argv)
    missing = [n for n in ("MASTODON_URL", "MASTODON_TOKEN") if not env.get(n)]
    if missing:
        print(f"missing environment variable(s): {', '.join(missing)}", file=sys.stderr)
        return 2
    try:
        delete_status(Mastodon(http, env["MASTODON_URL"], env["MASTODON_TOKEN"]), args.status_id, args.expect)
    except PublishError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
