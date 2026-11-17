import json
import re

import pytest

from traceguard import posts as posts_module
from traceguard.posts import (LIMITS, alt_text, build_thread, group_medians, load_groups, load_latest_reports,
                              load_spain, main, ranking, render_markdown)

DIFF_NOTABLE = {"site": "News A", "comparable": True, "notable": True,
                "added_tracking_services": [{"service": "ads.example"}],
                "removed_tracking_services": []}


def report(name, tracking, requests=10, *, status="ok", confidence="high", vantage="github-actions-us"):
    summary = {"status": status, "confidence": confidence}
    if status == "ok":
        summary["metrics"] = {"tracking_services": tracking, "third_party_requests": requests}
    return {"site": {"name": name, "url": f"https://{name.lower().replace(' ', '')}.example/"},
            "measurement": {"vantage": vantage, "observe_seconds": 12.0, "passes": 3}, "summary": summary}


REPORTS = [report("News A", 9), report("News B", 7), report("News C", 7, requests=50), report("News D", 0),
           report("News E", 4, confidence="low"), report("News F", 0, status="blocked")]


def test_ranking_excludes_low_confidence_failed_and_zero_sites_and_sorts_by_count_then_requests():
    assert [r["name"] for r in ranking(REPORTS)] == ["News A", "News C", "News B"]


@pytest.mark.parametrize("platform", ["bluesky", "mastodon"])
def test_threads_fit_the_platform_limit_without_mentions(platform):
    posts = build_thread("2026-10-11", REPORTS, [DIFF_NOTABLE], platform=platform,
                         report_url="https://example.org/report/2026-10-11")
    assert len(posts) == 4   # headline, changes, method and the post that points to the extension
    assert all(len(p) <= LIMITS[platform] for p in posts)
    assert not any("@" in p for p in posts)


def test_headline_counts_measured_sites_and_names_the_vantage():
    headline = build_thread("2026-10-11", REPORTS, [], platform="bluesky", report_url="u")[0]
    assert "5 of 6 sites measured" in headline and "GitHub servers in the US" in headline
    assert "News A 9" in headline


def test_headline_shrinks_to_fit_the_limit():
    many = [report(f"A very long outlet name number {i}", 10 - i) for i in range(5)]
    headline = build_thread("2026-10-11", many, [], platform="bluesky", report_url="u")[0]
    assert len(headline) <= LIMITS["bluesky"]


def test_changes_post_covers_first_week_stable_changes_and_no_changes():
    first = build_thread("d", REPORTS, [], platform="bluesky", report_url="u")[1]
    assert "first measurement" in first
    changed = build_thread("d", REPORTS, [DIFF_NOTABLE], platform="bluesky", report_url="u")[1]
    assert "News A: added ads.example" in changed and "u/changes.html" in changed
    quiet = dict(DIFF_NOTABLE, notable=False)
    assert "no stable change" in build_thread("d", REPORTS, [quiet], platform="bluesky", report_url="u")[1]
    not_comparable = {"site": "x", "comparable": False, "reasons": ["different vantage"]}
    assert "not comparable" in build_thread("d", REPORTS, [not_comparable], platform="bluesky", report_url="u")[1]


def test_posts_state_counts_not_verdicts():
    text = " ".join(build_thread("d", REPORTS, [DIFF_NOTABLE], platform="mastodon", report_url="u")).lower()
    assert "not legal verdicts" in text
    for word in ("illegal", "spying", "violat", "keylogger"):
        assert word not in text


def test_alt_text_lists_the_ranking():
    alt = alt_text(REPORTS)
    assert "News A: 9" in alt and "News D" not in alt


def test_cli_dry_run_writes_nothing_and_normal_run_writes_a_draft(tmp_path, capsys):
    folder = tmp_path / "runs" / "2026-10-11"
    folder.mkdir(parents=True)
    for r in REPORTS:
        (folder / f"{r['site']['name'].replace(' ', '-')}.json").write_text(json.dumps(r), encoding="utf-8")
    drafts = tmp_path / "drafts"
    isolated = ["--sites-file", str(tmp_path / "none.json"), "--spain-dir", str(tmp_path / "none")]  # not the real data
    assert main([str(tmp_path / "runs"), "--out", str(drafts), "--dry-run", *isolated]) == 0
    assert not drafts.exists()
    assert main([str(tmp_path / "runs"), "--out", str(drafts), "--no-image", *isolated]) == 0
    saved = json.loads((drafts / "2026-10-11" / "bluesky.json").read_text(encoding="utf-8"))
    assert saved["status"] == "draft" and len(saved["posts"]) == 4 and saved["image"] is None
    assert not (drafts / "2026-10-11" / "chart.png").exists()
    assert "NOT PUBLISHED" in capsys.readouterr().out


