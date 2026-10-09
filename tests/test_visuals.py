import json
import re

import pytest

from traceguard import site as site_module
from traceguard.cards import alt_text, ranking_card_html, ranking_rows, render_png
from traceguard.site import build_context, build_site
from traceguard.spark import sparkline

from test_site import blocked_report, ok_report, write


def test_sparkline_draws_a_line_for_several_points_and_only_a_dot_for_one():
    several = str(sparkline([("2026-09-27", 3), ("2026-10-04", 5), ("2026-10-11", 4)]))
    assert "<polyline" in several and several.count("<circle") == 3 and 'role="img"' in several
    assert "2026-10-04: 5" in several
    single = str(sparkline([("2026-10-04", 3)]))
    assert "<polyline" not in single and single.count("<circle") == 1
    assert str(sparkline([])) == ""


def test_sparkline_handles_flat_series_and_escapes_text():
    flat = str(sparkline([("a", 2), ("b", 2)]))
    assert "nan" not in flat.lower()
    assert "&lt;script&gt;" in str(sparkline([("<script>", 1)])) and "<script>" not in str(sparkline([("<script>", 1)]))


@pytest.fixture
def weeks(tmp_path):
    write(tmp_path, "2026-09-27", {"a": ok_report(tracking=1, vantage="github-actions-us")})
    write(tmp_path, "2026-10-04", {"a": ok_report(tracking=3, vantage="github-actions-us"), "b": blocked_report()})
    return tmp_path


def test_site_page_shows_the_chart_when_two_comparable_weeks_exist(weeks):
    out = weeks / "site"
    build_site(weeks / "runs", out, base_url="https://example.org/tw/")
    page = (out / "sites" / "a.html").read_text(encoding="utf-8")
    assert 'class="spark"' in page and "Evolution" in page
    assert "Trend" in (out / "index.html").read_text(encoding="utf-8")


def test_chart_ignores_weeks_measured_from_another_place(tmp_path):
    write(tmp_path, "2026-09-27", {"a": ok_report(tracking=1, vantage="local-windows-spain")})
    write(tmp_path, "2026-10-04", {"a": ok_report(tracking=3, vantage="github-actions-us")})
    out = tmp_path / "site"
    ctx = build_site(tmp_path / "runs", out)
    assert ctx["any_trend"] is False
    assert "appears once there are at least two weeks" in (out / "sites" / "a.html").read_text(encoding="utf-8")
    assert "<th>Trend</th>" not in (out / "index.html").read_text(encoding="utf-8")


def test_hero_shows_last_scan_and_where_it_ran_from(weeks):
    out = weeks / "site"
    build_site(weeks / "runs", out)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert re.search(r"Last scan: 2026-10-04 10:00 UTC · from GitHub servers in the US", html)


def test_the_header_has_no_lookup_bar_and_the_script_never_posts_anywhere(weeks):
    out = weeks / "site"
    build_site(weeks / "runs", out, repo_url="https://github.com/x/y")
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'id="check-form"' not in html and "Is your site in the list?" not in html
    js = (out / "assets" / "site.js").read_text(encoding="utf-8")
    assert "fetch(" not in js and "XMLHttpRequest" not in js and "sendBeacon" not in js


def test_open_graph_tags_appear_only_when_the_share_image_was_made(weeks, monkeypatch):
    out = weeks / "plain"
    build_site(weeks / "runs", out)
    assert "og:image" not in (out / "index.html").read_text(encoding="utf-8")

    def fake_render(html, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return True

    monkeypatch.setattr(site_module, "render_png", fake_render)
    out2 = weeks / "cards"
    build_site(weeks / "runs", out2, base_url="https://example.org/tw", share_cards=True)
    html = (out2 / "index.html").read_text(encoding="utf-8")
    assert 'content="https://example.org/tw/share/ranking.png"' in html
    assert "twitter:card" in html and "og:image:alt" in html
    assert (out2 / "share" / "ranking.png").exists()


def test_ranking_card_lists_the_top_sites_with_scaled_bars(weeks):
    ctx = build_context(weeks / "runs")
    rows = ranking_rows(ctx)
    assert rows[0]["name"] == "News A" and rows[0]["width"] == 100
    html = ranking_card_html(ctx)
    assert "News A" in html and "counts, not verdicts" in html
    assert "http://" not in html and "https://" not in html
    assert "News A: 3" in alt_text(ctx)


def test_ranking_card_leaves_out_low_confidence_sites_like_the_weekly_thread():
    ctx = {"measured": [{"name": "Low One", "tracking": 90, "band": "E", "confidence": "low"},
                        {"name": "Solid One", "tracking": 50, "band": "E", "confidence": "high"},
                        {"name": "Mid One", "tracking": 40, "band": "D", "confidence": "medium"}]}
    assert [r["name"] for r in ranking_rows(ctx)] == ["Solid One", "Mid One"]


def test_card_png_can_be_rendered_when_chromium_is_available(weeks, tmp_path):
    ctx = build_context(weeks / "runs")
    path = tmp_path / "card.png"
    if not render_png(ranking_card_html(ctx), path):
        pytest.skip("Chromium not available")
    assert path.read_bytes()[:4] == b"\x89PNG"
