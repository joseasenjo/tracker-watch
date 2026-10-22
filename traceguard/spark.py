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


def line_chart(series: list[tuple[str, list[tuple[str, float | None]]]], *, width: int = 640, height: int = 260,
               unit: str = "") -> Markup:
    """A small multi-line chart, one line per label, the same x positions (scan dates) for every line. Points with no value
    leave a gap. Colours come from CSS classes (light and dark); the data is also in the table next to it and in the
    accessible description."""
    dates = sorted({d for _, points in series for d, _ in points})
    values = [v for _, points in series for _, v in points if v is not None]
    if not dates or not values:
        return Markup("")
    left, right, top, bottom = 44, 110, 16, 34
    low, high = 0.0, max(values) * 1.08 or 1.0
    plot_w, plot_h = width - left - right, height - top - bottom

    def x(date: str) -> float:
        return left + plot_w * (dates.index(date) / (len(dates) - 1) if len(dates) > 1 else 0.5)

    def y(value: float) -> float:
        return top + plot_h * (1 - (value - low) / (high - low))

    parts = []
    for step in range(5):  # horizontal grid and the values on the left
        v = high * step / 4
        parts.append(f'<line class="lc-grid" x1="{left}" x2="{left + plot_w}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                     f'<text class="lc-num" x="{left - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{v:.0f}</text>')
    for date in dates:
        parts.append(f'<text class="lc-num" x="{x(date):.1f}" y="{height - 10}" text-anchor="middle">{escape(date[5:])}</text>')
    description = []
    for index, (label, points) in enumerate(series):
        css = f"lc-{index % 5}"
        known = [(d, v) for d, v in points if v is not None]
        for (d1, v1), (d2, v2) in zip(known, known[1:]):
            parts.append(f'<line class="lc-line {css}" x1="{x(d1):.1f}" y1="{y(v1):.1f}" x2="{x(d2):.1f}" y2="{y(v2):.1f}"/>')
        for d, v in known:
            parts.append(f'<circle class="lc-dot {css}" cx="{x(d):.1f}" cy="{y(v):.1f}" r="3.5"><title>{escape(label)} {escape(d)}: {v:g}{escape(unit)}</title></circle>')
        if known:
            last_d, last_v = known[-1]
            parts.append(f'<text class="lc-key {css}" x="{x(last_d) + 8:.1f}" y="{y(last_v) + 4:.1f}">{escape(label)}</text>')
        description.append(f"{label}: " + ", ".join(f"{d} {v:g}{unit}" for d, v in known))
    label_text = "Line chart. " + "; ".join(description)
    return Markup(f'<svg class="linechart" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
                  f'aria-label="{escape(label_text)}">{"".join(parts)}</svg>')


def stacked_bars(rows: list[tuple[str, dict[str, int]]], keys: tuple[str, ...], *, width: int = 640, height: int = 240) -> Markup:
    """One column per scan, split into the bands (A, B, ...) with the number of sites in each. Colours: CSS classes sb-0..sb-4
    (light and dark); the numbers are written inside the segments and in the accessible description."""
    if not rows:
        return Markup("")
    left, right, top, bottom = 40, 90, 14, 32
    total = max(sum(counts.get(k, 0) for k in keys) for _, counts in rows) or 1
    plot_w, plot_h = width - left - right, height - top - bottom
    slot = plot_w / len(rows)
    bar = min(70.0, slot * 0.62)
    parts, description = [], []
    for i, (label, counts) in enumerate(rows):
        x = left + slot * i + (slot - bar) / 2
        y = top + plot_h
        for index, key in enumerate(keys):
            n = counts.get(key, 0)
            if not n:
                continue
            h = plot_h * n / total
            y -= h
            parts.append(f'<rect class="sb sb-{index % 5}" x="{x:.1f}" y="{y:.1f}" width="{bar:.1f}" height="{h:.1f}"><title>{escape(label)}, band {escape(key)}: {n}</title></rect>')
            if h >= 14:
                parts.append(f'<text class="sb-num" x="{x + bar / 2:.1f}" y="{y + h / 2 + 4:.1f}" text-anchor="middle">{n}</text>')
        parts.append(f'<text class="lc-num" x="{x + bar / 2:.1f}" y="{height - 10}" text-anchor="middle">{escape(label[5:])}</text>')
        description.append(f"{label}: " + ", ".join(f"{k} {counts.get(k, 0)}" for k in keys))
    for index, key in enumerate(keys):  # the key, on the right
        parts.append(f'<rect class="sb sb-{index % 5}" x="{width - right + 14}" y="{top + 6 + 20 * index}" width="12" height="12"/>'
                     f'<text class="lc-num" x="{width - right + 32}" y="{top + 16 + 20 * index}">Band {escape(key)}</text>')
    return Markup(f'<svg class="stackbars" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
                  f'aria-label="{escape("Sites per band and scan. " + "; ".join(description))}">{"".join(parts)}</svg>')


def heatmap(rows: list[str], columns: list[str], values: list[list[int]], *, extra: list[int] | None = None,
            extra_label: str = "All sites", unit: str = "%") -> Markup:
    """Rows x columns of percentages as shaded cells, with the number in each cell; an optional last column (the overall
    figure). Shading uses one CSS class (hm-cell) with an opacity from the value, so light and dark themes both work."""
    if not rows or not columns:
        return Markup("")
    label_w, cell_w, cell_h, top = 170, 62, 24, 38
    names = columns + ([extra_label] if extra else [])
    width, height = label_w + cell_w * len(names) + 8, top + cell_h * len(rows) + 8
    parts = []
    for c, name in enumerate(names):
        parts.append(f'<text class="lc-num" x="{label_w + cell_w * c + cell_w / 2:.1f}" y="{top - 12}" text-anchor="middle">{escape(name)}</text>')
    for r, row in enumerate(rows):
        y = top + cell_h * r
        parts.append(f'<text class="hm-label" x="{label_w - 8}" y="{y + cell_h / 2 + 4:.1f}" text-anchor="end">{escape(row)}</text>')
        for c in range(len(names)):
            v = values[r][c] if c < len(columns) else extra[r]
            x = label_w + cell_w * c
            cls = "hm-cell hm-extra" if c >= len(columns) else "hm-cell"
            parts.append(f'<rect class="{cls}" x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" style="--v:{v / 100:.2f}"><title>{escape(row)}, {escape(names[c])}: {v}{escape(unit)}</title></rect>'
                         f'<text class="hm-num" x="{x + (cell_w - 2) / 2:.1f}" y="{y + cell_h / 2 + 3:.1f}" text-anchor="middle">{v}</text>')
    return Markup(f'<svg class="heatmap" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
                  f'aria-label="{escape("Share of the sites of each family running each company: " + "; ".join(f"{row}: " + ", ".join(f"{names[c]} {values[r][c] if c < len(columns) else extra[r]}%" for c in range(len(names))) for r, row in enumerate(rows)))}">{"".join(parts)}</svg>')