def test_cli_refuses_to_draft_when_too_few_sites_were_measured_well(tmp_path):
    folder = tmp_path / "runs" / "2026-10-11"
    folder.mkdir(parents=True)
    for r in REPORTS:  # 4 of 6 are usable
        (folder / f"{r['site']['name'].replace(' ', '-')}.json").write_text(json.dumps(r), encoding="utf-8")
    isolated = ["--sites-file", str(tmp_path / "none.json"), "--spain-dir", str(tmp_path / "none"),
                "--out", str(tmp_path / "drafts"), "--no-image"]
    assert main([str(tmp_path / "runs"), "--min-measured", "0.9", *isolated]) == 2
    assert not (tmp_path / "drafts").exists()
    assert main([str(tmp_path / "runs"), "--min-measured", "0.5", *isolated]) == 0


def test_load_latest_reports_picks_newest_folder(tmp_path):
    for day in ("2026-10-04", "2026-10-11"):
        (tmp_path / day).mkdir()
        (tmp_path / day / "a.json").write_text(json.dumps(report(day, 1)), encoding="utf-8")
    date, reports = load_latest_reports(tmp_path)
    assert date == "2026-10-11" and reports[0]["site"]["name"] == "2026-10-11"


GROUPS = {"https://newsa.example": "US", "https://newsb.example": "US", "https://newsc.example": "UK",
          "https://newsd.example": "UK"}


def test_country_line_gives_the_median_and_the_sites_measured_per_group():
    assert group_medians(REPORTS, GROUPS) == [("UK", 4, 2), ("US", 8, 2)]  # (7 + 0) / 2 rounds to 4
    posts = build_thread("d", REPORTS, [], platform="bluesky", report_url="u", groups=GROUPS)
    assert len(posts) == 4 and posts[1].startswith("By country") and "US 8 (2)" in posts[1]
    assert "consent banner only to European visitors" in posts[1] and "GitHub servers in the US" in posts[1]
    one_group = {k: "US" for k in GROUPS}  # a single group: no comparison line
    assert len(build_thread("d", REPORTS, [], platform="bluesky", report_url="u", groups=one_group)) == 3


def test_spain_block_is_its_own_post_with_its_own_origin_and_fits_the_limit():
    es = [report(f"Diario Largo Numero {i}", 60 - i, vantage="local-windows-spain") for i in range(12)]
    es.append(report("Blocked Daily", 0, status="blocked", vantage="local-windows-spain"))
    for platform in ("bluesky", "mastodon"):
        posts = build_thread("2026-10-11", REPORTS, [], platform=platform, report_url="u",
                             groups=GROUPS, spain=("2026-10-10", es))
        spanish = [p for p in posts if p.startswith("Spanish outlets")]
        assert len(spanish) == 1 and "measured from a PC in Spain (2026-10-10)" in spanish[0]
        assert "12 of 13 sites measured" in spanish[0] and "Diario Largo Numero 0 60" in spanish[0]
        assert all(len(p) <= LIMITS[platform] for p in posts) and not any("@" in p for p in posts)
    assert len(build_thread("d", REPORTS, [], platform="bluesky", report_url="u", spain=("d", []))) == 3


def test_load_spain_keeps_only_spanish_sites_and_ignores_old_or_missing_folders(tmp_path):
    groups = {"https://es1.example": "ES", "https://us1.example": "US"}
    day = tmp_path / "2026-10-09"
    day.mkdir()
    for name, url in (("es1", "https://es1.example/"), ("us1", "https://us1.example/")):
        rep = report(name, 5, vantage="local-windows-spain")
        rep["site"]["url"] = url
        (day / f"{name}.json").write_text(json.dumps(rep), encoding="utf-8")
    date, reports = load_spain(tmp_path, groups, "2026-10-11")
    assert date == "2026-10-09" and [r["site"]["name"] for r in reports] == ["es1"]
    assert load_spain(tmp_path, groups, "2026-10-30") is None  # too old
    assert load_spain(tmp_path / "missing", groups, "2026-10-11") is None


def test_load_groups_reads_the_site_list(tmp_path):
    f = tmp_path / "sites.json"
    f.write_text(json.dumps({"sites": [{"url": "https://a.example/", "group": "ES"}, {"url": "https://b.example"}]}),
                 encoding="utf-8")
    assert load_groups(f) == {"https://a.example": "ES"} and load_groups(tmp_path / "none.json") == {}


