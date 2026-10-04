import json

import pytest

from traceguard.posts import LIMITS, alt_text, build_thread, load_latest_reports, main, ranking

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
    assert main([str(tmp_path / "runs"), "--out", str(drafts), "--dry-run"]) == 0
    assert not drafts.exists()
    assert main([str(tmp_path / "runs"), "--out", str(drafts)]) == 0
    saved = json.loads((drafts / "2026-10-11" / "bluesky.json").read_text(encoding="utf-8"))
    assert saved["status"] == "draft" and len(saved["posts"]) == 3
    assert "NOT PUBLISHED" in capsys.readouterr().out


def test_load_latest_reports_picks_newest_folder(tmp_path):
    for day in ("2026-10-04", "2026-10-11"):
        (tmp_path / day).mkdir()
        (tmp_path / day / "a.json").write_text(json.dumps(report(day, 1)), encoding="utf-8")
    date, reports = load_latest_reports(tmp_path)
    assert date == "2026-10-11" and reports[0]["site"]["name"] == "2026-10-11"
