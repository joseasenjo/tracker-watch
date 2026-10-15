"""Mailing list and sending, for the optional email digest.

Three small parts, each replaceable on its own:
  * the subscriber store     load_subscribers / save_subscribers and the add / confirm / unsubscribe operations;
  * the message builder      build_message (plain text, one message per person, no tracking of any kind);
  * the senders              a registry of sending back ends (SENDERS). "smtp" is the first one; a provider API or any
                             other way of sending is one more class with @register_sender, nothing else changes.

Principles (see docs/EMAIL_DIGEST.md): only people who asked and confirmed are written to; every message carries a way
to leave; addresses live outside the public repository (the local file data/private/ is git-ignored, and in GitHub
they are one repository secret); nothing here ever prints a full address.
"""
from __future__ import annotations

import json
import re
import smtplib
import ssl
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Mapping, Protocol

from .notify import mask

STORE = Path("data/private/subscribers.json")  # never committed: data/private/ is in .gitignore
STATUSES = ("pending", "confirmed", "unsubscribed")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+$")
SEGMENTS = ("all", "interest")


class MailingError(ValueError):
    pass


# --- subscribers ------------------------------------------------------------------------------------------------------

def validate_email(address: str) -> str:
    cleaned = (address or "").strip().lower()
    if not cleaned or len(cleaned) > 254 or not EMAIL_RE.match(cleaned):
        raise MailingError("that does not look like an email address")
    return cleaned


def _check(entry: dict) -> dict:
    email = validate_email(entry.get("email", ""))
    status = entry.get("status")
    if status not in STATUSES:
        raise MailingError(f"unknown status {status!r} for {mask(email)}")
    return {**entry, "email": email}


def load_subscribers(env: Mapping[str, str] | None = None, path: Path | str = STORE) -> list[dict]:
    """The list from the environment (GitHub: the secret SUBSCRIBERS_JSON) or, failing that, the local private file."""
    env = env or {}
    raw = (env.get("SUBSCRIBERS_JSON") or "").strip()
    path = Path(path)
    if raw:
        data = json.loads(raw)
    elif path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        return []
    entries = data["subscribers"] if isinstance(data, dict) else data
    return [_check(entry) for entry in entries]