def test_the_chart_is_attached_to_post_one_with_its_alt_text(tmp_path, monkeypatch):
    folder = tmp_path / "runs" / "2026-10-11"
    folder.mkdir(parents=True)
    for r in REPORTS:
        (folder / f"{r['site']['name'].replace(' ', '-')}.json").write_text(json.dumps(r), encoding="utf-8")

    def fake_chart(runs_dir, sites_file, path):  # the real one draws a PNG with Chromium
        path.write_bytes(b"png")
        return "Bar chart. News A: 9."
    monkeypatch.setattr(posts_module, "render_chart", fake_chart)
    drafts = tmp_path / "drafts"
    isolated = ["--sites-file", str(tmp_path / "none.json"), "--spain-dir", str(tmp_path / "none")]
    assert main([str(tmp_path / "runs"), "--out", str(drafts), *isolated]) == 0
    day = drafts / "2026-10-11"
    saved = json.loads((day / "bluesky.json").read_text(encoding="utf-8"))
    assert saved["image"] == "chart.png" and saved["alt_text"] == "Bar chart. News A: 9." and (day / "chart.png").exists()
    text = (day / "bluesky.md").read_text(encoding="utf-8")
    assert text.index("[Image attached to this post: chart.png]") < text.index("## Post 2")
    assert "attached to post 1" in text
    assert main([str(tmp_path / "runs"), "--out", str(tmp_path / "d2"), "--dry-run", *isolated]) == 0
    assert not (tmp_path / "d2").exists()  # a dry run draws nothing


def test_markdown_says_when_no_image_was_drawn():
    text = render_markdown("d", "bluesky", ["one", "two"], "alt")
    assert "no image was drawn" in text and "Image attached" not in text


def _with_services(r, *companies):
    r["summary"]["services"] = [{"service": f"{c.lower()}.example", "entity": c, "tracking": True, "stable": True}
                                for c in companies]
    return r


def test_midweek_post_ranks_companies_by_sites_reached_and_names_no_outlet():
    reports = [_with_services(report("News A", 2), "Alpha", "Beta"), _with_services(report("News B", 1), "Alpha"),
               _with_services(report("News C", 1), "Alpha", "Gamma"), report("News F", 0, status="blocked")]
    assert posts_module.company_reach(reports) == [("Alpha", 3), ("Beta", 1), ("Gamma", 1)]
    (post,) = posts_module.build_midweek("2026-10-11", reports, platform="bluesky", report_url="https://x.test/")
    assert "Alpha 3 of 3" in post and "News A" not in post and "@" not in post and len(post) <= LIMITS["bluesky"]


def test_midweek_says_how_each_company_moved_since_the_previous_scan_and_reads_well():
    now = [_with_services(report("News A", 2), "Alpha", "Beta"), _with_services(report("News B", 1), "Alpha"),
           _with_services(report("News C", 1), "Alpha")]
    before = [_with_services(report("News A", 2), "Alpha", "Beta"), _with_services(report("News B", 1), "Beta"),
              _with_services(report("News C", 1), "Alpha")]
    (post,) = posts_module.build_midweek("2026-10-11", now, platform="bluesky", report_url="https://x.test/", previous=before)
    assert "Alpha 3 of 3 (+1)" in post and "Beta 1 of 3 (-1)" in post and "vs last scan" in post
    assert "on the most sites" in post and "the most of the" not in post and len(post) <= LIMITS["bluesky"]
    (plain,) = posts_module.build_midweek("2026-10-11", now, platform="bluesky", report_url="https://x.test/")
    assert "vs last scan" not in plain and "(+" not in plain


def test_midweek_keeps_the_label_and_fits_the_limit_with_movement():
    reports = [_with_services(report(f"News {i}", 1), "Google Advertising", "Amazon Technologies", "Comscore Audience") for i in range(30)]
    (post,) = posts_module.build_midweek("2026-10-14", reports, platform="bluesky", report_url="https://joseasenjo.github.io/tracker-watch/",
                                         previous=reports[:25])
    assert len(post) <= LIMITS["bluesky"] and "Automated post." in post


def test_previous_reports_reads_the_scan_before_the_given_date(tmp_path):
    for day, name in (("2026-10-04", "old"), ("2026-10-11", "new")):
        (tmp_path / day).mkdir()
        (tmp_path / day / "a.json").write_text(json.dumps(report(name, 1)), encoding="utf-8")
    assert [r["site"]["name"] for r in posts_module.previous_reports(tmp_path, "2026-10-11")] == ["old"]
    assert posts_module.previous_reports(tmp_path, "2026-10-04") == []


