"""The second attempt: only unmeasured sites of a fresh scan, never a site refused again and again, a retry replaces a report
only when it is usable, and the autonomy log says what happened."""
import json
from datetime import date

import yaml

from traceguard import autonomy, retry_blocked
from traceguard.retry_blocked import candidates, run

TODAY = date(2026, 11, 24)


def report(name, status="ok", confidence="high"):
    summary = {"status": status, "confidence": confidence, "http_status": 403 if status == "blocked" else 200}
    return {"site": {"name": name, "url": f"https://{name}.example/", "first_party_domains": [f"{name}.example"]},
            "measurement": {"passes": 3}, "summary": summary}


def write(root, day, reports):
    folder = root / "runs" / day
    folder.mkdir(parents=True, exist_ok=True)
    for name, rep in reports.items():
        (folder / f"{name}.json").write_text(json.dumps(rep), encoding="utf-8")
    return folder


def week(root, day, **statuses):
    return write(root, day, {n: report(n, s) for n, s in statuses.items()})


def test_only_the_unmeasured_sites_of_a_recent_scan_are_picked(tmp_path):
    week(tmp_path, "2026-11-23", a="ok", b="blocked", c="error")
    folder, picked = candidates(tmp_path / "runs", TODAY)
    assert folder.name == "2026-11-23" and sorted(n for n, _ in picked) == ["b.json", "c.json"]
    assert candidates(tmp_path / "runs", date(2026, 11, 27)) == (None, [])   # four days old: not due
    assert candidates(tmp_path / "empty", TODAY) == (None, [])


def test_low_confidence_counts_as_not_measured_well_and_a_retried_site_is_not_retried_twice(tmp_path):
    write(tmp_path, "2026-11-23", {"a": report("a", "ok", "low"), "b": {**report("b", "blocked"), "retry": {"at": "x"}}})
    _, picked = candidates(tmp_path / "runs", TODAY)
    assert [n for n, _ in picked] == ["a.json"]


def test_a_site_refused_in_the_four_scans_before_is_not_asked_again(tmp_path):
    for day in ("2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"):
        week(tmp_path, day, always="blocked", flaps="blocked")
    week(tmp_path, "2026-11-09", always="blocked", flaps="ok")   # flaps was measured once: it stays a candidate
    week(tmp_path, "2026-11-23", always="blocked", flaps="blocked")
    _, picked = candidates(tmp_path / "runs", TODAY)
    assert [n for n, _ in picked] == ["flaps.json"]


def test_a_usable_retry_replaces_the_report_and_is_recorded_and_an_unusable_one_changes_nothing(tmp_path):
    folder = week(tmp_path, "2026-11-23", a="ok", b="blocked", c="blocked")
    answers = {"b": report("b", "ok"), "c": report("c", "blocked")}
    logged = []
    run(tmp_path / "runs", tmp_path / "autonomy", today=TODAY, scan=lambda old: answers[old["site"]["name"]], log=logged.append)
    b = json.loads((folder / "b.json").read_text(encoding="utf-8"))
    assert b["summary"]["status"] == "ok" and b["retry"]["first_attempt"] == {"status": "blocked", "http_status": 403, "confidence": "high"}
    assert "retry" not in json.loads((folder / "c.json").read_text(encoding="utf-8"))
    assert json.loads((folder / "c.json").read_text(encoding="utf-8"))["summary"]["status"] == "blocked"
    notes = autonomy.load_notes(tmp_path / "autonomy")["2026-11-23"]
    assert any("second attempt the next day: b" in n["text"] for n in notes) and any("Still not measurable" in n["text"] and "c" in n["text"] for n in notes)


def test_a_crash_on_one_site_does_not_stop_the_others(tmp_path):
    folder = week(tmp_path, "2026-11-23", b="blocked", c="blocked")

    def scan(old):
        if old["site"]["name"] == "b":
            raise RuntimeError("browser died")
        return report("c", "ok")

    assert run(tmp_path / "runs", tmp_path / "autonomy", today=TODAY, scan=scan, log=lambda *_: None) == 0
    assert json.loads((folder / "c.json").read_text(encoding="utf-8"))["summary"]["status"] == "ok"


def test_the_workflow_is_scheduled_on_tuesday_shares_the_scan_lock_and_pushes_with_retry():
    text = (retry_blocked.__file__.rsplit("traceguard", 1)[0] + ".github/workflows/retry-blocked.yml")
    content = open(text, encoding="utf-8").read()
    data = yaml.safe_load(content)
    assert (data.get("on") or data.get(True))["schedule"] == [{"cron": "11 8 * * 2"}]
    assert data["concurrency"]["group"] == "weekly-scan" and data["permissions"] == {"contents": "write", "actions": "write"}
    assert "python -m traceguard.retry_blocked data/runs" in content and "push_with_retry.sh" in content
