"""Trends and the concentration index: pure functions of the reports, comparable scans only."""
import json

from traceguard import trends


def report(url, tracking, entities, *, confidence="high", status="ok", vantage="github-actions-us", passes=3, observe=12.0):
    return {
        "site": {"name": url, "url": url},
        "measurement": {"vantage": vantage, "passes": passes, "observe_seconds": observe},
        "summary": {"status": status, "confidence": confidence,
                    "metrics": {"tracking_services": tracking, "third_party_requests": 10},
                    "services": [{"entity": e, "tracking": True, "stable": True, "passes_seen": 3, "service": f"{e}.example"}
                                 for e in entities]},
    }


def write(tmp_path, date, reports):
    folder = tmp_path / "runs" / date
    folder.mkdir(parents=True, exist_ok=True)
    for i, r in enumerate(reports):
        (folder / f"s{i}.json").write_text(json.dumps(r), encoding="utf-8")


def test_the_concentration_index_is_ten_thousand_for_one_company_and_low_when_it_is_spread():
    one = [report("https://a/", 1, ["Google"]), report("https://b/", 1, ["Google"])]
    assert trends.concentration(one)["concentration_index"] == 10_000
    spread = [report(f"https://s{i}/", 1, [f"Co{i}"]) for i in range(10)]
    c = trends.concentration(spread)
    assert c["concentration_index"] == 1000 and c["companies"] == 10 and c["top_company_pct"] == 10.0


def test_the_leader_reach_and_the_top_three_are_measured_on_usable_sites_only():
    reports = [report("https://a/", 2, ["Google", "Amazon"]), report("https://b/", 1, ["Google"]),
               report("https://c/", 1, ["Google"], confidence="low"), report("https://d/", 0, [], status="blocked")]
    c = trends.concentration(reports)
    assert c["usable"] == 2 and c["top_company"] == "Google" and c["top_company_sites"] == 2 and c["top_company_pct"] == 100.0
    assert c["reach"] == [("Google", 2), ("Amazon", 1)]


def test_one_scan_becomes_one_point_with_medians_by_group_and_bands():
    groups = {"https://a": "UK", "https://b": "UK", "https://c": "DE"}
    reports = [report("https://a/", 60, ["X"]), report("https://b/", 40, ["X"]), report("https://c/", 1, [])]
    p = trends.point("2026-10-05", reports, groups)
    assert p["median_tracking"] == 40 and p["by_group"] == {"DE": 1, "UK": 50.0}
    assert p["bands"] == {"A": 1, "B": 0, "C": 0, "D": 1, "E": 1} and p["usable"] == 3


def test_only_scans_under_the_same_conditions_are_compared(tmp_path):
    write(tmp_path, "2026-09-28", [report("https://a/", 5, ["X"], vantage="local-windows-spain")])
    write(tmp_path, "2026-10-05", [report("https://a/", 8, ["X"])])
    write(tmp_path, "2026-10-12", [report("https://a/", 10, ["X"])])
    result = trends.summary(tmp_path / "runs")
    assert [p["date"] for p in result["series"]] == ["2026-09-28", "2026-10-05", "2026-10-12"]
    assert [p["date"] for p in result["comparable"]] == ["2026-10-05", "2026-10-12"] and result["excluded"] == 1
    assert [m["month"] for m in result["monthly"]] == ["2026-10"] and result["change"] is None


def test_the_monthly_rows_average_the_weekly_medians_and_the_change_is_month_over_month(tmp_path):
    write(tmp_path, "2026-10-05", [report("https://a/", 10, ["X"])])
    write(tmp_path, "2026-10-12", [report("https://a/", 20, ["X"])])
    write(tmp_path, "2026-11-02", [report("https://a/", 30, ["X"])])
    result = trends.summary(tmp_path / "runs")
    october, november = result["monthly"]
    assert october["scans"] == 2 and october["median_tracking"] == 15.0 and november["median_tracking"] == 30.0
    assert result["change"]["delta_median"] == 15.0 and result["change"]["previous"] == "2026-10"
    assert october["leaders"][0] == {"company": "X", "mean_pct_of_sites": 100.0}


def test_a_scan_with_no_usable_site_is_left_out_and_the_cli_writes_both_files(tmp_path, capsys):
    write(tmp_path, "2026-10-05", [report("https://a/", 0, [], status="blocked")])
    write(tmp_path, "2026-10-12", [report("https://a/", 7, ["X"])])
    assert [p["date"] for p in trends.load_series(tmp_path / "runs")] == ["2026-10-12"]
    out = tmp_path / "out"
    assert trends.main([str(tmp_path / "runs"), "--sites-file", str(tmp_path / "none.json"), "--out", str(out)]) == 0
    assert (out / "series.csv").read_text(encoding="utf-8").splitlines()[0].startswith("date,vantage")
    assert json.loads((out / "monthly.json").read_text(encoding="utf-8"))["scans"] == 1
    (tmp_path / "empty-runs").mkdir()
    assert trends.main([str(tmp_path / "empty-runs"), "--out", str(tmp_path / "x")]) == 2