def test_hook_reads_well():
    (post,) = posts_module.build_hook("2026-10-11", [report("News A", 9), report("News B", 4)], platform="bluesky", report_url="https://x.test/")
    assert "the most among the 2 news sites" in post


def test_midweek_cli_writes_its_own_folder(tmp_path):
    folder = tmp_path / "runs" / "2026-10-11"
    folder.mkdir(parents=True)
    (folder / "a.json").write_text(json.dumps(_with_services(report("News A", 1), "Alpha")), encoding="utf-8")
    assert main([str(tmp_path / "runs"), "--kind", "midweek", "--out", str(tmp_path / "d")]) == 0
    saved = json.loads((tmp_path / "d" / "2026-10-11-midweek" / "bluesky.json").read_text(encoding="utf-8"))
    assert saved["status"] == "draft" and len(saved["posts"]) == 1 and saved["image"] is None


def test_root_post_says_it_is_automated_and_carries_hashtags():
    root = build_thread("2026-10-11", REPORTS, [], platform="bluesky", report_url="u")[0]
    assert root.endswith("Automated post. #privacy #adtech #GDPR") and len(root) <= LIMITS["bluesky"]
    many = [report(f"A very long outlet name number {i}", 10 - i) for i in range(5)]
    long_root = build_thread("2026-10-11", many, [], platform="bluesky", report_url="u")[0]
    assert "Automated post." in long_root and len(long_root) <= LIMITS["bluesky"]


def test_changes_post_lists_added_and_dropped_per_site_with_a_link_and_fits():
    many = [dict(DIFF_NOTABLE, site=f"Outlet {i}") for i in range(12)]
    text = build_thread("d", REPORTS, many, platform="bluesky", report_url="https://x.test/")[1]
    assert len(text) <= LIMITS["bluesky"] and "All 12 changes: https://x.test/changes.html" in text


def test_hook_names_only_the_most_tracked_outlet_and_is_a_draft(tmp_path):
    reports = [report("News A", 9), report("News B", 4), report("News C", 0)]
    (post,) = posts_module.build_hook("2026-10-11", reports, platform="bluesky", report_url="https://x.test/")
    assert post.startswith("News A: 9 third-party tracking services") and "News B" not in post
    assert len(post) <= LIMITS["bluesky"] and "@" not in post and "Automated post." in post
    (streaky,) = posts_module.build_hook("2026-10-11", reports, platform="bluesky", report_url="https://x.test/", streak=4)
    assert "4 scans in a row" in streaky and len(streaky) <= LIMITS["bluesky"] and "Automated post." in streaky


def test_midweek_keeps_automated_post_and_drops_a_company_before_dropping_it():
    reports = [_with_services(report(f"News {i}", 1), "Google Advertising", "Amazon Technologies", "Comscore Audience") for i in range(30)]
    (post,) = posts_module.build_midweek("2026-10-14", reports, platform="bluesky", report_url="https://joseasenjo.github.io/tracker-watch/")
    assert "Automated post." in post and len(post) <= LIMITS["bluesky"]
    short = [_with_services(report("News A", 1), "Alpha")]
    (small,) = posts_module.build_midweek("2026-10-14", short, platform="bluesky", report_url="u")
    assert small.endswith("Automated post. #privacy #adtech #GDPR")


def test_max_chars_keeps_mastodon_posts_inside_the_bluesky_limit(tmp_path, monkeypatch):
    monkeypatch.setitem(posts_module.LIMITS, "mastodon", 500)  # restored after the test: main() lowers it at run time
    runs = _race(tmp_path, ["a"] * 4)   # a steady leader on its 4th scan in a row: the hook is drafted
    none = str(tmp_path / "none.json")
    assert main([str(runs), "--platform", "mastodon", "--kind", "hook", "--max-chars", "300",
                 "--out", str(tmp_path / "d"), "--sites-file", none]) == 0
    saved = json.loads((tmp_path / "d" / "2026-10-26-hook" / "mastodon.json").read_text(encoding="utf-8"))
    assert saved["platform"] == "mastodon" and all(len(p) <= 300 for p in saved["posts"])
    assert posts_module.LIMITS["mastodon"] == 500   # the lowered limit lasts for one run only
    # asking for more than the platform allows never raises its limit
    main([str(runs), "--platform", "mastodon", "--kind", "hook", "--max-chars", "9999", "--out", str(tmp_path / "d2"), "--sites-file", none])
    assert posts_module.LIMITS["mastodon"] == 500


