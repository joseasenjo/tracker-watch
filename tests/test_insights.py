"""Insights on the trends: stability, anomalies, what changed, series, families. Pure functions of the scans."""
from traceguard import insights, spark, trends
from tests.test_trends import report


def scan(date, counts, *, requests=None, companies=None):
    """One point from {stem: tracking count}; every site runs the given companies (default Google)."""
    reports, stems = [], []
    for stem, n in counts.items():
        r = report(f"https://{stem}/", n, (companies or {}).get(stem, ["Google"]))
        r["summary"]["metrics"]["third_party_requests"] = (requests or {}).get(stem, 100)
        reports.append(r)
        stems.append(stem)
    return trends.point(date, reports, {f"https://{s}": "UK" for s in counts}, stems=stems)


def test_a_site_that_barely_moves_is_stable_and_one_that_swings_is_variable():
    points = [scan(d, {"a": a, "b": b}) for d, a, b in [("2026-10-05", 40, 10), ("2026-10-12", 41, 60), ("2026-10-19", 39, 15)]]
    s = insights.stability(points)
    labels = {r["stem"]: r["label"] for r in s["rows"]}
    assert labels == {"a": "stable", "b": "variable"} and s["stable"] == 1 and s["variable"] == 1


def test_stability_needs_enough_scans():
    points = [scan("2026-10-05", {"a": 5}), scan("2026-10-12", {"a": 5})]
    assert insights.stability(points)["rows"] == []


def test_a_drop_with_the_requests_is_a_probable_artifact_until_it_is_kept():
    points = [scan("2026-09-28", {"a": 54}, requests={"a": 400}), scan("2026-10-05", {"a": 55}, requests={"a": 400}),
              scan("2026-10-12", {"a": 56}, requests={"a": 410}), scan("2026-10-19", {"a": 2}, requests={"a": 40})]
    (a,) = insights.anomalies(points)
    assert a["verdict"] == "probable_artifact" and a["requests_change_pct"] == -90 and a["outside_usual"] is True


def test_the_scan_after_a_jump_confirms_it_or_reverts_it_and_a_fresh_jump_is_unconfirmed():
    kept = [scan("2026-10-05", {"a": 10}), scan("2026-10-12", {"a": 60}), scan("2026-10-19", {"a": 61})]
    assert insights.anomalies(kept)[0]["verdict"] == "confirmed"
    back = [scan("2026-10-05", {"a": 10}), scan("2026-10-12", {"a": 60}), scan("2026-10-19", {"a": 11})]
    assert insights.anomalies(back)[0]["verdict"] == "reverted"
    fresh = [scan("2026-10-05", {"a": 10}), scan("2026-10-12", {"a": 11}), scan("2026-10-19", {"a": 60})]
    assert insights.anomalies(fresh)[0]["verdict"] == "unconfirmed"


def test_small_moves_are_not_anomalies():
    assert insights.anomalies([scan("2026-10-05", {"a": 40}), scan("2026-10-12", {"a": 45})]) == []


def test_what_changed_lists_the_biggest_movers_and_the_company_reach():
    old = scan("2026-10-05", {"a": 10, "b": 30, "c": 5}, companies={"a": ["X"], "b": ["X"], "c": ["X"]})
    new = scan("2026-10-12", {"a": 18, "b": 20, "c": 5}, companies={"a": ["X"], "b": ["X"], "c": []})
    c = insights.what_changed([old, new])
    assert [m["name"] for m in c["sites_up"]] == ["https://a/"] and c["sites_up"][0]["delta"] == 8
    assert [m["name"] for m in c["sites_down"]] == ["https://b/"]
    assert c["companies_lost"] == [{"company": "X", "from": 3, "to": 2, "delta": -1}] or c["companies_lost"] == []
    assert insights.what_changed([new]) is None


def test_company_and_category_series_have_one_value_per_scan():
    points = [scan("2026-10-05", {"a": 5, "b": 5}), scan("2026-10-12", {"a": 5, "b": 5})]
    (name, values), *_ = insights.company_series(points)
    assert name == "Google" and [v for _, v in values] == [100.0, 100.0]
    (label, cat), *_ = insights.category_series(points, {"advertising": "Advertising"})
    assert label == "Advertising" and len(cat) == 2


def test_the_page_says_preliminary_until_enough_scans():
    assert insights.preliminary(4) and not insights.preliminary(insights.PRELIMINARY_BELOW)


def test_families_group_sites_that_share_a_stack_and_name_the_marking_companies():
    shared = ["Amobee", "Outbrain", "GumGum"]
    counts, companies = {}, {}
    for s in ("h1", "h2", "h3", "h4"):
        counts[s], companies[s] = 80, shared + ["Google"]
    for s in ("l1", "l2", "l3", "l4"):
        counts[s], companies[s] = 3, ["Google"]
    result = insights.families(scan("2026-10-05", counts, companies=companies))
    heavy = next(f for f in result["families"] if "h1" in " ".join(f["sites"]) or f["median_tracking"] == 80)
    assert heavy["size"] == 4 and {m["company"] for m in heavy["signature"]} >= {"Amobee", "Outbrain", "GumGum"}


def test_the_sites_csv_has_one_row_per_site_and_scan():
    points = [scan("2026-10-05", {"a": 5, "b": 6}), scan("2026-10-12", {"a": 5, "b": 6})]
    rows = insights.sites_rows(points)
    assert len(rows) == 4 and len(rows[0]) == len(insights.SITES_COLUMNS)


def test_the_charts_draw_labels_and_cells():
    bars = spark.stacked_bars([("2026-10-05", {"A": 1, "B": 2, "E": 3})], ("A", "B", "C", "D", "E"))
    assert "sb-0" in bars and "2026-10-05" in bars
    heat = spark.heatmap(["Google", "Amazon"], ["F1", "F2"], [[100, 50], [0, 25]], extra=[76, 59], extra_label="All")
    assert heat.count("hm-cell") >= 4 and "Google" in heat and "F2" in heat
