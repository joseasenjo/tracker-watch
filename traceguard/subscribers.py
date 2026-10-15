"""Manage the subscriber list, on this computer only.

The list is a private file (data/private/subscribers.json, git-ignored). To use it from GitHub it is copied, whole, into
ONE repository secret, SUBSCRIBERS_JSON, with `push-secret`. Addresses are never printed in full unless you ask.

  add EMAIL --source mailto --note "asked by email on 2026-10-16, confirmed by reply" [--interest] [--lang en]
  confirm EMAIL         the person confirmed (for example by replying to your confirmation message)
  remove EMAIL          the person left: honoured at once and remembered so they are not added again by mistake
  list [--full]         who is on the list (masked unless --full)
  push-secret           copy the list into the repository secret (uses your logged-in GitHub CLI)

How a person gets on the list: they write to you asking for it; you add them (pending), answer asking them to confirm;
when they reply, you confirm them. That reply is the proof of consent: keep the note accurate.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

from . import mailing
from .notify import mask

DEFAULT_REPO = "joseasenjo/tracker-watch"
SECRET_LIMIT = 40_000  # GitHub secrets hold at most 48 KB; leave room


def main(argv: list[str] | None = None, *, runner=subprocess.run) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.subscribers", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default=str(mailing.STORE), help="the private list (default: data/private/subscribers.json)")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("email")
    add.add_argument("--source", default="mailto")
    add.add_argument("--note", required=True)
    add.add_argument("--interest", action="store_true", help="also asked for the organisation review")
    add.add_argument("--lang", default="en")
    for name in ("confirm", "remove"):
        sub.add_parser(name).add_argument("email")
    listing = sub.add_parser("list")
    listing.add_argument("--full", action="store_true")
    push = sub.add_parser("push-secret")
    push.add_argument("--repo", default=DEFAULT_REPO)
    args = parser.parse_args(argv)
    path = Path(args.file)

    try:
        subscribers = mailing.load_subscribers({}, path)
        if args.command == "add":
            subscribers = mailing.add(subscribers, args.email, source=args.source, note=args.note,
                                      interest=args.interest, language=args.lang)
            print(f"added (pending): {mask(mailing.validate_email(args.email))}. Ask them to confirm, then run: confirm")
        elif args.command == "confirm":
            subscribers = mailing.confirm(subscribers, args.email)
            print(f"confirmed: {mask(mailing.validate_email(args.email))}")
        elif args.command == "remove":
            subscribers = mailing.unsubscribe(subscribers, args.email)
            print(f"unsubscribed: {mask(mailing.validate_email(args.email))}")
        elif args.command == "list":
            counts = Counter(s["status"] for s in subscribers)
            for s in sorted(subscribers, key=lambda s: s["email"]):
                shown = s["email"] if args.full else mask(s["email"])
                print(f"{s['status']:<12} {shown}  interest={'yes' if s.get('interest') else 'no'}  lang={s.get('language', 'en')}")
            print(f"total {len(subscribers)}: " + ", ".join(f"{counts.get(k, 0)} {k}" for k in mailing.STATUSES))
            return 0
        elif args.command == "push-secret":
            if not path.exists():
                print("there is no list yet: nothing to copy", file=sys.stderr)
                return 1
            body = path.read_text(encoding="utf-8")
            if len(body.encode("utf-8")) > SECRET_LIMIT:
                print("the list is too big for one GitHub secret: time for a different store (see docs/EMAIL_DIGEST.md)",
                      file=sys.stderr)
                return 1
            done = runner(["gh", "secret", "set", "SUBSCRIBERS_JSON", "--repo", args.repo], input=body, text=True,
                          capture_output=True)
            if done.returncode != 0:
                print("the secret was not set (is the GitHub CLI logged in?)", file=sys.stderr)
                return 1
            print(f"secret SUBSCRIBERS_JSON updated in {args.repo} ({len(subscribers)} entries)")
            return 0
    except mailing.MailingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    mailing.save_subscribers(subscribers, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