def test_the_scheduled_workflows_publish_on_mastodon_after_bluesky_and_never_fail_bluesky_for_it():
    import yaml
    from pathlib import Path
    for name in ("weekly-scan", "post-midweek"):
        text = Path(f".github/workflows/{name}.yml").read_text(encoding="utf-8")
        steps = [s for job in yaml.safe_load(text)["jobs"].values() for s in job["steps"]]
        names = [s.get("name", "") for s in steps]
        bluesky = next(i for i, n in enumerate(names) if n.startswith("Publish on Bluesky"))
        mastodon = next(i for i, n in enumerate(names) if n.startswith("Publish on Mastodon"))
        assert mastodon > bluesky
        step = steps[mastodon]
        assert step["continue-on-error"] is True and "MASTODON_ENABLED == 'true'" in step["if"] and "POST_REVIEW != 'true'" in step["if"]
        assert "--platform mastodon --send" in step["run"] and step["env"]["MASTODON_TOKEN"] == "${{ secrets.MASTODON_TOKEN }}"
        assert "--max-chars 300" in text  # the Mastodon drafts are written inside Bluesky's limit


def test_a_change_line_says_how_many_more_services_changed_than_the_two_it_names():
    from traceguard.posts import _change_line
    few = {"site": "A", "added_tracking_services": [{"service": "x.com"}], "removed_tracking_services": [{"service": "a.com"}, {"service": "b.com"}]}
    assert _change_line(few) == "A: added x.com; dropped a.com, b.com"
    many = {"site": "BBC News", "added_tracking_services": [], "removed_tracking_services": [{"service": f"s{i}.com"} for i in range(52)]}
    assert _change_line(many) == "BBC News: dropped s0.com, s1.com (+50 more)"


def test_the_monthly_post_compares_months_fits_the_limit_and_says_when_it_is_the_first(tmp_path):
    from traceguard.posts import build_monthly
    first = {"change": None, "monthly": [{"month": "2026-10", "scans": 4, "median_tracking": 7.8, "concentration_index": 117,
                                           "leaders": [{"company": "Google", "mean_pct_of_sites": 68.8}]}]}
    [text] = build_monthly(first, platform="bluesky", report_url="https://site.example/")
    assert len(text) <= 300 and "first month" in text and "Google reaches 68.8% of the sites" in text and "Automated post." in text
    second = {"change": {"median_tracking": 9.5, "delta_median": 1.7, "previous": "2026-10"},
              "monthly": [first["monthly"][0], {**first["monthly"][0], "month": "2026-11", "median_tracking": 9.5}]}
    [text] = build_monthly(second, platform="bluesky", report_url="https://site.example/")
    assert "9.5 (+1.7 on 2026-10)" in text and "first month" not in text and "https://site.example/trends.html" in text
    import pytest
    with pytest.raises(ValueError):
        build_monthly({"change": None, "monthly": []}, platform="bluesky", report_url="https://site.example/")


def test_the_monthly_post_talks_about_the_last_complete_month_and_compares_it_with_the_one_before():
    from datetime import date
    from traceguard.posts import build_monthly, previous_month
    assert previous_month(date(2026, 11, 3)) == "2026-10" and previous_month(date(2027, 1, 3)) == "2026-12"
    row = lambda m, v: {"month": m, "scans": 4, "median_tracking": v, "concentration_index": 117,  # noqa: E731
                        "leaders": [{"company": "Google", "mean_pct_of_sites": 68.0}]}
    summary = {"change": None, "monthly": [row("2026-09", 8.0), row("2026-10", 9.0), row("2026-11", 99.0)]}  # November is only starting
    [text] = build_monthly(summary, platform="bluesky", report_url="https://site.example/", month="2026-10")
    assert "2026-10: median tracking services per site 9 (+1.0 on 2026-09)" in text and "99" not in text
    [text] = build_monthly(summary, platform="bluesky", report_url="https://site.example/", month="2026-07")  # no data: the newest month
    assert "2026-11" in text




def _scans(tmp_path, counts, confidences=None):
    """Weekly scans of one site, 'https://a/', with the given tracking counts (and optional confidence of each scan)."""
    from datetime import date, timedelta
    from tests.test_trends import report as scan_report, write as scan_write
    for i, n in enumerate(counts):
        scan_write(tmp_path, (date(2026, 10, 5) + timedelta(days=7 * i)).isoformat(),
                   [scan_report("https://a/", n, ["X"], confidence=(confidences or ["high"] * len(counts))[i])])
    return tmp_path / "runs"


