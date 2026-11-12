"""The watchdog: a scheduled workflow that never started is reported; one that ran, is running or failed is not."""
import pathlib
from datetime import datetime, timezone

import yaml

from traceguard import watchdog

ROOT = pathlib.Path(__file__).parent.parent
MAIL = {"MAIL_SMTP_HOST": "smtp.example.org", "MAIL_USER": "u@example.org", "MAIL_PASSWORD": "mail-pass-789",
        "NOTIFY_EMAIL": "owner@example.com", "GITHUB_REPOSITORY": "o/r", "GH_TOKEN": "token-xyz"}


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def run(created, conclusion="success", status="completed"):
    return {"created_at": created + "Z", "conclusion": conclusion, "status": status}


def test_only_the_workflows_due_by_now_are_checked():
    assert set(watchdog.windows(at("2026-11-04T19:17"))) == {"weekly-scan", "post-midweek", "monthly-report"}   # Wed 4th
    assert set(watchdog.windows(at("2026-11-11T19:17"))) == {"weekly-scan", "post-midweek", "monthly-report", "data-release"}
    assert set(watchdog.windows(at("2026-11-02T10:00"))) == {"weekly-scan"}   # Monday the 2nd: nothing else is due yet
    assert watchdog.windows(at("2026-11-11T19:17"))["weekly-scan"] == at("2026-11-09T00:00")


def test_a_late_but_started_run_is_not_a_problem_and_a_missing_one_is():
    now = at("2026-11-11T19:17")
    runs = {"weekly-scan": [run("2026-11-09T16:59")], "post-midweek": [run("2026-11-11T15:57")],
            "monthly-report": [run("2026-11-03T16:43")], "data-release": [run("2026-11-04T17:40")]}
    assert watchdog.problems(now, runs) == []
    runs["weekly-scan"] = [run("2026-11-02T16:41")]   # only last week's scan
    (line,) = watchdog.problems(now, runs)
    assert line.startswith("weekly-scan: no run was created since 2026-11-09 00:00 UTC")


def test_a_failure_is_left_to_alert_failure_but_cancelled_runs_and_unfinished_ones_are_handled():
    now = at("2026-11-11T19:17")
    base = {"weekly-scan": [run("2026-11-09T16:59")], "monthly-report": [run("2026-11-03T16:43")],
            "data-release": [run("2026-11-04T17:40")]}
    failed = dict(base, **{"post-midweek": [run("2026-11-11T15:57", "failure")]})
    assert watchdog.problems(now, failed) == []
    cancelled = dict(base, **{"post-midweek": [run("2026-11-11T15:57", "cancelled")]})
    assert "every run ended as cancelled" in watchdog.problems(now, cancelled)[0]
    running = dict(base, **{"post-midweek": [run("2026-11-11T15:57", None, "in_progress")]})
    assert watchdog.problems(now, running) == []


def test_main_sends_one_notice_and_exits_red_when_something_is_missing(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(watchdog.notify, "send", lambda config, message, **kw: sent.append(message))
    empty = lambda repo, token, name: []   # noqa: E731
    code = watchdog.main(env=dict(MAIL, WATCHDOG_RUN_URL="https://github.com/o/r/actions/runs/9"),
                         now=at("2026-11-11T19:17"), fetch=empty)
    assert code == 1 and len(sent) == 1
    assert sent[0]["Subject"].startswith("[Tracker Watch] NOT RUNNING: weekly-scan")
    body = sent[0].get_content()
    assert "mail-pass-789" not in body and "token-xyz" not in body and "https://github.com/o/r/actions/runs/9" in body
    assert "token-xyz" not in capsys.readouterr().out


def test_main_is_quiet_when_all_is_well_and_loud_when_it_cannot_look(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(watchdog.notify, "send", lambda config, message, **kw: sent.append(message))
    fine = lambda repo, token, name: [run("2026-11-12T01:00")]   # noqa: E731
    assert watchdog.main(env=MAIL, now=at("2026-11-12T19:17"), fetch=fine) == 0 and not sent

    def broken(repo, token, name):
        raise OSError("down")
    assert watchdog.main(env=MAIL, now=at("2026-11-12T19:17"), fetch=broken) == 2
    assert watchdog.main(env={}, now=at("2026-11-12T19:17"), fetch=fine) == 2
    assert not sent


def test_the_workflow_is_read_only_pinned_and_scheduled_twice_a_week():
    text = (ROOT / ".github/workflows/watchdog.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["permissions"] == {"actions": "read", "contents": "read"}
    assert (data.get("on") or data.get(True))["schedule"] == [{"cron": "17 19 * * 3,5"}]
    assert "python -m traceguard.watchdog" in text
