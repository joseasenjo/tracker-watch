"""Email notice when a thread has been published on Bluesky or Mastodon.

GitHub does not send an email when a workflow succeeds, only when it fails, so the publisher sends this one itself
over SMTP. It is optional and never gets in the way: with no mail settings nothing is sent, and a mail failure is
reported in the log but never fails the publication.

Settings come from the environment (in GitHub: repository secrets and variables), never from a file:
  MAIL_SMTP_HOST      SMTP server, e.g. smtp.gmail.com                      (secret)
  MAIL_SMTP_PORT      587 (STARTTLS, default) or 465 (TLS from the start)    (optional)
  MAIL_USER           the account that sends the notice                      (secret)
  MAIL_PASSWORD       its app password                                       (secret)
  MAIL_FROM           sender address, if different from MAIL_USER             (optional)
  NOTIFY_EMAIL        where the notice goes; falls back to CONTACT_EMAIL      (variable)
The connection is always encrypted; the password is never printed.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

REQUIRED = ("MAIL_SMTP_HOST", "MAIL_USER", "MAIL_PASSWORD")
PLATFORM_NAMES = {"bluesky": "Bluesky", "mastodon": "Mastodon"}


def mail_config(env) -> dict | None:
    """The mail settings, or None when the notice is not configured."""
    if any(not (env.get(name) or "").strip() for name in REQUIRED):
        return None
    recipient = (env.get("NOTIFY_EMAIL") or env.get("CONTACT_EMAIL") or "").strip()
    if not recipient or "@" not in recipient or any(c in recipient for c in "\r\n,; "):
        return None
    try:
        port = int((env.get("MAIL_SMTP_PORT") or "587").strip())
    except ValueError:
        return None
    return {"host": env["MAIL_SMTP_HOST"].strip(), "port": port, "user": env["MAIL_USER"].strip(),
            "password": env["MAIL_PASSWORD"], "sender": (env.get("MAIL_FROM") or env["MAIL_USER"]).strip(),
            "to": recipient}


def mask(address: str) -> str:
    name, _, domain = address.partition("@")
    return (name[:1] + "***@" + domain) if domain else "***"


def build_message(draft: dict, platform: str, state: dict, folder: str, config: dict) -> EmailMessage:
    folder_name = folder.rstrip("/")
    kind = ("mid-week post" if folder_name.endswith("-midweek") else
            "alert post" if folder_name.endswith("-alert") else
            "hook post" if folder_name.endswith("-hook") else
            "announcement post" if folder_name.endswith("-announce") else "weekly thread")
    name = PLATFORM_NAMES.get(platform, platform)
    posts = state.get("posts", [])
    texts = draft.get("posts", [])
    lines = [f"The {kind} of {draft.get('date', '?')} is now published on {name}.", "",
             f"Posts: {len(posts)}   Finished: {state.get('finished_at', '?')}", ""]
    for index, post in enumerate(posts):
        lines.append(f"{index + 1}. {post.get('url') or post.get('uri') or '(no link)'}")
        if index < len(texts):
            lines.append("   " + texts[index].replace("\n", "\n   "))
        lines.append("")
    lines.append("This notice comes from the Tracker Watch publisher. Switch it off by removing the MAIL_* secrets.")
    message = EmailMessage()
    message["Subject"] = f"[Tracker Watch] Published on {name}: {kind} {draft.get('date', '')}".strip()
    message["From"] = config["sender"]
    message["To"] = config["to"]
    message.set_content("\n".join(lines))
    return message


def send(config: dict, message: EmailMessage, *, smtp=smtplib.SMTP, smtp_ssl=smtplib.SMTP_SSL, timeout: float = 30) -> None:
    context = ssl.create_default_context()
    if config["port"] == 465:
        with smtp_ssl(config["host"], config["port"], timeout=timeout, context=context) as server:
            server.login(config["user"], config["password"])
            server.send_message(message)
        return
    with smtp(config["host"], config["port"], timeout=timeout) as server:
        server.starttls(context=context)  # no encryption, no password: never fall back to plain text
        server.login(config["user"], config["password"])
        server.send_message(message)


def notify_published(draft: dict, platform: str, state: dict, draft_path: Path | str, env, *, smtp=smtplib.SMTP,
                     smtp_ssl=smtplib.SMTP_SSL) -> str:
    """Send the notice if it is configured. Returns one line for the log; never raises."""
    config = mail_config(env)
    if config is None:
        return "no email notice (MAIL_SMTP_HOST, MAIL_USER, MAIL_PASSWORD and NOTIFY_EMAIL are not all set)"
    try:
        message = build_message(draft, platform, state, str(Path(draft_path).parent.name), config)
        send(config, message, smtp=smtp, smtp_ssl=smtp_ssl)
    except Exception as exc:  # a mail problem must never undo or fail a publication
        return f"email notice NOT sent ({type(exc).__name__}); the post itself was published"
    return f"email notice sent to {mask(config['to'])}"


def main(argv: list[str] | None = None, env=None) -> int:
    """`python -m traceguard.notify test`: send a test message with the current settings, to check them."""
    import os
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["test"]:
        print("usage: python -m traceguard.notify test", file=sys.stderr)
        return 2
    env = os.environ if env is None else env
    config = mail_config(env)
    if config is None:
        print("not configured: set MAIL_SMTP_HOST, MAIL_USER, MAIL_PASSWORD and NOTIFY_EMAIL (or CONTACT_EMAIL)", file=sys.stderr)
        return 1
    message = EmailMessage()
    message["Subject"] = "[Tracker Watch] Test of the publication notice"
    message["From"], message["To"] = config["sender"], config["to"]
    message.set_content("If you can read this, the notice that follows every Bluesky publication will reach you.")
    try:
        send(config, message)
    except Exception as exc:
        print(f"test email NOT sent ({type(exc).__name__}). Check the server, the port and the app password.", file=sys.stderr)
        return 1
    print(f"test email sent to {mask(config['to'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