def test_a_jump_is_announced_only_once_the_next_scan_has_confirmed_it(tmp_path):
    runs = _scans(tmp_path, [10, 11, 12, 60, 61])
    swings, why = posts_module.confirmed_swings(runs, tmp_path / "none.json")
    assert [(s["site"], s["previous"], s["current"], s["held"]) for s in swings] == [("https://a/", 12, 60, 61)]
    assert swings[0]["previous_at"] == "2026-10-19" and swings[0]["current_at"] == "2026-10-26" and swings[0]["held_at"] == "2026-11-02"
    (post,) = posts_module.build_alert(swings, platform="bluesky", report_url="https://x.test/")
    assert "from 12 (2026-10-19) to 60 (2026-10-26), still 61 on 2026-11-02" in post
    assert len(post) <= LIMITS["bluesky"] and "consent banner" not in post and "@" not in post


def test_a_jump_seen_in_one_scan_only_is_not_announced(tmp_path):
    runs = _scans(tmp_path, [10, 11, 12, 60])   # the jump is in the newest scan: nothing can confirm it yet
    assert posts_module.confirmed_swings(runs, tmp_path / "none.json") == ([], [])
    reverted = _scans(tmp_path / "b", [10, 11, 12, 60, 11])
    swings, why = posts_module.confirmed_swings(reverted, tmp_path / "none.json")
    assert swings == [] and "not confirmed" in why[0]


def test_a_confirmed_jump_needs_three_full_readings(tmp_path):
    runs = _scans(tmp_path, [10, 11, 12, 60, 61], confidences=["high", "high", "high", "medium", "high"])   # 2 of 3 passes in the jump scan
    swings, why = posts_module.confirmed_swings(runs, tmp_path / "none.json")
    assert swings == [] and "not every reading was a full one" in why[0]


def test_a_site_that_already_swings_from_week_to_week_makes_no_alert_by_swinging_again(tmp_path):
    runs = _scans(tmp_path, [52, 3, 55, 2, 73, 60, 2, 2])   # the pattern of a site that serves a different page each time
    swings, why = posts_module.confirmed_swings(runs, tmp_path / "none.json")
    assert swings == [] and "already swung" in why[0]


def test_a_site_with_too_little_history_is_not_judged(tmp_path):
    swings, why = posts_module.confirmed_swings(_scans(tmp_path, [57, 17, 17]), tmp_path / "none.json")
    assert swings == [] and "too few earlier scans" in why[0]
    assert posts_module.confirmed_swings(_scans(tmp_path / "b", [10, 60]), tmp_path / "none.json") == ([], [])


def test_alert_cli_writes_its_own_folder_only_for_a_confirmed_jump(tmp_path):
    quiet = _scans(tmp_path / "q", [10, 11, 12, 60])
    assert main([str(quiet), "--kind", "alert", "--out", str(tmp_path / "d1"), "--sites-file", str(tmp_path / "none.json")]) == 0
    assert not (tmp_path / "d1").exists()
    loud = _scans(tmp_path / "l", [10, 11, 12, 60, 61])
    assert main([str(loud), "--kind", "alert", "--out", str(tmp_path / "d2"), "--sites-file", str(tmp_path / "none.json")]) == 0
    saved = json.loads((tmp_path / "d2" / "2026-11-02-alert" / "bluesky.json").read_text(encoding="utf-8"))
    assert saved["status"] == "draft" and "from 12" in saved["posts"][0]


def _race(tmp_path, leaders, confidences=None):
    """Weekly scans of two sites: 'https://a/' (51 services) and 'https://b/' (40); the one named in `leaders` leads each scan."""
    from datetime import date, timedelta
    from tests.test_trends import report as scan_report, write as scan_write
    for i, who in enumerate(leaders):
        conf = (confidences or ["high"] * len(leaders))[i]
        counts = {"https://a/": 51, "https://b/": 40} if who == "a" else {"https://a/": 40, "https://b/": 51}
        scan_write(tmp_path, (date(2026, 10, 5) + timedelta(days=7 * i)).isoformat(),
                   [scan_report(url, n, ["X"], confidence=conf) for url, n in counts.items()])
    return tmp_path / "runs"


def test_a_steady_leader_gets_a_hook_only_on_every_fourth_scan_in_a_row(tmp_path):
    none = tmp_path / "none.json"
    leader, why = posts_module.stable_leader(_race(tmp_path / "x", ["a", "a", "a", "a"]), none)
    assert leader == {"name": "https://a/", "tracking": 51, "streak": 4} and why == []
    for n in (3, 5):
        leader, why = posts_module.stable_leader(_race(tmp_path / f"n{n}", ["a"] * n), none)
        assert leader is None and "in a row" in why[0]
    leader, why = posts_module.stable_leader(_race(tmp_path / "short", ["a", "a"]), none)
    assert leader is None and "not been steady" in why[0]   # too few scans to call anything steady


