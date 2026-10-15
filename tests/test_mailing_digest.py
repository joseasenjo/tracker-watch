"""The optional email digest: the subscriber list, the senders and the digest command. Nothing here touches the network."""
import json
import smtplib
from datetime import date
from pathlib import Path

import pytest

from traceguard import digest, mailing, subscribers as subscribers_cli

TODAY = date(2026, 10, 16)
SMTP_ENV = {"MAIL_SMTP_HOST": "smtp.example.org", "MAIL_USER": "news@example.org", "MAIL_PASSWORD": "pw",
            "CONTACT_EMAIL": "contact@example.org", "DIGEST_ENABLED": "true", "DIGEST_DELAY_SECONDS": "0"}


class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.sent, self.tls, self.user = host, port, [], False, None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        self.user = user

    def send_message(self, message):
        if "bad@" in str(message["To"]):
            raise smtplib.SMTPRecipientsRefused({})
        self.sent.append(message)


@pytest.fixture(autouse=True)
def _reset():
    FakeSMTP.instances.clear()
    yield


def people():
    subs = []
    for email, interest in (("ana@example.org", True), ("ben@example.org", False), ("cai@example.org", False)):
        subs = mailing.add(subs, email, source="mailto", note=f"asked by email, {email}", interest=interest, today=TODAY)
        subs = mailing.confirm(subs, email, today=TODAY)
    return mailing.add(subs, "dan@example.org", source="mailto", note="asked, not confirmed yet", today=TODAY)


# --- the list ---------------------------------------------------------------------------------------------------------

def test_emails_are_checked_and_lowercased():
    assert mailing.validate_email("  Ana@Example.ORG ") == "ana@example.org"
    for bad in ("", "no-at-sign", "a@b", "a b@c.org", "a@b.org\nbcc:x@y.org", "x" * 260 + "@a.org"):
        with pytest.raises(mailing.MailingError):
            mailing.validate_email(bad)


def test_the_lifecycle_pending_confirmed_and_unsubscribed():
    subs = mailing.add([], "ana@example.org", source="mailto", note="asked by email", today=TODAY)
    assert subs[0]["status"] == "pending" and mailing.recipients(subs) == []
    subs = mailing.confirm(subs, "ana@example.org", today=TODAY)
    assert [s["email"] for s in mailing.recipients(subs)] == ["ana@example.org"]
    subs = mailing.unsubscribe(subs, "ana@example.org", today=TODAY)
    assert subs[0]["status"] == "unsubscribed" and mailing.recipients(subs) == []


def test_an_unknown_address_that_unsubscribes_is_remembered_and_asking_again_starts_over():
    subs = mailing.unsubscribe([], "eve@example.org", today=TODAY)
    assert subs[0]["status"] == "unsubscribed"
    again = mailing.add(subs, "eve@example.org", source="mailto", note="asked again by email", today=TODAY)
    assert again[0]["status"] == "pending" and "unsubscribed" in again[0]  # still pending until she confirms
    assert mailing.recipients(again) == []


def test_adding_needs_the_record_of_how_the_person_asked():
    with pytest.raises(mailing.MailingError):
        mailing.add([], "ana@example.org", source="mailto", note="  ")
    with pytest.raises(mailing.MailingError):
        mailing.confirm([], "nobody@example.org")


def test_only_confirmed_people_are_recipients_and_the_interest_segment_is_a_subset():
    subs = people()
    assert [s["email"] for s in mailing.recipients(subs)] == ["ana@example.org", "ben@example.org", "cai@example.org"]
    assert [s["email"] for s in mailing.recipients(subs, "interest")] == ["ana@example.org"]
    with pytest.raises(mailing.MailingError):
        mailing.recipients(subs, "everyone")


