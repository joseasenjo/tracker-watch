"""Tiny inline SVG line chart for the weekly count of tracking services of one site."""
from __future__ import annotations

from markupsafe import Markup, escape


def sparkline(points: list[tuple[str, int]], *, width: int = 260, height: int = 56) -> Markup:
    """points: (date, value) from oldest to newest. Colours come from CSS classes (light and dark)."""
    if not points:
        return Markup("")
    pad = 8
    values = [v for _, v in points]
    low, high = min(values), max(values)
    span = max(high - low, 1)
    count = len(points)
    xs = [pad + (width - 2 * pad) * (i / (count - 1) if count > 1 else 0.5) for i in range(count)]
    ys = [height - pad - (height - 2 * pad) * ((v - low) / span) for v in values]
    label = "Tracking services contacted per week: " + ", ".join(f"{d}: {v}" for d, v in points)
    line = ""
    if count > 1:
        line = f'<polyline class="spark-line" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))}"/>'
    dots = "".join(f'<circle class="spark-dot" cx="{x:.1f}" cy="{y:.1f}" r="3.5"><title>{escape(d)}: {v}</title></circle>'
                   for (d, v), x, y in zip(points, xs, ys))
    return Markup(f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
                  f'role="img" aria-label="{escape(label)}">{line}{dots}</svg>')