def test_a_leader_that_changed_recently_has_a_short_streak(tmp_path):
    leader, why = posts_module.stable_leader(_race(tmp_path, ["b", "b", "a", "a"]), tmp_path / "none.json")
    assert leader is None and "led 2 scans in a row" in why[0]


def test_no_hook_for_a_leader_that_is_not_steady_or_not_fully_measured(tmp_path):
    from datetime import date, timedelta
    from tests.test_trends import report as scan_report, write as scan_write
    for i, n in enumerate([90, 20, 95, 15]):   # always the top of the list, but it swings week to week
        scan_write(tmp_path / "w", (date(2026, 10, 5) + timedelta(days=7 * i)).isoformat(), [scan_report("https://a/", n, ["X"])])
    leader, why = posts_module.stable_leader(tmp_path / "w" / "runs", tmp_path / "none.json")
    assert leader is None and "not been steady" in why[0]
    medium = _race(tmp_path / "m", ["a"] * 4, confidences=["high", "high", "high", "medium"])
    leader, why = posts_module.stable_leader(medium, tmp_path / "none.json")
    assert leader is None and "not a full one" in why[0]


def test_hook_cli_writes_a_draft_only_for_a_qualifying_leader(tmp_path):
    none = str(tmp_path / "none.json")
    assert main([str(_race(tmp_path / "s", ["a", "a", "a"])), "--kind", "hook", "--out", str(tmp_path / "d1"), "--sites-file", none]) == 0
    assert not (tmp_path / "d1").exists()
    assert main([str(_race(tmp_path / "l", ["a"] * 4)), "--kind", "hook", "--out", str(tmp_path / "d2"), "--sites-file", none]) == 0
    saved = json.loads((tmp_path / "d2" / "2026-10-26-hook" / "bluesky.json").read_text(encoding="utf-8"))
    assert saved["status"] == "draft" and saved["image"] is None and "4 scans in a row" in saved["posts"][0]


def _scans_with_requests(tmp_path, counts, requests, confidences=None):
    from datetime import date, timedelta
    from tests.test_trends import report as scan_report, write as scan_write
    for i, n in enumerate(counts):
        r = scan_report("https://a/", n, ["X"], confidence=(confidences or ["high"] * len(counts))[i])
        r["summary"]["metrics"]["third_party_requests"] = requests[i]
        scan_write(tmp_path, (date(2026, 10, 5) + timedelta(days=7 * i)).isoformat(), [r])
    return tmp_path / "runs"


def _change_diff(name="https://a/"):
    return [{"site": name, "comparable": True, "notable": True}]


def test_a_small_change_on_a_steady_full_reading_stays_in_the_weekly_changes(tmp_path):
    runs = _scans_with_requests(tmp_path, [50, 51, 50, 52], [100] * 4)
    kept, why = posts_module.vetted_changes(_change_diff(), runs, tmp_path / "none.json")
    assert kept[0]["notable"] is True and why == []


def test_a_change_with_a_reading_that_was_not_full_is_left_out_of_the_public_list(tmp_path):
    runs = _scans_with_requests(tmp_path, [50, 51, 50, 52], [100] * 4, confidences=["high", "high", "high", "medium"])
    kept, why = posts_module.vetted_changes(_change_diff(), runs, tmp_path / "none.json")
    assert kept[0]["notable"] is False and "not every reading was a full one" in why[0] and kept[0]["left_out"]


def test_a_big_move_is_left_out_when_the_requests_moved_with_it_or_the_site_was_never_steady(tmp_path):
    with_requests = _scans_with_requests(tmp_path / "a", [50, 51, 50, 12], [400, 410, 400, 100])
    kept, why = posts_module.vetted_changes(_change_diff(), with_requests, tmp_path / "none.json")
    assert kept[0]["notable"] is False and "third-party requests moved" in why[0]
    swinging = _scans_with_requests(tmp_path / "b", [90, 20, 95, 15], [100] * 4)
    kept, why = posts_module.vetted_changes(_change_diff(), swinging, tmp_path / "none.json")
    assert kept[0]["notable"] is False and "without a steady record" in why[0]


