"""Shareable image of the weekly ranking (1200x630, the size social networks preview).

The HTML is generated here and turned into a PNG with Playwright, which is already needed for
scanning. Everything is inline: system fonts, no external requests.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

SRC = Path(__file__).resolve().parent.parent / "site_src"
CARD_SIZE = (1200, 630)
MAX_ROWS = 6


def ranking_rows(ctx: dict) -> list[dict]:
    """Top sites by number of tracking services, with bar widths scaled to the largest."""
    top = [e for e in ctx["measured"] if e["tracking"] > 0][:MAX_ROWS]
    largest = max((e["tracking"] for e in top), default=1)
    return [{"name": e["name"], "count": e["tracking"], "band": e["band"],
             "width": max(4, round(100 * e["tracking"] / largest))} for e in top]


def alt_text(ctx: dict) -> str:
    rows = ranking_rows(ctx)
    if not rows:
        return "No measured site contacted a classified tracking service before any interaction."
    listing = "; ".join(f"{r['name']}: {r['count']}" for r in rows)
    return ("Bar chart of the number of third-party tracking services contacted before any interaction, "
            f"measurement of {ctx['date']}. {listing}.")


def ranking_card_html(ctx: dict) -> str:
    env = Environment(loader=FileSystemLoader(SRC / "templates"), autoescape=select_autoescape(["html"]))
    return env.get_template("share_ranking.html").render(rows=ranking_rows(ctx), **ctx)


def render_png(html: str, path: Path) -> bool:
    """Write the card as a PNG. Returns False when Playwright or Chromium is not available."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": CARD_SIZE[0], "height": CARD_SIZE[1]})
                page.set_content(html)
                page.screenshot(path=str(path))
            finally:
                browser.close()
    except Exception:
        return False
    return path.exists()
