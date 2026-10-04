import json
from datetime import datetime, timezone

from traceguard.classify import TrackerList
from traceguard.reclassify import main, reclassify_folder, reclassify_report

from test_report import DOUBLECLICK, make_run, own_run, request

NEW_LIST = {"domains": {"newads.example": {"entity": "NewAds", "category": "advertising"},
                        "stats.g.doubleclick.net": {"entity": "Google", "category": "advertising"}},
            "_meta": {"tracking_categories": ["advertising", "analytics"]}}


def tracker_list(tmp_path, data=NEW_LIST, name="list.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return TrackerList.load(path)


def base_report():
    extra = request("ads.newads.example", "newads.example", "third")
    runs = [own_run(1, [extra]), own_run(2, [extra]), own_run(3, [extra])]
    for run in runs:  # the original list did not know newads.example
        for r in run["requests"]:
            if r["host"] == "stats.g.doubleclick.net":
                r["tracker"] = DOUBLECLICK
    return {"schema_version": "0.1", "tool": {"name": "traceguard", "version": "0.1.0"},
            "generated_at": "2026-10-04T10:00:00+00:00",
            "site": {"name": "News", "url": "https://news.example/", "first_party_domains": ["news.example"]},
            "measurement": {"vantage": "v", "observe_seconds": 12.0, "passes": 3, "tracker_list": "old.json"},
            "summary": {}, "findings": [], "runs": runs}


def test_a_new_list_changes_the_counts_without_touching_the_measurement(tmp_path):
    old = base_report()
    new = reclassify_report(old, tracker_list(tmp_path), now=datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert new["summary"]["metrics"]["tracking_services"] == 2
    assert new["generated_at"] == "2026-10-04T10:00:00+00:00" and new["reclassified_at"].startswith("2026-10-05")
    assert new["measurement"]["tracker_list"] == "list.json"
    assert new["runs"][0]["requests"] == [dict(r, tracker=r["tracker"]) for r in new["runs"][0]["requests"]]
    assert [r["host"] for r in new["runs"][0]["requests"]] == [r["host"] for r in old["runs"][0]["requests"]]
    assert old["runs"][0]["requests"][-1].get("tracker") is None  # the original was not modified


def test_same_list_gives_same_counts(tmp_path):
    only_doubleclick = {"domains": {"stats.g.doubleclick.net": {"entity": "Google", "category": "advertising"}},
                        "_meta": {"tracking_categories": ["advertising"]}}
    new = reclassify_report(base_report(), tracker_list(tmp_path, only_doubleclick))
    assert new["summary"]["metrics"]["tracking_services"] == 1


def test_failed_measurements_are_kept_as_failed(tmp_path):
    report = base_report()
    report["runs"] = [make_run(i, [], status="blocked", http_status=403) for i in (1, 2, 3)]
    new = reclassify_report(report, tracker_list(tmp_path))
    assert new["summary"]["status"] == "blocked" and "metrics" not in new["summary"]


def test_folder_run_reports_before_and_after_and_cli_needs_an_explicit_target(tmp_path, capsys):
    source = tmp_path / "day"
    source.mkdir()
    report = base_report()
    report["summary"] = {"metrics": {"tracking_services": 1}}
    (source / "news.json").write_text(json.dumps(report), encoding="utf-8")
    changes = reclassify_folder(source, tracker_list(tmp_path), tmp_path / "out")
    assert changes == [("News", 1, 2)] and (tmp_path / "out" / "news.json").exists()
    assert json.loads((source / "news.json").read_text(encoding="utf-8"))["summary"]["metrics"]["tracking_services"] == 1
    assert main([str(source), "--tracker-list", str(tmp_path / "list.json"), "--out", str(tmp_path / "out2")]) == 0
    assert "News: 1 -> 2" in capsys.readouterr().out
    try:
        main([str(source)])
    except SystemExit as exc:
        assert exc.code == 2
