"""Emails the owner when a scheduled workflow did not run at all.

alert-failure only speaks when a run ENDS in failure. If GitHub never starts a scheduled run (a dropped schedule, workflows
switched off, a stuck queue), nothing fails and nothing is said: silence would look like "it worked". This check looks at what
GitHub says about the runs of the workflows that must have run by now:

  weekly-scan      a run created since this Monday 00:00 UTC
  post-midweek     a run created since this Wednesday 00:00 UTC (checked from Wednesday on)
  monthly-report   a run created since the 3rd of the month (checked from the 4th on)
  data-release     a run created since the 4th of the month (checked from the 5th on)

A run counts as long as it is on its way, finished well, or failed (a failure is already reported by alert-failure, which
would otherwise be repeated here). A window with no run, or only runs that were cancelled or timed out, is reported.

The GitHub token is read from the environment (GH_TOKEN) and only used to read run lists; nothing is changed.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

from . import notify

WORKFLOWS = ("weekly-scan", "post-midweek", "monthly-report", "data-release")
ON_ITS_WAY = {"queued", "in_progress", "waiting", "pending", "requested"}
HANDLED_ELSEWHERE = {"success", "failure"}   # failure: alert-failure has already written


def windows(now: datetime) -> dict[str, datetime]:
    """workflow -> the moment from which a run must exist, only for the workflows that are due at `now` (UTC)."""
    today = now.astimezone(timezone.utc).date()
    start = lambda d: datetime(d.year, d.month, d.day, tzinfo=timezone.utc)  # noqa: E731
    monday = today - timedelta(days=today.weekday())
    due = {"weekly-scan": start(monday)}
    if today.weekday() >= 2:
        due["post-midweek"] = start(monday + timedelta(days=2))
    if today.day >= 4:
        due["monthly-report"] = start(date(today.year, today.month, 3))
    if today.day >= 5:
        due["data-release"] = start(date(today.year, today.month, 4))
    return due


def _stamp(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def problems(now: datetime, runs: dict[str, list[dict]]) -> list[str]:
    """One sentence per workflow that should have run since its window opened and did not. `runs`: workflow -> run list."""
    found = []
    for name, since in windows(now).items():
        recent = [r for r in runs.get(name, []) if _stamp(r["created_at"]) >= since]
        if not recent:
            found.append(f"{name}: no run was created since {since:%Y-%m-%d %H:%M} UTC.")
        elif not any(r.get("status") in ON_ITS_WAY or r.get("conclusion") in HANDLED_ELSEWHERE for r in recent):
            ends = ", ".join(sorted({str(r.get("conclusion")) for r in recent}))
            found.append(f"{name}: since {since:%Y-%m-%d %H:%M} UTC every run ended as {ends} and none finished well.")
    return found


def fetch_runs(repo: str, token: str, workflow: str, *, opener=urllib.request.urlopen, timeout: float = 30) -> list[dict]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}.yml/runs?per_page=15",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tracker-watch-watchdog"})
    with opener(request, timeout=timeout) as response:
        return json.load(response).get("workflow_runs", [])


def build_message(found: list[str], run_url: str, config: dict):
    message = notify.EmailMessage()
    message["Subject"] = "[Tracker Watch] NOT RUNNING: " + ", ".join(line.split(":")[0] for line in found)[:150]
    message["From"], message["To"] = config["sender"], config["to"]
    message.set_content("\n".join([
        "A scheduled workflow that should have run by now did not.", "", *found, "",
        "Nothing has failed (a failure would have its own notice): GitHub simply did not start it, or every run was cancelled.",
        "Open Actions, look at the workflow's list of runs and start it by hand (Run workflow) if it is really missing.", "",
        f"Check: {' '.join((run_url or '?').split())[:200]}", "",
        "This notice comes from the Tracker Watch watchdog."]))
    return message


def main(argv: list[str] | None = None, env=None, *, now: datetime | None = None, fetch=fetch_runs) -> int:
    env = os.environ if env is None else env
    repo, token = (env.get("GITHUB_REPOSITORY") or "").strip(), (env.get("GH_TOKEN") or "").strip()
    if not repo or not token:
        print("GITHUB_REPOSITORY and GH_TOKEN are needed", file=sys.stderr)
        return 2
    now = now or datetime.now(timezone.utc)
    try:
        runs = {name: fetch(repo, token, name) for name in windows(now)}
    except Exception as exc:  # the check itself must be loud when it cannot look
        print(f"could not read the run lists ({type(exc).__name__})", file=sys.stderr)
        return 2
    found = problems(now, runs)
    if not found:
        print("every scheduled workflow due by now has run: " + ", ".join(windows(now)))
        return 0
    print("\n".join(found))
    config = notify.mail_config(env)
    if config is None:
        print("not configured: no notice sent", file=sys.stderr)
        return 1
    try:
        notify.send(config, build_message(found, env.get("WATCHDOG_RUN_URL") or "", config))
    except Exception as exc:
        print(f"watchdog notice NOT sent ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"watchdog notice sent to {notify.mask(config['to'])}")
    return 1   # the run itself turns red, so the gap is also visible in the Actions list


if __name__ == "__main__":
    raise SystemExit(main())