def test_the_list_comes_from_the_secret_first_and_the_private_file_second(tmp_path):
    path = tmp_path / "private" / "subscribers.json"
    mailing.save_subscribers(people(), path)
    assert len(mailing.load_subscribers({}, path)) == 4
    secret = json.dumps({"subscribers": [{"email": "zoe@example.org", "status": "confirmed"}]})
    assert [s["email"] for s in mailing.load_subscribers({"SUBSCRIBERS_JSON": secret}, path)] == ["zoe@example.org"]
    assert mailing.load_subscribers({}, tmp_path / "missing.json") == []
    with pytest.raises(mailing.MailingError):
        mailing.load_subscribers({"SUBSCRIBERS_JSON": json.dumps([{"email": "x@example.org", "status": "maybe"}])})


def test_the_private_folder_is_ignored_by_git():
    ignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert "data/private/" in ignore.splitlines()


# --- messages and senders ---------------------------------------------------------------------------------------------

def test_a_message_is_plain_text_for_one_person_and_can_be_left():
    message = mailing.build_message(subject="Hello", body="Body", to="Ana@example.org", sender="news@example.org",
                                    unsubscribe_to="contact@example.org")
    assert message["To"] == "ana@example.org" and message.get_content_type() == "text/plain"
    assert message["List-Unsubscribe"] == "<mailto:contact@example.org?subject=unsubscribe>"
    with pytest.raises(mailing.MailingError):
        mailing.build_message(subject="Hi\nBcc: x@y.org", body="b", to="ana@example.org", sender="n@example.org")


def test_senders_are_pluggable():
    with pytest.raises(mailing.MailingError):
        mailing.make_sender("carrier-pigeon", {})

    @mailing.register_sender("memory")
    class MemorySender:
        def __init__(self, env):
            self.got = []

        def send_batch(self, messages):
            self.got += messages
            return mailing.BatchResult(sent=len(messages))

    try:
        sender = mailing.make_sender("memory", {})
        assert sender.name == "memory" and sender.send_batch([object()]).sent == 1
        assert {"smtp", "dryrun", "memory"} <= set(mailing.SENDERS)
    finally:
        mailing.SENDERS.pop("memory", None)


def test_smtp_needs_a_complete_configuration_and_prefers_the_dedicated_account():
    with pytest.raises(mailing.SenderNotConfigured):
        mailing.SmtpSender({"MAIL_USER": "x@example.org"})
    env = {**SMTP_ENV, "DIGEST_MAIL_USER": "digest@example.org", "DIGEST_MAIL_PASSWORD": "other", "MAIL_FROM": "Tracker Watch <news@example.org>"}
    config = mailing.smtp_config(env)
    assert config["user"] == "digest@example.org" and config["password"] == "other" and config["port"] == 587
    assert mailing.smtp_config({**SMTP_ENV, "MAIL_SMTP_PORT": "abc"}) is None
    half = mailing.smtp_config({**SMTP_ENV, "DIGEST_MAIL_USER": "digest@example.org"})  # no password of its own
    assert half["user"] == "news@example.org" and half["password"] == "pw"  # falls back whole, never mixed


def test_smtp_sends_one_message_each_over_an_encrypted_connection_and_a_bad_address_does_not_stop_the_rest():
    sender = mailing.SmtpSender(SMTP_ENV, smtp=FakeSMTP, sleep=lambda s: None)
    messages = [mailing.build_message(subject="s", body="b", to=to, sender="news@example.org")
                for to in ("ana@example.org", "bad@example.org", "ben@example.org")]
    result = sender.send_batch(messages)
    server = FakeSMTP.instances[0]
    assert server.tls and server.user == "news@example.org" and len(FakeSMTP.instances) == 1  # one connection
    assert (result.sent, result.failed) == (2, 1)
    assert [str(m["To"]) for m in server.sent] == ["ana@example.org", "ben@example.org"]
    assert all("@example.org" not in e or "***@" in e for e in result.errors)  # errors never carry a full address


def test_smtp_on_port_465_uses_tls_from_the_start_and_the_cap_leaves_the_rest_for_another_run():
    used = []

    class FakeSSL(FakeSMTP):
        def __init__(self, host, port, timeout=None, context=None):
            super().__init__(host, port, timeout, context)
            used.append(port)

    sender = mailing.SmtpSender({**SMTP_ENV, "MAIL_SMTP_PORT": "465", "DIGEST_MAX_PER_RUN": "2"}, smtp_ssl=FakeSSL,
                                sleep=lambda s: None)
    messages = [mailing.build_message(subject="s", body="b", to=f"u{i}@example.org", sender="news@example.org") for i in range(5)]
    result = sender.send_batch(messages)
    assert used == [465] and result.sent == 2 and any("cap" in e for e in result.errors)


