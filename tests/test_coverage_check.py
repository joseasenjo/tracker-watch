"""The coverage check: it names the sites lost since last week and e-mails only below the warning level."""
import json

from traceguard import autonomy, coverage_check

MAIL = {"MAIL_SMTP_HOST": "smtp.example.org", "MAIL_USER": "u@example.org", "MAIL_PASSWORD": "mail-pass-789",
        "NOTIFY_EMAIL": "owner@example.com"}


def report(name, status="ok", confidence="high"):
    return {"site": {"name": name, "url": f"https://{name}/"}, "generated_at": "2026-11-16T10:00:00+00:00",
            "summary": {"status": status, "confidence": confidence, "metrics": {}}}


def write(tmp_path, day, reports):
    folder = tmp_path / "runs" / day
    folder.mkdir(parents=True, exist_ok=True)
    for r in reports:
        (folder / f"{r['site']['name']}.json").write_text(json.dumps(r), encoding="utf-8")
    return tmp_path / "runs"


def two_scans(tmp_path, now_bad):
    names = [f"s{i}" for i in range(10)]
    write(tmp_path, "2026-11-09", [report(n) for n in names])
    return write(tmp_path, "2026-11-16", [report(n, status="blocked" if n in now_bad else "ok") for n in names])


def test_it_counts_the_usable_sites_and_names_the_ones_lost_since_the_scan_before(tmp_path):
    runs = two_scans(tmp_path, {"s1", "s2", "s3"})
    result = coverage_check.assess(runs, minimum=8)
    assert (result["measured"], result["total"], result["low"]) == (7, 10, True)
    assert result["lost"] == ["s1", "s2", "s3"] and result["unmeasured"] == ["s1", "s2", "s3"]
    assert coverage_check.assess(runs, minimum=7)["low"] is False


def test_a_medium_confidence_site_counts_as_measured_and_a_low_one_does_not(tmp_path):
    write(tmp_path, "2026-11-16", [report("a", confidence="medium"), report("b", confidence="low"), report("c")])
    assert coverage_check.assess(tmp_path / "runs", minimum=1)["measured"] == 2


def test_main_sends_one_notice_below_the_level_and_records_it(tmp_path, monkeypatch, capsys):
    runs = two_scans(tmp_path, {"s1", "s2", "s3"})
    sent = []
    monkeypatch.setattr(coverage_check.notify, "send", lambda config, message, **kw: sent.append(message))
    env = dict(MAIL, COVERAGE_MIN="8", COVERAGE_RUN_URL="https://github.com/o/r/actions/runs/5",
               COVERAGE_RECORD_DIR=str(tmp_path / "autonomy"))
    assert coverage_check.main(env=env, runs_dir=str(runs)) == 0 and len(sent) == 1
    assert sent[0]["Subject"].startswith("[Tracker Watch] LOW COVERAGE: 7 of 10 sites measured on 2026-11-16 (minimum 8)")
    body = sent[0].get_content()
    assert "s1, s2, s3" in body and "mail-pass-789" not in body and "actions/runs/5" in body
    assert autonomy.load_notes(tmp_path / "autonomy")["2026-11-16"][0]["kind"] == "coverage"


def test_main_is_silent_when_the_level_is_met_and_defaults_to_70_percent_of_the_list(tmp_path, monkeypatch):
    runs = two_scans(tmp_path, {"s1"})
    sent = []
    monkeypatch.setattr(coverage_check.notify, "send", lambda config, message, **kw: sent.append(message))
    assert coverage_check.main(env=dict(MAIL, COVERAGE_MIN="9"), runs_dir=str(runs)) == 0 and not sent
    assert coverage_check.main(env=dict(MAIL), runs_dir=str(runs)) == 0 and not sent   # 9 of 10 is above the default (70 % = 7)
    bad = two_scans(tmp_path / "b", {"s1", "s2", "s3", "s4"})   # 6 of 10 is below it
    assert coverage_check.main(env=dict(MAIL), runs_dir=str(bad)) == 0 and len(sent) == 1
    assert coverage_check.main(env=dict(MAIL, COVERAGE_MIN="x"), runs_dir=str(runs)) == 2


def test_without_mail_settings_it_says_so_and_never_raises(tmp_path, capsys):
    runs = two_scans(tmp_path, {"s1", "s2"})
    assert coverage_check.main(env={"COVERAGE_MIN": "9"}, runs_dir=str(runs)) == 1
    assert "not configured" in capsys.readouterr().err
