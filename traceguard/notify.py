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


def _kind(folder_name: str) -> str:
    folder_name = folder_name.rstrip("/")
    return ("mid-week post" if folder_name.endswith("-midweek") else "alert post" if folder_name.endswith("-alert") else
            "hook post" if folder_name.endswith("-hook") else "announcement post" if folder_name.endswith("-announce") else
            "weekly thread" if folder_name else "post")


def build_not_published_message(platform: str, reason: str, folder_name: str, config: dict, *, date: str = "",
                                posts_out: int = 0, total: int = 0, skipped: bool = False, secrets=()) -> EmailMessage:
    """The notice for a post that did NOT go out, so silence never means 'it worked'. `skipped` = left out on purpose
    (switch off, review mode, already published); otherwise it failed and needs attention. Secrets never appear in it."""
    name = PLATFORM_NAMES.get(platform, platform)
    text = " ".join(str(reason).split())[:400]
    for secret in secrets:
        if secret and len(secret) >= 6:
            text = text.replace(secret, "***")
    kind = _kind(folder_name)
    message = EmailMessage()
    message["Subject"] = f"[Tracker Watch] NOT published on {name} ({'skipped' if skipped else 'FAILED'}): {kind} {date}".strip()
    message["From"], message["To"] = config["sender"], config["to"]
    lines = [f"The {kind}{' of ' + date if date else ''} was NOT published on {name}.", "",
             f"Status: {'left out on purpose, nothing to do unless you wanted it published' if skipped else 'FAILED, it needs your attention'}",
             f"Why: {text}"]
    if total:
        lines.append(f"Posts already out: {posts_out} of {total}" + ("" if not posts_out else " (the rest can be resumed: run publish-draft again)"))
    lines += ["", "To publish it by hand: Actions -> publish-draft -> Run workflow (the folder, and the platform)." if not skipped else
              "To publish it anyway: fix the cause (a switch, review mode) and run publish-draft by hand.", "",
              "This notice comes from the Tracker Watch publisher."]
    message.set_content("\n".join(lines))
    return message


def notify_not_published(platform: str, reason: str, folder_name: str, env, *, date: str = "", posts_out: int = 0, total: int = 0,
                         skipped: bool = False, smtp=smtplib.SMTP, smtp_ssl=smtplib.SMTP_SSL) -> str:
    """Send the 'not published' notice if mail is configured. One line for the log; never raises."""
    config = mail_config(env)
    if config is None:
        return "no email notice (MAIL_SMTP_HOST, MAIL_USER, MAIL_PASSWORD and NOTIFY_EMAIL are not all set)"
    secrets = [env.get(n) for n in ("BLUESKY_APP_PASSWORD", "MASTODON_TOKEN", "MAIL_PASSWORD")]
    try:
        send(config, build_not_published_message(platform, reason, folder_name, config, date=date, posts_out=posts_out,
                                                 total=total, skipped=skipped, secrets=secrets), smtp=smtp, smtp_ssl=smtp_ssl)
    except Exception as exc:
        return f"'not published' email NOT sent ({type(exc).__name__})"
    return f"'not published' email sent to {mask(config['to'])}"


def build_failure_message(env, config: dict) -> EmailMessage:
    """The notice for a scheduled workflow that failed. The values come from the environment, never from a command line."""
    def clean(name: str, limit: int = 200) -> str:
        return " ".join((env.get(name) or "?").split())[:limit]
    workflow, url = clean("FAILED_WORKFLOW", 80), clean("FAILED_RUN_URL")
    message = EmailMessage()
    message["Subject"] = f"[Tracker Watch] FAILED: {workflow}"
    message["From"], message["To"] = config["sender"], config["to"]
    message.set_content("\n".join([
        f"The workflow '{workflow}' ({clean('FAILED_EVENT', 40)}) ended with an error.",
        *([f"Started: {clean('FAILED_AT', 40)}"] if (env.get("FAILED_AT") or "").strip() else []), "", f"Log: {url}", "",
        "Nothing has been repeated automatically. Open the log, read the first red step, and start the workflow again by hand "
        "(Actions -> the workflow -> Run workflow) once the cause is fixed.", "",
        "This notice comes from the Tracker Watch failure watcher."]))
    return message


def main(argv: list[str] | None = None, env=None) -> int:
    """`python -m traceguard.notify test` sends a test message; `failure` reports a failed workflow (details in the environment)."""
    import os
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if args not in (["test"], ["failure"], ["not-published"]):
        print("usage: python -m traceguard.notify test|failure|not-published", file=sys.stderr)
        return 2
    env = os.environ if env is None else env
    config = mail_config(env)
    if args == ["not-published"]:  # a post left out on purpose by the workflow itself (details in the environment)
        platforms = [p.strip() for p in (env.get("NOT_PUBLISHED_PLATFORM") or "").split(",") if p.strip() in PLATFORM_NAMES]
        if not platforms or not (env.get("NOT_PUBLISHED_REASON") or "").strip():
            print("NOT_PUBLISHED_PLATFORM (bluesky, mastodon) and NOT_PUBLISHED_REASON are needed", file=sys.stderr)
            return 2
        for platform in platforms:
            print(notify_not_published(platform, env["NOT_PUBLISHED_REASON"], env.get("NOT_PUBLISHED_FOLDER") or "", env,
                                       date=env.get("NOT_PUBLISHED_DATE") or "", skipped=True))
        return 0
    if args == ["failure"]:
        if config is None:
            print("not configured: no failure notice sent", file=sys.stderr)
            return 1
        try:
            send(config, build_failure_message(env, config))
        except Exception as exc:
            print(f"failure notice NOT sent ({type(exc).__name__})", file=sys.stderr)
            return 1
        print(f"failure notice sent to {mask(config['to'])}")
        return 0
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