# --- the digest -------------------------------------------------------------------------------------------------------

def draft_folder(tmp_path, name="2026-10-12", posted=True, posts=("First post.", "Second post.")):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "bluesky.json").write_text(json.dumps({"date": "2026-10-12", "posts": list(posts)}), encoding="utf-8")
    if posted:
        (folder / "bluesky.posted.json").write_text(json.dumps({"complete": True, "posts": [{"url": "https://bsky.app/profile/x/post/1"}]}), encoding="utf-8")
    return folder


def test_kinds_and_the_default_is_weekly_and_midweek():
    assert [digest.kind_of(n) for n in ("2026-10-12", "2026-10-14-midweek", "2026-10-12-alert", "2026-10-12-hook", "2026-10-13-announce")] == \
        ["weekly", "midweek", "alert", "hook", "announce"]
    assert digest.allowed_kinds({}) == ("weekly", "midweek") and digest.allowed_kinds({"DIGEST_KINDS": "weekly, alert"}) == ("weekly", "alert")


def test_the_digest_has_the_posts_the_way_to_leave_and_no_tracking():
    subject, body = digest.build_digest({"date": "2026-10-12", "posts": ["One.", "Two."]}, "weekly", site_url="https://site.example/",
                                        sign="Tracker Watch, contact@example.org", discussion_url="https://bsky.app/p/1")
    assert subject == "[Tracker Watch] Weekly check, 2026-10-12"
    assert "One." in body and "Two." in body and "https://site.example/" in body and "https://bsky.app/p/1" in body
    assert "reply with the word UNSUBSCRIBE" in body and "contact@example.org" in body
    assert "<img" not in body.lower() and "utm_" not in body and "pixel" not in body.lower().replace("no pixels", "")


def test_what_only_makes_sense_on_bluesky_is_left_out_of_the_email():
    assert digest.clean_post("Weekly check: 41 of 48 sites. Thread below. Automated post. #privacy #adtech #GDPR") == \
        "Weekly check: 41 of 48 sites."
    assert digest.clean_post("Midweek: Google 28 of 39. Automated post.") == "Midweek: Google 28 of 39."
    assert digest.clean_post("Keeps #inside the text and its end.") == "Keeps #inside the text and its end."


def test_a_dry_run_sends_nothing_shows_the_message_and_does_not_mark_it_as_sent(tmp_path):
    folder = draft_folder(tmp_path)
    code, lines = digest.run(folder, SMTP_ENV, subscribers=people(), now=None)
    text = "\n".join(lines)
    assert code == 0 and "DRY RUN" in text and "3 sent" in text and "First post." in text
    assert "@example.org" not in text.replace("contact@example.org", "") and not (folder / digest.MARKER).exists()


def test_sending_needs_a_published_draft_the_switch_and_a_contact_address(tmp_path):
    unpublished = draft_folder(tmp_path, "2026-10-12", posted=False)
    assert "not been published" in "\n".join(digest.run(unpublished, SMTP_ENV, send=True, subscribers=people())[1])
    folder = draft_folder(tmp_path, "2026-10-14-midweek")
    code, lines = digest.run(folder, {k: v for k, v in SMTP_ENV.items() if k != "DIGEST_ENABLED"}, send=True, subscribers=people())
    assert code == 3 and "DIGEST_ENABLED" in lines[0]
    code, lines = digest.run(folder, {k: v for k, v in SMTP_ENV.items() if k != "CONTACT_EMAIL"}, send=True, subscribers=people())
    assert code == 3 and "CONTACT_EMAIL" in lines[0]


