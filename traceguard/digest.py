"""The email digest: what the bot just published on Bluesky, as a plain-text email to the people who asked for it.

It is built from the same draft the publisher posts (data/drafts/<date>/bluesky.json), so the email and the posts can
never disagree. It runs after a successful publication, from the same GitHub workflows, and does nothing unless the
repository variable DIGEST_ENABLED is "true".

Safeguards, all enforced here and covered by tests:
  * it only sends what has already been published (bluesky.posted.json is complete);
  * it only writes to confirmed subscribers, one message each, never a visible list;
  * the same digest is never sent twice (digest.sent.json in the draft folder: a date and a count, no addresses);
  * without --send nothing is sent: it shows what would go out (a dry run);
  * every message names the way to leave; there are no images, pixels or wrapped links.

Usage:  python -m traceguard.digest data/drafts/2026-10-12            (dry run)
        python -m traceguard.digest data/drafts/2026-10-12 --send     (needs DIGEST_ENABLED=true)
Settings come from the environment: see docs/EMAIL_DIGEST.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from . import mailing

SITE_URL = "https://joseasenjo.github.io/tracker-watch/"
DEFAULT_KINDS = ("weekly", "midweek")
LABELS = {"weekly": "Weekly check", "midweek": "Midweek update", "alert": "Notable change",
          "hook": "Most-tracked outlet", "announce": "Announcement"}
MARKER = "digest.sent.json"
DEFAULT_CTA = ("If your organisation needs a tracker and consent audit of its websites, a security review or an "
               "automation, reply to this email or write to {contact}. I am the developer: tell me what you need and "
               "we will see how I can help.")


def kind_of(folder_name: str) -> str:
    for suffix in ("midweek", "alert", "hook", "announce"):
        if folder_name.rstrip("/").endswith("-" + suffix):
            return suffix
    return "weekly"


def allowed_kinds(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = (env.get("DIGEST_KINDS") or "").strip()
    return tuple(k.strip() for k in raw.split(",") if k.strip()) if raw else DEFAULT_KINDS


def signature(env: Mapping[str, str]) -> str:
    custom = (env.get("DIGEST_SIGNATURE") or "").strip()
    if custom:
        return custom
    contact = (env.get("CONTACT_EMAIL") or "").strip()
    return f"Tracker Watch, {contact}" if contact else "Tracker Watch"


_BLUESKY_ONLY = re.compile(r"(\s*Automated post\.)?(\s+#\w+)*\s*$")


def clean_post(text: str) -> str:
    """The post as an email reads it: without what only makes sense on Bluesky (the thread pointer, the hashtags)."""
    text = text.strip().replace(" Thread below.", "")
    return _BLUESKY_ONLY.sub("", text).rstrip()


def cta_text(env: Mapping[str, str]) -> str:
    """The closing offer of services. DIGEST_CTA replaces it; an empty DIGEST_CTA is ignored (the footer says it exists)."""
    custom = (env.get("DIGEST_CTA") or "").strip()
    contact = (env.get("CONTACT_EMAIL") or "").strip() or "the address above"
    return custom or DEFAULT_CTA.format(contact=contact)


def build_digest(draft: dict, kind: str, *, site_url: str, sign: str, discussion_url: str = "",
                 cta: str = "") -> tuple[str, str]:
    """(subject, body) in plain text. The offer and the footer are not optional: they are part of every digest."""
    date_text = str(draft.get("date", "")).strip()
    label = LABELS.get(kind, "Update")
    lines = [f"{label}, {date_text}", ""]
    for post in draft.get("posts", []):
        lines += [clean_post(post), ""]
    lines.append(f"Full results and method: {site_url}")
    if discussion_url:
        lines.append(f"On Bluesky: {discussion_url}")
    if cta:
        lines += ["", cta]
    lines += ["", "--",
              "You receive this because you asked to be kept informed about Tracker Watch, which may include a mention "
              "of the developer's audit, analysis and automation services, and you confirmed your address.",
              "To stop, reply with the word UNSUBSCRIBE and you will be removed; no reason needed.",
              sign,
              "This email has no tracking: no images, no pixels, no wrapped links."]
    return f"[Tracker Watch] {label}, {date_text}".strip(), "\n".join(lines) + "\n"


def published_state(folder: Path) -> dict | None:
    path = folder / "bluesky.posted.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def already_sent(folder: Path) -> bool:
    return (folder / MARKER).exists()


def mark_sent(folder: Path, count: int, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    (folder / MARKER).write_text(json.dumps({"sent_at": now.isoformat(), "recipients": count}, indent=2) + "\n",
                                 encoding="utf-8")


def from_address(env: Mapping[str, str]) -> str:
    config = mailing.smtp_config(env)
    if config:
        return config["sender"]
    return (env.get("CONTACT_EMAIL") or "news@example.invalid").strip()


def run_test(draft: dict, kind: str, env: Mapping[str, str], sender_name: str) -> tuple[int, list[str]]:
    """One message to CONTACT_EMAIL only, marked [TEST]. Needs no subscribers, leaves no mark, and sends no one else."""
    contact = (env.get("CONTACT_EMAIL") or "").strip()
    try:
        owner = mailing.validate_email(contact)
        sender = mailing.make_sender(sender_name or env.get("DIGEST_SENDER") or "smtp", env)
    except mailing.MailingError as exc:
        return 3, [f"test not sent: {exc}"]
    subject, body = build_digest(draft, kind, site_url=env.get("SITE_URL") or SITE_URL, sign=signature(env), cta=cta_text(env))
    message = mailing.build_message(subject="[TEST] " + subject, body=body, to=owner, sender=from_address(env),
                                    unsubscribe_to=contact)
    result = sender.send_batch([message])
    lines = [f"{sender.name}: test message, {result.sent} sent, {result.failed} failed (only to {mailing.mask(owner)})"] + result.errors
    return (0 if result.failed == 0 and result.sent else 1), lines


def run(folder: Path | str, env: Mapping[str, str], *, send: bool = False, segment: str = "all", sender_name: str = "",
        again: bool = False, subscribers: list[dict] | None = None, now: datetime | None = None,
        test: bool = False) -> tuple[int, list[str]]:
    """Returns (exit code, log lines). The lines never contain an address."""
    folder = Path(folder)
    draft_path = folder / "bluesky.json"
    if not draft_path.exists():
        return 2, [f"no draft in {folder}"]
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    kind = kind_of(folder.name)
    if test:
        return run_test(draft, kind, env, sender_name)
    if kind not in allowed_kinds(env):
        return 0, [f"not sent: digests of kind '{kind}' are not enabled (DIGEST_KINDS)"]
    state = published_state(folder)
    if send and not (state and state.get("complete")):
        return 0, ["not sent: this draft has not been published yet; the digest follows the publication"]
    try:
        everyone = mailing.load_subscribers(env) if subscribers is None else subscribers
        chosen = mailing.recipients(everyone, segment)
    except (mailing.MailingError, ValueError) as exc:
        return 2, [f"the subscriber list cannot be read: {exc}"]
    if not chosen:
        return 0, ["no confirmed subscribers: nothing to send"]
    if send:
        if (env.get("DIGEST_ENABLED") or "").strip().lower() != "true":
            return 3, ["refused: DIGEST_ENABLED is not 'true' (the digest is off)"]
        if not (env.get("CONTACT_EMAIL") or "").strip():
            return 3, ["refused: CONTACT_EMAIL is needed, it is where people write to unsubscribe"]
        if already_sent(folder) and not again:
            return 0, ["already sent: this digest went out before (use --again only to repeat it on purpose)"]
    discussion = ""
    if state and state.get("posts"):
        discussion = state["posts"][0].get("url", "") or ""
    subject, body = build_digest(draft, kind, site_url=env.get("SITE_URL") or SITE_URL, sign=signature(env),
                                 discussion_url=discussion, cta=cta_text(env))
    try:
        sender = mailing.make_sender("dryrun" if not send else (sender_name or env.get("DIGEST_SENDER") or "smtp"), env)
    except mailing.MailingError as exc:
        return 1, [f"cannot send: {exc}"]
    sender_address, contact = from_address(env), (env.get("CONTACT_EMAIL") or "").strip()
    messages = [mailing.build_message(subject=subject, body=body, to=r["email"], sender=sender_address,
                                      unsubscribe_to=contact) for r in chosen]
    result = sender.send_batch(messages)
    lines = [f"{sender.name}: {result.sent} sent, {result.failed} failed (of {len(chosen)} confirmed)"] + result.errors
    if not send:
        lines += ["DRY RUN: nothing was sent.", f"Subject: {subject}", "--- body ---", *body.splitlines()]
    elif result.sent:
        mark_sent(folder, result.sent, now)
    return (0 if result.failed == 0 else 1), lines


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.digest", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", help="a draft folder, e.g. data/drafts/2026-10-12")
    parser.add_argument("--send", action="store_true", help="really send (needs DIGEST_ENABLED=true); without it, a dry run")
    parser.add_argument("--segment", choices=mailing.SEGMENTS, default="all")
    parser.add_argument("--sender", default="", help="sending back end (default: DIGEST_SENDER or smtp)")
    parser.add_argument("--again", action="store_true", help="send even if this digest already went out")
    parser.add_argument("--test", action="store_true", help="send ONE test message to CONTACT_EMAIL only (no list, no mark)")
    args = parser.parse_args(argv)
    code, lines = run(args.folder, os.environ if env is None else env, send=args.send, segment=args.segment,
                      sender_name=args.sender, again=args.again, test=args.test)
    print("\n".join(lines), file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
