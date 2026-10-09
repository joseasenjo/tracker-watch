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
    # same rule as the weekly thread: sites measured with low confidence are not ranked
    top = [e for e in ctx["measured"] if e["tracking"] > 0 and e.get("confidence", "high") in ("high", "medium")][:MAX_ROWS]
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


def origins_rows(ctx: dict) -> list[dict]:
    """Largest gaps between the first two origins, with bar widths on a common scale."""
    origins = ctx["origins"]
    if not origins["has_data"]:
        return []
    first, second = origins["origins"][:2]
    rows = [r for r in origins["rows"] if r["gap"] is not None][:6]
    top = max((max(r["cells"][first]["tracking"], r["cells"][second]["tracking"]) for r in rows), default=1) or 1
    return [{"name": r["name"], "a": r["cells"][first]["tracking"], "b": r["cells"][second]["tracking"],
             "wa": max(2, round(100 * r["cells"][first]["tracking"] / top)),
             "wb": max(2, round(100 * r["cells"][second]["tracking"] / top))} for r in rows]


def origins_alt_text(ctx: dict) -> str:
    rows = origins_rows(ctx)
    if not rows:
        return "No site has been measured from more than one origin yet."
    first, second = ctx["origins"]["origins"][:2]
    listing = "; ".join(f"{r['name']}: {first} {r['a']}, {second} {r['b']}" for r in rows)
    return ("Paired bar chart of the number of third-party tracking services contacted before any interaction, "
            f"measured from {first} and from {second}. {listing}.")


def origins_card_html(ctx: dict) -> str:
    env = Environment(loader=FileSystemLoader(SRC / "templates"), autoescape=select_autoescape(["html"]))
    first, second = ctx["origins"]["origins"][:2]
    return env.get_template("share_origins.html").render(rows=origins_rows(ctx), first=first, second=second,
                                                         compared=ctx["origins"]["compared"], **ctx)


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
