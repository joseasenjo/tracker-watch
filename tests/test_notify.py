"""The email notice after a publication: optional, encrypted, and never able to break a publication."""
import smtplib
from datetime import date

from traceguard.notify import build_message, mail_config, mask, notify_published
from traceguard.publish import main

from test_publish import BSKY_ENV, FakeServer, write_draft

TODAY = date(2026, 10, 5)
MAIL = {"MAIL_SMTP_HOST": "smtp.example.org", "MAIL_USER": "bot@example.org", "MAIL_PASSWORD": "mail-pass-789",
        "NOTIFY_EMAIL": "owner@example.com"}
DRAFT = {"date": "2026-10-12", "posts": ["First post text", "Second post text"]}
STATE = {"posts": [{"url": "https://bsky.app/profile/did:plc:abc/post/r1"}, {"url": "https://bsky.app/profile/did:plc:abc/post/r2"}],
         "finished_at": "2026-10-12T07:40:00.000Z", "complete": True}


class FakeSMTP:
    sent = []
    log = []
    fail = False

    def __init__(self, host, port, timeout=30, context=None):
        self.host, self.port = host, port
        FakeSMTP.log.append(("connect", host, port, context is not None))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        FakeSMTP.log.append(("starttls", context is not None))

    def login(self, user, password):
        FakeSMTP.log.append(("login", user))
        if FakeSMTP.fail:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials mail-pass-789")

    def send_message(self, message):
        FakeSMTP.sent.append(message)


def reset():
    FakeSMTP.sent, FakeSMTP.log, FakeSMTP.fail = [], [], False


def test_the_notice_is_off_unless_every_setting_is_there():
    assert mail_config({}) is None
    for name in ("MAIL_SMTP_HOST", "MAIL_USER", "MAIL_PASSWORD"):
        assert mail_config({k: v for k, v in MAIL.items() if k != name}) is None
    assert mail_config({k: v for k, v in MAIL.items() if k != "NOTIFY_EMAIL"}) is None
    assert mail_config({**{k: v for k, v in MAIL.items() if k != "NOTIFY_EMAIL"}, "CONTACT_EMAIL": "c@example.com"})["to"] == "c@example.com"
    assert mail_config({**MAIL, "NOTIFY_EMAIL": "a@b.com\nBcc: x@y.com"}) is None  # no header injection
    assert mail_config({**MAIL, "MAIL_SMTP_PORT": "abc"}) is None
    assert mail_config(MAIL)["port"] == 587 and mail_config({**MAIL, "MAIL_SMTP_PORT": "465"})["port"] == 465


def test_the_message_names_the_kind_the_date_and_every_post():
    config = mail_config(MAIL)
    weekly = build_message(DRAFT, "bluesky", STATE, "data/drafts/2026-10-12", config)
    assert weekly["Subject"] == "[Tracker Watch] Published on Bluesky: weekly thread 2026-10-12"
    assert weekly["To"] == "owner@example.com" and weekly["From"] == "bot@example.org"
    body = weekly.get_content()
    assert "post/r1" in body and "post/r2" in body and "First post text" in body and "Second post text" in body
    assert "mail-pass-789" not in body and "mail-pass-789" not in str(weekly)
    midweek = build_message(DRAFT, "bluesky", STATE, "data/drafts/2026-10-12-midweek", config)
    assert "mid-week post" in midweek["Subject"]


def test_a_mastodon_publication_gets_its_own_notice_with_the_link_to_the_post():
    config = mail_config(MAIL)
    state = {"posts": [{"id": "9", "url": "https://mastodon.social/@trackerwatch/9"}], "finished_at": "2026-10-19T09:05:00Z"}
    message = build_message({"date": "2026-10-19", "posts": ["Hello from Mastodon"]}, "mastodon", state, "data/drafts/2026-10-19", config)
    assert message["Subject"] == "[Tracker Watch] Published on Mastodon: weekly thread 2026-10-19"
    body = message.get_content()
    assert "is now published on Mastodon" in body and "https://mastodon.social/@trackerwatch/9" in body and "Hello from Mastodon" in body
    assert "Bluesky" not in message["Subject"] and "mail-pass-789" not in str(message)


