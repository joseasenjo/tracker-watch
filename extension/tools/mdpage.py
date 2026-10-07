"""The small subset of Markdown that PRIVACY.md uses (headings, paragraphs, lists, one table, `code`, **bold**),
turned into an HTML fragment for the extension's settings page. Every text is escaped; the page parses the
fragment with DOMParser and never injects it as a string."""
from __future__ import annotations

import html
import re

SEPARATOR = re.compile(r"\|[-| :]+\|")


def inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)


def markdown_page(md: str) -> str:
    out: list[str] = []
    para: list[str] = []
    items: list[tuple[str, list[str]]] = []
    rows: list[str] = []

    def cells(row: str) -> list[str]:
        return [c.strip() for c in row.strip().strip("|").split("|")]

    def flush() -> None:
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()
        if items:
            def li(item):
                text, subs = item
                inner = "<ul>" + "".join(f"<li>{inline(s)}</li>" for s in subs) + "</ul>" if subs else ""
                return f"<li>{inline(text)}{inner}</li>"
            out.append("<ul>" + "".join(li(i) for i in items) + "</ul>")
            items.clear()
        if rows:
            head, *body = [r for r in rows if not SEPARATOR.fullmatch(r)]
            out.append("<table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells(head)) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells(r)) + "</tr>" for r in body)
                       + "</table>")
            rows.clear()

    for line in md.splitlines():
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("|"):
            rows.append(s)
        elif s.startswith("## "):
            flush()
            out.append(f"<h3>{inline(s[3:])}</h3>")
        elif s.startswith("# "):
            flush()
            out.append(f"<h2>{inline(s[2:])}</h2>")
        elif s.startswith("- ") and items and line.startswith("  "):
            items[-1][1].append(s[2:])  # an indented item belongs to the previous one
        elif s.startswith("- "):
            if para:
                flush()
            items.append((s[2:], []))
        elif items and line.startswith("  "):
            if items[-1][1]:
                items[-1][1][-1] += " " + s
            else:
                items[-1] = (items[-1][0] + " " + s, items[-1][1])
        else:
            para.append(s)
    flush()
    return "\n".join(out) + "\n"