def test_a_big_move_with_steady_record_and_unchanged_requests_is_kept(tmp_path):
    runs = _scans_with_requests(tmp_path, [50, 51, 50, 12], [100] * 4)
    kept, why = posts_module.vetted_changes(_change_diff(), runs, tmp_path / "none.json")
    assert kept[0]["notable"] is True and why == []


def test_changes_that_are_not_notable_pass_through_untouched(tmp_path):
    runs = _scans_with_requests(tmp_path, [50, 51, 50, 52], [100] * 4)
    diffs = [{"site": "https://a/", "comparable": True, "notable": False}, {"site": "x", "comparable": False, "reasons": ["y"]}]
    assert posts_module.vetted_changes(diffs, runs, tmp_path / "none.json") == (diffs, [])


def test_the_weekly_thread_ends_with_the_post_that_points_to_the_extension():
    thread = build_thread("d", REPORTS, [], platform="bluesky", report_url="https://x.test/tw/")
    assert thread[-1].endswith("https://x.test/tw/extension.html") and "free browser extension" in thread[-1]
    assert all(len(p) <= LIMITS["bluesky"] for p in thread)
    assert "extension" not in build_thread("d", REPORTS, [], platform="bluesky", report_url="")[-1].lower()   # no address: no post


def test_every_explainer_fits_both_networks_names_no_outlet_and_links_only_to_the_site(monkeypatch):
    monkeypatch.setitem(posts_module.LIMITS, "mastodon", 300)   # the workflows write Mastodon posts within 300 characters
    entries = posts_module.load_explainers("data/explainers.json")
    assert len(entries) >= 6 and len({e["id"] for e in entries}) == len(entries)
    outlets = ("Independent", "BBC", "Mirror", "New York", "Guardian", "Times", "El Pa", "Zeit", "AS ")
    for e in entries:
        for platform, cap in (("bluesky", 300), ("mastodon", 300)):
            (text,) = posts_module.build_explainer(e, platform=platform, report_url="https://joseasenjo.github.io/tracker-watch/")
            assert len(text) <= cap and "{site}" not in text and "@" not in text and "Automated post." in text
            assert not any(o in text for o in outlets), e["id"]
            assert all(u.startswith("https://joseasenjo.github.io/tracker-watch/") for u in re.findall(r"https?://\S+", text))


def test_one_explainer_waits_at_a_time_and_the_next_one_follows_once_it_is_published(tmp_path):
    entries = [{"id": "a", "text": "A {site}"}, {"id": "b", "text": "B"}]
    drafts = tmp_path / "drafts"
    assert posts_module.next_explainer(entries, drafts)[0]["id"] == "a"
    old = drafts / "2026-11-02-explainer"
    old.mkdir(parents=True)
    (old / "bluesky.json").write_text(json.dumps({"explainer": "a"}), encoding="utf-8")
    entry, why = posts_module.next_explainer(entries, drafts)
    assert entry is None and "waiting for approval" in why
    (old / "bluesky.posted.json").write_text("{}", encoding="utf-8")
    assert posts_module.next_explainer(entries, drafts)[0]["id"] == "b"
    new = drafts / "2026-11-09-explainer"
    new.mkdir()
    (new / "bluesky.json").write_text(json.dumps({"explainer": "b"}), encoding="utf-8")
    (new / "bluesky.posted.json").write_text("{}", encoding="utf-8")
    assert posts_module.next_explainer(entries, drafts)[0]["id"] == "a"   # all used: evergreen, start again


def test_explainer_cli_drafts_the_same_text_on_both_networks_and_never_a_second_one(tmp_path):
    runs = _scans(tmp_path / "r", [10, 11])
    out, file = tmp_path / "d", tmp_path / "e.json"
    file.write_text(json.dumps({"explainers": [{"id": "one", "text": "One {site}"}, {"id": "two", "text": "Two"}]}), encoding="utf-8")
    common = [str(runs), "--kind", "explainer", "--out", str(out), "--explainers-file", str(file), "--report-url", "https://x.test/"]
    assert main(common + ["--platform", "bluesky"]) == 0
    assert main(common + ["--platform", "mastodon", "--max-chars", "300"]) == 0
    folder = out / "2026-10-12-explainer"
    bluesky, mastodon = (json.loads((folder / f"{p}.json").read_text(encoding="utf-8")) for p in ("bluesky", "mastodon"))
    assert bluesky["explainer"] == mastodon["explainer"] == "one" and bluesky["status"] == "draft"
    assert bluesky["posts"][0].startswith("One https://x.test/")
    assert main(common + ["--platform", "bluesky"]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["2026-10-12-explainer"]   # still the same single draft