def test_it_sends_over_an_encrypted_connection_and_never_prints_the_password():
    reset()
    line = notify_published(DRAFT, "bluesky", STATE, "data/drafts/2026-10-12", MAIL, smtp=FakeSMTP)
    assert line == "email notice sent to o***@example.com" and "mail-pass-789" not in line
    assert [e[0] for e in FakeSMTP.log] == ["connect", "starttls", "login"]  # encryption before the password
    assert FakeSMTP.log[1] == ("starttls", True) and len(FakeSMTP.sent) == 1


def test_port_465_uses_tls_from_the_start():
    reset()
    used = []

    class Ssl(FakeSMTP):
        def __init__(self, host, port, timeout=30, context=None):
            used.append((host, port, context is not None))
            super().__init__(host, port, timeout, context)

    notify_published(DRAFT, "bluesky", STATE, "data/drafts/2026-10-12", {**MAIL, "MAIL_SMTP_PORT": "465"},
                     smtp=None, smtp_ssl=Ssl)
    assert used == [("smtp.example.org", 465, True)] and len(FakeSMTP.sent) == 1


def test_a_mail_failure_is_reported_without_the_secret_and_never_raises():
    reset()
    FakeSMTP.fail = True
    line = notify_published(DRAFT, "bluesky", STATE, "data/drafts/2026-10-12", MAIL, smtp=FakeSMTP)
    assert "NOT sent" in line and "SMTPAuthenticationError" in line and "mail-pass-789" not in line
    assert "no email notice" in notify_published(DRAFT, "bluesky", STATE, "d/x", {}, smtp=FakeSMTP)


def test_a_publication_sends_one_notice_and_a_broken_mail_server_does_not_fail_it(tmp_path, capsys):
    reset()
    write_draft(tmp_path / "d", "bluesky")
    server = FakeServer()
    assert main([str(tmp_path / "d"), "--platform", "bluesky", "--send"], env={**BSKY_ENV, **MAIL}, http=server,
                today=TODAY, smtp=FakeSMTP) == 0
    out = capsys.readouterr().out
    assert "email notice sent to o***@example.com" in out and len(FakeSMTP.sent) == 1
    assert "mail-pass-789" not in out and "app-pass-123" not in out
    # a repeat publishes nothing, and says so: silence must never mean "it worked"
    main([str(tmp_path / "d"), "--platform", "bluesky", "--send", "--skip-if-published"], env={**BSKY_ENV, **MAIL},
         http=server, today=TODAY, smtp=FakeSMTP)
    assert len(FakeSMTP.sent) == 2 and "NOT published on Bluesky (skipped)" in FakeSMTP.sent[1]["Subject"]
    # broken mail server on a new draft: the publication still succeeds
    reset()
    FakeSMTP.fail = True
    write_draft(tmp_path / "e", "bluesky")
    assert main([str(tmp_path / "e"), "--platform", "bluesky", "--send"], env={**BSKY_ENV, **MAIL}, http=FakeServer(),
                today=TODAY, smtp=FakeSMTP) == 0
    assert "NOT sent" in capsys.readouterr().out


def test_without_mail_settings_the_publisher_just_says_so(tmp_path, capsys):
    write_draft(tmp_path / "d", "bluesky")
    assert main([str(tmp_path / "d"), "--platform", "bluesky", "--send"], env=BSKY_ENV, http=FakeServer(), today=TODAY) == 0
    assert "no email notice" in capsys.readouterr().out


def test_mask_hides_the_address():
    assert mask("owner@example.com") == "o***@example.com" and mask("nonsense") == "***"


def test_the_test_command_reports_missing_and_broken_settings_without_the_secret(capsys, monkeypatch):
    from traceguard import notify
    assert notify.main(["test"], env={}) == 1 and "not configured" in capsys.readouterr().err
    assert notify.main(["nonsense"], env=MAIL) == 2
    monkeypatch.setattr(notify, "send", lambda config, message, **kw: (_ for _ in ()).throw(smtplib.SMTPAuthenticationError(535, b"mail-pass-789")))
    assert notify.main(["test"], env=MAIL) == 1
    err = capsys.readouterr().err
    assert "NOT sent" in err and "mail-pass-789" not in err
    sent = []
    monkeypatch.setattr(notify, "send", lambda config, message, **kw: sent.append(message))
    assert notify.main(["test"], env=MAIL) == 0 and sent[0]["Subject"].endswith("Test of the publication notice")
    assert "o***@example.com" in capsys.readouterr().out