def test_it_sends_once_and_remembers_it_without_keeping_addresses(tmp_path):
    folder = draft_folder(tmp_path)
    mailing.SENDERS["smtp"] = lambda env: mailing.SmtpSender(env, smtp=FakeSMTP, sleep=lambda s: None)
    try:
        code, lines = digest.run(folder, SMTP_ENV, send=True, subscribers=people())
        assert code == 0 and "3 sent, 0 failed" in lines[0]
        sent = FakeSMTP.instances[0].sent
        assert len(sent) == 3 and all(len(m.get_all("To")) == 1 for m in sent)  # one message each, nobody sees the others
        marker = json.loads((folder / digest.MARKER).read_text(encoding="utf-8"))
        assert marker["recipients"] == 3 and "@" not in json.dumps(marker)
        again_code, again_lines = digest.run(folder, SMTP_ENV, send=True, subscribers=people())
        assert again_code == 0 and "already sent" in again_lines[0] and len(FakeSMTP.instances) == 1
        digest.run(folder, SMTP_ENV, send=True, again=True, subscribers=people())
        assert len(FakeSMTP.instances) == 2
    finally:
        mailing.SENDERS["smtp"] = mailing.SmtpSender


def test_nobody_is_written_to_when_there_are_no_confirmed_subscribers_or_the_kind_is_off(tmp_path):
    folder = draft_folder(tmp_path)
    only_pending = mailing.add([], "dan@example.org", source="mailto", note="asked", today=TODAY)
    assert "no confirmed subscribers" in digest.run(folder, SMTP_ENV, send=True, subscribers=only_pending)[1][0]
    alert = draft_folder(tmp_path, "2026-10-12-alert")
    assert "not enabled" in digest.run(alert, SMTP_ENV, send=True, subscribers=people())[1][0]
    assert digest.run(tmp_path / "missing", SMTP_ENV)[0] == 2


def test_an_unknown_or_unconfigured_sender_is_reported_not_raised(tmp_path):
    folder = draft_folder(tmp_path)
    code, lines = digest.run(folder, SMTP_ENV, send=True, sender_name="carrier-pigeon", subscribers=people())
    assert code == 1 and "unknown sender" in lines[0]
    env = {k: v for k, v in SMTP_ENV.items() if k != "MAIL_PASSWORD"}
    code, lines = digest.run(folder, env, send=True, subscribers=people())
    assert code == 1 and "mail settings are not complete" in lines[0]


# --- the command to manage the list -----------------------------------------------------------------------------------

def test_the_list_command_never_prints_a_full_address_unless_asked(tmp_path, capsys):
    path = str(tmp_path / "subscribers.json")
    assert subscribers_cli.main(["--file", path, "add", "ana@example.org", "--note", "asked by email", "--interest"]) == 0
    assert subscribers_cli.main(["--file", path, "confirm", "ana@example.org"]) == 0
    assert subscribers_cli.main(["--file", path, "add", "ben@example.org", "--note", "asked by email"]) == 0
    capsys.readouterr()
    assert subscribers_cli.main(["--file", path, "list"]) == 0
    masked = capsys.readouterr().out
    assert "ana@example.org" not in masked and "a***@example.org" in masked and "1 confirmed" in masked and "1 pending" in masked
    subscribers_cli.main(["--file", path, "list", "--full"])
    assert "ana@example.org" in capsys.readouterr().out
    assert subscribers_cli.main(["--file", path, "remove", "ana@example.org"]) == 0
    assert mailing.load_subscribers({}, path)[0]["status"] == "unsubscribed"
    assert subscribers_cli.main(["--file", path, "add", "bad", "--note", "x"]) == 1


def test_pushing_the_list_to_the_secret_sends_it_on_stdin_and_prints_no_address(tmp_path, capsys):
    path = tmp_path / "subscribers.json"
    mailing.save_subscribers(people(), path)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return type("Done", (), {"returncode": 0})()

    assert subscribers_cli.main(["--file", str(path), "push-secret"], runner=runner) == 0
    command, kwargs = calls[0]
    assert command[:4] == ["gh", "secret", "set", "SUBSCRIBERS_JSON"] and "ana@example.org" in kwargs["input"]
    assert "@" not in capsys.readouterr().out
    assert subscribers_cli.main(["--file", str(tmp_path / "none.json"), "push-secret"], runner=runner) == 1
