import json

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
    assert len(posts) == 3
    assert all(len(p) <= LIMITS[platform] for p in posts)
    assert not any("@" in p for p in posts)


def test_headline_counts_measured_sites_and_names_the_vantage():
    headline = build_thread("2026-10-11", REPORTS, [], platform="bluesky", report_url="u")[0]
    assert "4 of 6 sites measured" in headline and "GitHub servers in the US" in headline
    assert "News A 9" in headline


def test_headline_shrinks_to_fit_the_limit():
    many = [report(f"A very long outlet name number {i}", 10 - i) for i in range(5)]
    headline = build_thread("2026-10-11", many, [], platform="bluesky", report_url="u")[0]
    assert len(headline) <= LIMITS["bluesky"]


def test_changes_post_covers_first_week_stable_changes_and_no_changes():
    first = build_thread("d", REPORTS, [], platform="bluesky", report_url="u")[1]
    assert "first measurement" in first
    changed = build_thread("d", REPORTS, [DIFF_NOTABLE], platform="bluesky", report_url="u")[1]
    assert "News A +ads.example" in changed
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
    assert saved["status"] == "draft" and len(saved["posts"]) == 3 and saved["image"] is None
    assert not (drafts / "2026-10-11" / "chart.png").exists()
    assert "NOT PUBLISHED" in capsys.readouterr().out


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