def test_a_failed_workflow_gets_a_notice_with_the_log_link_and_nothing_secret(capsys, monkeypatch):
    from traceguard import notify
    env = dict(MAIL, FAILED_WORKFLOW="weekly-scan", FAILED_RUN_URL="https://github.com/o/r/actions/runs/1",
               FAILED_EVENT="schedule", FAILED_AT="2026-10-19T07:00:11Z")
    sent = []
    monkeypatch.setattr(notify, "send", lambda config, message, **kw: sent.append(message))
    assert notify.main(["failure"], env=env) == 0
    assert sent[0]["Subject"] == "[Tracker Watch] FAILED: weekly-scan" and sent[0]["To"] == "owner@example.com"
    body = sent[0].get_content()
    assert "https://github.com/o/r/actions/runs/1" in body and "mail-pass-789" not in body
    assert notify.main(["failure"], env={}) == 1 and "not configured" in capsys.readouterr().err
    hostile = dict(env, FAILED_WORKFLOW="x\r\nBcc: attacker@example.org")
    notify.main(["failure"], env=hostile)
    assert "\n" not in sent[1]["Subject"] and "Bcc" not in sent[1].keys()


def test_the_scheduled_posting_workflows_email_a_failed_mastodon_or_digest_step():
    import pathlib
    import yaml
    for name in ("weekly-scan", "post-midweek"):
        steps = next(j for j in yaml.safe_load(pathlib.Path(f".github/workflows/{name}.yml").read_text(encoding="utf-8"))["jobs"].values()
                     if any(s.get("id") == "mastodon" for s in j["steps"]))["steps"]
        ids = [s.get("id") for s in steps]
        assert "mastodon" in ids and "digest" in ids
        warn = next(s for s in steps if "failure" in str(s.get("run", "")) or s.get("name", "").startswith("Warn by email"))
        assert "steps.mastodon.outcome == 'failure'" in warn["if"] and "steps.digest.outcome == 'failure'" in warn["if"]
        assert steps.index(warn) > ids.index("digest")


def test_a_failed_publication_emails_that_it_was_not_published_and_why(tmp_path, capsys):
    reset()
    write_draft(tmp_path / "d", "bluesky")
    assert main([str(tmp_path / "d"), "--platform", "bluesky", "--send"], env=MAIL, http=FakeServer(), today=TODAY, smtp=FakeSMTP) == 1
    [mail] = FakeSMTP.sent
    assert mail["Subject"].startswith("[Tracker Watch] NOT published on Bluesky (FAILED)")
    body = mail.get_content()
    assert "missing environment variable" in body and "needs your attention" in body and "mail-pass-789" not in body


def test_the_not_published_notice_hides_secrets_and_tells_how_far_a_thread_got():
    from traceguard.notify import build_not_published_message
    config = mail_config(MAIL)
    message = build_not_published_message("mastodon", "HTTP 500 token tok-secret-456 rejected", "2026-10-12", config, date="2026-10-12",
                                          posts_out=2, total=5, secrets=("tok-secret-456",))
    body = message.get_content()
    assert "tok-secret-456" not in body and "***" in body and "Posts already out: 2 of 5" in body
    assert message["Subject"].startswith("[Tracker Watch] NOT published on Mastodon (FAILED): weekly thread")


def test_the_workflow_can_say_a_post_was_left_out_on_purpose(monkeypatch, capsys):
    from traceguard import notify
    sent = []
    monkeypatch.setattr(notify, "send", lambda config, message, **kw: sent.append(message))
    env = dict(MAIL, NOT_PUBLISHED_PLATFORM="bluesky,mastodon", NOT_PUBLISHED_REASON="review mode is on", NOT_PUBLISHED_FOLDER="2026-10-19")
    assert notify.main(["not-published"], env=env) == 0
    assert [m["Subject"] for m in sent] == ["[Tracker Watch] NOT published on Bluesky (skipped): weekly thread",
                                            "[Tracker Watch] NOT published on Mastodon (skipped): weekly thread"]
    assert "left out on purpose" in sent[0].get_content() and "review mode is on" in sent[0].get_content()
    assert notify.main(["not-published"], env=MAIL) == 2