def save_subscribers(subscribers: list[dict], path: Path | str = STORE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(subscribers, key=lambda s: s["email"])
    path.write_text(json.dumps({"subscribers": ordered}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _find(subscribers: list[dict], email: str) -> int | None:
    for index, entry in enumerate(subscribers):
        if entry["email"] == email:
            return index
    return None


def add(subscribers: list[dict], email: str, *, source: str, note: str, interest: bool = False, language: str = "en",
        today: date | None = None) -> list[dict]:
    """A person asked to be on the list: pending until they confirm. `note` says how they asked (it is the proof)."""
    email, today = validate_email(email), (today or date.today()).isoformat()
    if not note.strip():
        raise MailingError("say how this person asked to subscribe (it is the record of their consent)")
    entry = {"email": email, "status": "pending", "source": source.strip(), "consent": note.strip(), "asked": today,
             "interest": bool(interest), "language": language.strip() or "en"}
    result = list(subscribers)
    index = _find(result, email)
    if index is None:
        result.append(entry)
    else:
        result[index] = {**result[index], **entry, "status": "pending"}  # asking again after leaving starts over
    return result


def confirm(subscribers: list[dict], email: str, *, today: date | None = None) -> list[dict]:
    email, today = validate_email(email), (today or date.today()).isoformat()
    index = _find(subscribers, email)
    if index is None:
        raise MailingError("that address is not on the list: add it first")
    result = list(subscribers)
    result[index] = {**result[index], "status": "confirmed", "confirmed": today}
    return result


def unsubscribe(subscribers: list[dict], email: str, *, today: date | None = None) -> list[dict]:
    """Leaving is always honoured at once. The entry stays, marked, so the person is never added again by mistake."""
    email, today = validate_email(email), (today or date.today()).isoformat()
    result = list(subscribers)
    index = _find(result, email)
    if index is None:
        result.append({"email": email, "status": "unsubscribed", "source": "unsubscribe", "consent": "", "asked": today,
                       "interest": False, "language": "en", "unsubscribed": today})
    else:
        result[index] = {**result[index], "status": "unsubscribed", "unsubscribed": today}
    return result


def recipients(subscribers: list[dict], segment: str = "all") -> list[dict]:
    """Only people who confirmed. `segment="interest"` keeps those who also asked for the organisation review."""
    if segment not in SEGMENTS:
        raise MailingError(f"unknown segment {segment!r}; use one of {', '.join(SEGMENTS)}")
    confirmed = [s for s in subscribers if s["status"] == "confirmed"]
    return [s for s in confirmed if s.get("interest")] if segment == "interest" else confirmed


# --- messages ---------------------------------------------------------------------------------------------------------

def build_message(*, subject: str, body: str, to: str, sender: str, unsubscribe_to: str = "") -> EmailMessage:
    """One plain-text message for one person. No images, no pixels, no wrapped links."""
    for value in (subject, to, sender, unsubscribe_to):
        if "\n" in value or "\r" in value:
            raise MailingError("a header value contains a line break")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = validate_email(to)
    if unsubscribe_to:
        message["Reply-To"] = unsubscribe_to
        message["List-Unsubscribe"] = f"<mailto:{unsubscribe_to}?subject=unsubscribe>"
    message.set_content(body)
    return message


# --- senders ----------------------------------------------------------------------------------------------------------

@dataclass
class BatchResult:
    sent: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)  # kinds of error, never an address


class Sender(Protocol):
    name: str

    def send_batch(self, messages: list[EmailMessage]) -> BatchResult: ...


SENDERS: dict[str, Callable[[Mapping[str, str]], Sender]] = {}


def register_sender(name: str):
    """Add a sending back end: `@register_sender("brevo")` on a class whose constructor takes the environment."""
    def decorator(factory):
        SENDERS[name] = factory
        factory.name = name
        return factory
    return decorator


def make_sender(name: str, env: Mapping[str, str]) -> Sender:
    if name not in SENDERS:
        raise MailingError(f"unknown sender {name!r}; known: {', '.join(sorted(SENDERS))}")
    return SENDERS[name](env)


@register_sender("dryrun")
class DryRunSender:
    """Sends nothing: keeps the messages so they can be shown. The default of every command without --send."""

    def __init__(self, env: Mapping[str, str] | None = None):
        self.messages: list[EmailMessage] = []

    def send_batch(self, messages: list[EmailMessage]) -> BatchResult:
        self.messages.extend(messages)
        return BatchResult(sent=len(messages))


class SenderNotConfigured(MailingError):
    pass


def smtp_config(env: Mapping[str, str]) -> dict | None:
    """SMTP settings. DIGEST_MAIL_* (a dedicated account, recommended) win over MAIL_* (the notice account)."""
    dedicated = bool((env.get("DIGEST_MAIL_USER") or "").strip() and env.get("DIGEST_MAIL_PASSWORD"))

    def pick(name: str) -> str:
        return ((env.get("DIGEST_" + name) if dedicated else "") or env.get(name) or "").strip()
    host, user = pick("MAIL_SMTP_HOST"), pick("MAIL_USER")
    # the dedicated account is used only with its own user AND password, never mixed with the notice account's
    password = (env.get("DIGEST_MAIL_PASSWORD") if dedicated else env.get("MAIL_PASSWORD")) or ""
    if not (host and user and password):
        return None
    try:
        port = int(pick("MAIL_SMTP_PORT") or "587")
    except ValueError:
        return None
    return {"host": host, "port": port, "user": user, "password": password, "sender": pick("MAIL_FROM") or user}


@register_sender("smtp")
class SmtpSender:
    """Sends over an encrypted SMTP connection, one message per person, gently (a pause between messages, a cap per run)."""

    def __init__(self, env: Mapping[str, str], *, smtp=smtplib.SMTP, smtp_ssl=smtplib.SMTP_SSL, sleep=time.sleep):
        config = smtp_config(env)
        if config is None:
            raise SenderNotConfigured("the mail settings are not complete (MAIL_SMTP_HOST, MAIL_USER, MAIL_PASSWORD)")
        self.config, self.smtp, self.smtp_ssl, self.sleep = config, smtp, smtp_ssl, sleep
        self.delay = float(env.get("DIGEST_DELAY_SECONDS") or 1.0)
        self.max_per_run = int(env.get("DIGEST_MAX_PER_RUN") or 100)

    @contextmanager
    def _connection(self):
        context = ssl.create_default_context()
        cfg = self.config
        if cfg["port"] == 465:
            with self.smtp_ssl(cfg["host"], cfg["port"], timeout=30, context=context) as server:
                server.login(cfg["user"], cfg["password"])
                yield server
        else:
            with self.smtp(cfg["host"], cfg["port"], timeout=30) as server:
                server.starttls(context=context)  # no encryption, no password: never fall back to plain text
                server.login(cfg["user"], cfg["password"])
                yield server

    def send_batch(self, messages: list[EmailMessage]) -> BatchResult:
        result = BatchResult()
        todo = messages[: self.max_per_run]
        if len(messages) > len(todo):
            result.errors.append(f"cap: {len(messages) - len(todo)} message(s) left for another run (DIGEST_MAX_PER_RUN)")
        with self._connection() as server:
            for index, message in enumerate(todo):
                try:
                    server.send_message(message)
                    result.sent += 1
                except Exception as exc:  # one bad address must not stop the rest
                    result.failed += 1
                    result.errors.append(f"{type(exc).__name__} for {mask(str(message['To']))}")
                if index < len(todo) - 1 and self.delay:
                    self.sleep(self.delay)
        return result
