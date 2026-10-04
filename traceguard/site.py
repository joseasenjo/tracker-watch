"""Static site generator: reads the weekly reports and writes a self-contained website.

The public site is in English and makes no third-party requests. A Spanish internal dashboard
can also be written to a separate folder that is never published.

Usage: python -m traceguard.site data/runs --out site --sites-file data/sites.json \
           --base-url https://example.org/tracker-watch/ [--dashboard dashboard_local]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import SCHEMA_VERSION
from .classify import DEFAULT_TRACKER_LIST
from .bands import band_for, describe as describe_bands
from .diff import compare_reports
from .cards import (alt_text as ranking_alt_text, origins_alt_text, origins_card_html, ranking_card_html,
                    render_png)
from .findings import SCRIPT_BEHAVIOURS
from .links import load_links
from .origins import compare as compare_origins, load_extra, paired_bars
from .spark import sparkline

SRC = Path(__file__).resolve().parent.parent / "site_src"
SITE_NAME = "Tracker Watch"
VANTAGE_LABELS = {"github-actions-us": "GitHub servers in the US", "local-windows-spain": "a PC in Spain"}
VANTAGE_LABELS_ES = {"github-actions-us": "servidores de GitHub en EEUU", "local-windows-spain": "un PC en España"}

STATUS_EN = {
    "blocked": "The site refused the automated browser (HTTP {http}).",
    "incomplete": "The page served an error or challenge page instead of its content ({error}).",
    "error": "The page could not be loaded ({error}).",
}
STATUS_ES = {
    "blocked": "La web rechazó al navegador automático (HTTP {http}).",
    "incomplete": "La web sirvió una página de error o de desafío ({error}).",
    "error": "No se pudo cargar la página ({error}).",
}


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(SRC / "templates"),
                       autoescape=select_autoescape(["html", "xml"]), trim_blocks=True, lstrip_blocks=True)


def load_history(runs_dir: Path) -> dict[str, dict[str, dict]]:
    """date -> {stem: report}, oldest first."""
    history = {}
    for folder in sorted(p for p in Path(runs_dir).iterdir() if p.is_dir()):
        history[folder.name] = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))}
    return history


def load_site_meta(sites_file: str | None) -> dict[str, dict]:
    if not sites_file:
        return {}
    data = json.loads(Path(sites_file).read_text(encoding="utf-8"))
    return {(e["url"]): e for e in (data["sites"] if isinstance(data, dict) else data)}


def load_trackers() -> tuple[list[dict], str]:
    data = json.loads(DEFAULT_TRACKER_LIST.read_text(encoding="utf-8"))
    rows = [{"domain": d, "entity": v["entity"], "category": v["category"], "evidence": v.get("evidence")}
            for d, v in sorted(data["domains"].items())]
    return rows, data["_meta"]["note"]


def _status_text(summary: dict, table: dict) -> str:
    template = table.get(summary["status"], "{error}")
    return template.format(http=summary.get("http_status"), error=summary.get("error") or "no details")


def _domain_rows(report: dict) -> list[dict]:
    """Third-party domains with the classification seen in the requests of the passes that worked."""
    known: dict[str, dict] = {}
    for run in report["runs"]:
        if run["status"] != "ok":
            continue
        for request in run["requests"]:
            if request.get("party") == "third" and request.get("tracker"):
                known.setdefault(request["domain"], request["tracker"])
    rows = []
    for item in report["summary"].get("third_party_domains", []):
        tracker = known.get(item["domain"], {})
        rows.append({"domain": item["domain"], "passes_seen": item["passes_seen"],
                     "entity": tracker.get("entity"), "category": tracker.get("category")})
    rows.sort(key=lambda r: (r["category"] is None, r["domain"]))
    return rows


def _entry(stem: str, report: dict, meta: dict, history: dict, date: str, diff: dict | None) -> dict:
    summary, site = report["summary"], report["site"]
    info = meta.get(site["url"], {})
    metrics = summary.get("metrics", {})
    entry = {
        "stem": stem, "name": site["name"], "url": site["url"], "group": info.get("group", "Other"),
        "kind": info.get("kind", "news"), "status": summary["status"],
        "confidence": summary.get("confidence"), "passes_ok": summary["passes_ok"],
        "passes_total": summary["passes_total"],
        "tracking": metrics.get("tracking_services"), "domains": metrics.get("third_party_domains"),
        "requests": metrics.get("third_party_requests"),
        "cookies_third": metrics.get("third_party_cookies"), "cookies_total": metrics.get("cookies_total"),
        "findings": report["findings"],
        # keyboard listeners stay in the raw data only: nearly every page has some, so listing them is noise
        "observations": [o for o in summary.get("observations", [])
                         if o["party"] == "third" and o["stable"] and o["kind"] in SCRIPT_BEHAVIOURS],
        "diff": diff, "delta": None,
        "status_text": _status_text(summary, STATUS_EN) if summary["status"] != "ok" else "",
        "status_text_es": _status_text(summary, STATUS_ES) if summary["status"] != "ok" else "",
        "domain_rows": _domain_rows(report) if summary["status"] == "ok" else [],
    }
    entry["band"] = band_for(entry["tracking"], entry["confidence"]) if summary["status"] == "ok" else None
    if diff and diff["comparable"]:
        entry["delta"] = diff["metrics"]["tracking_services"]["delta"]
    entry["history"] = [{"date": d, "vantage": VANTAGE_LABELS.get(r["measurement"]["vantage"], r["measurement"]["vantage"]),
                         "status": r["summary"]["status"],
                         "tracking": r["summary"].get("metrics", {}).get("tracking_services")}
                        for d, reports in history.items() for s, r in reports.items() if s == stem]
    # Only weeks measured from the same place are plotted together: other origins are not comparable.
    latest_vantage = VANTAGE_LABELS.get(report["measurement"]["vantage"], report["measurement"]["vantage"])
    points = [(h["date"], h["tracking"]) for h in entry["history"]
              if h["status"] == "ok" and h["tracking"] is not None and h["vantage"] == latest_vantage]
    entry["trend_n"] = len(points)
    entry["spark"] = sparkline(points)
    entry["spark_small"] = sparkline(points, width=96, height=28)
    return entry


def build_context(runs_dir: Path | str, sites_file: str | None = None, extra_dir: Path | str | None = None) -> dict:
    history = load_history(Path(runs_dir))
    if not history:
        raise FileNotFoundError(f"no dated report folders in {runs_dir}")
    dates = list(history)
    date = dates[-1]
    meta = load_site_meta(sites_file)
    entries, diffs = [], []
    for stem, report in history[date].items():
        previous = next((history[d][stem] for d in reversed(dates[:-1]) if stem in history[d]), None)
        diff = compare_reports(previous, report) if previous else None
        if diff:
            diff["stem"] = stem
            diffs.append(diff)
        entries.append(_entry(stem, report, meta, history, date, diff))

    measured = sorted((e for e in entries if e["status"] == "ok"),
                      key=lambda e: (-e["tracking"], -e["requests"], e["name"]))
    unmeasured = sorted((e for e in entries if e["status"] != "ok"), key=lambda e: e["name"])
    first = next(iter(history[date].values()))
    measurement = first["measurement"]
    stamps = sorted(datetime.fromisoformat(r["generated_at"]) for r in history[date].values())
    trackers, tracker_source = load_trackers()
    pending: dict[str, set] = {}
    for stem, report in history[date].items():
        if report["summary"]["status"] != "ok":
            continue
        for row in _domain_rows(report):
            if row["entity"] is None and next((d for d in report["summary"]["third_party_domains"]
                                               if d["domain"] == row["domain"]), {}).get("stable"):
                pending.setdefault(row["domain"], set()).add(report["site"]["name"])
    pending_domains = sorted(({"domain": d, "sites": sorted(s)} for d, s in pending.items() if len(s) >= 2),
                             key=lambda x: (-len(x["sites"]), x["domain"]))
    primary_label = VANTAGE_LABELS.get(measurement["vantage"], measurement["vantage"])
    extra = load_extra(extra_dir if extra_dir is not None else Path(runs_dir).parent / "extra")
    extra = {v: d for v, d in extra.items() if VANTAGE_LABELS.get(v, v) != primary_label}
    origins = compare_origins(date, history[date], primary_label, extra, VANTAGE_LABELS, meta)
    return {
        "origins": origins, "origins_chart": paired_bars(origins["rows"], origins["origins"]),
        "site_name": SITE_NAME, "date": date, "dates": dates,
        "vantage": VANTAGE_LABELS.get(measurement["vantage"], measurement["vantage"]),
        "passes": measurement["passes"], "observe": f"{measurement['observe_seconds']:g}",
        "schema": SCHEMA_VERSION, "entries": entries, "measured": measured, "unmeasured": unmeasured,
        "with_tracking": sum(1 for e in measured if e["tracking"] > 0),
        "groups": sorted({e["group"] for e in entries}),
        "diffs": diffs, "notable": [d for d in diffs if d["comparable"] and d["notable"]],
        "not_comparable": [d for d in diffs if not d["comparable"]],
        "trackers": trackers, "tracker_count": len(trackers), "tracker_source": tracker_source,
        "pending_domains": pending_domains, "history": history,
        "low_confidence": [e for e in measured if e["confidence"] in ("medium", "low")],
        "vantage_es": VANTAGE_LABELS_ES.get(measurement["vantage"], measurement["vantage"]),
        "bands": describe_bands(),
        "any_trend": any(e["trend_n"] >= 2 for e in measured),
        "lookup": [{"name": e["name"], "host": (urlsplit(e["url"]).hostname or "").removeprefix("www."),
                    "stem": e["stem"], "measured": e["status"] == "ok"} for e in entries],
        "scan_started": stamps[0].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "scan_finished": stamps[-1].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "tool_version": first["tool"]["version"], "browser": measurement.get("browser", "unknown"),
        "user_agent": measurement.get("user_agent", ""), "locale": measurement.get("locale", ""),
        "timezone": measurement.get("timezone", ""),
    }


def _feed(ctx: dict, base_url: str) -> str:
    base = base_url if base_url.endswith("/") else base_url + "/"
    now = format_datetime(datetime.now(timezone.utc))
    items = []
    for date in reversed(ctx["dates"]):
        link = f"{base}index.html#{date}"
        top = ctx["measured"][:3] if date == ctx["date"] else []
        summary = (f"{len(ctx['measured'])} sites measured, {ctx['with_tracking']} contacted at least one tracking service "
                   f"before any interaction (measured from {ctx['vantage']}). "
                   + ("Most: " + ", ".join(f"{e['name']} {e['tracking']}" for e in top) + "." if top else ""))
        if date == ctx["date"] and ctx["notable"]:
            summary += " Notable changes: " + "; ".join(d["site"] for d in ctx["notable"][:5]) + "."
        items.append(f"<item><title>Weekly measurement {escape(date)}</title><link>{escape(link)}</link>"
                     f"<guid isPermaLink=\"false\">{escape(date)}</guid><description>{escape(summary)}</description></item>")
        if len(items) >= 20:
            break
    return ("<?xml version=\"1.0\" encoding=\"utf-8\"?><rss version=\"2.0\"><channel>"
            f"<title>{SITE_NAME}</title><link>{escape(base)}</link>"
            "<description>Weekly measurement of third-party tracking on news websites.</description>"
            f"<lastBuildDate>{now}</lastBuildDate>{''.join(items)}</channel></rss>")


def _check_subscribe_url(url: str | None) -> tuple[str, str]:
    """Return (url, host) for a hosted email-signup page, or ('', '') when none is configured."""
    url = (url or "").strip()
    if not url:
        return "", ""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("the subscribe URL must be an https:// address")
    return url, parts.hostname


_EMAIL = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$")


def _check_contact(email: str | None, linkedin: str | None, author: str | None, next_scan: str | None) -> dict:
    """Optional public contact details. Nothing is shown unless it was passed explicitly."""
    email, linkedin = (email or "").strip(), (linkedin or "").strip()
    if email and not _EMAIL.match(email):
        raise ValueError("the contact email does not look like an email address")
    if linkedin:
        parts = urlsplit(linkedin)
        if parts.scheme != "https" or not parts.hostname or not parts.hostname.endswith("linkedin.com"):
            raise ValueError("the LinkedIn address must be an https://…linkedin.com/… URL")
    return {"contact_email": email, "linkedin_url": linkedin, "author": (author or "").strip(),
            "next_scan": (next_scan or "").strip()}


def _write_csv(ctx: dict, path: Path) -> None:
    columns = ["date", "site", "url", "country", "type", "status", "confidence", "tracking_services", "band",
               "third_party_domains", "third_party_requests", "third_party_cookies"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for e in sorted(ctx["entries"], key=lambda x: x["name"]):
            ok = e["status"] == "ok"
            writer.writerow([ctx["date"], e["name"], e["url"], e["group"], e["kind"], e["status"],
                             e["confidence"] or "", e["tracking"] if ok else "", e["band"] or "",
                             e["domains"] if ok else "", e["requests"] if ok else "",
                             e["cookies_third"] if ok else ""])


def build_site(runs_dir: Path | str, out_dir: Path | str, *, sites_file: str | None = None,
               base_url: str = "https://example.org/tracker-watch/", subscribe_url: str | None = None,
               repo_url: str | None = None, contact_email: str | None = None, linkedin_url: str | None = None,
               author: str | None = None, next_scan: str | None = None, share_cards: bool = False,
               extra_dir: Path | str | None = None, links_file: Path | str | None = None) -> dict:
    ctx = build_context(runs_dir, sites_file, extra_dir)
    ctx["links"] = load_links(links_file if links_file is not None else Path(runs_dir).parent / "links.json")
    base = base_url if base_url.endswith("/") else base_url + "/"
    ctx["share_image"], ctx["share_alt"] = "", ranking_alt_text(ctx)
    ctx["share_origins_image"], ctx["share_origins_alt"] = "", origins_alt_text(ctx)
    ctx["site_base"] = base
    ctx["subscribe_url"], ctx["subscribe_host"] = _check_subscribe_url(subscribe_url)
    ctx["repo_url"] = (repo_url or "").strip()
    ctx.update(_check_contact(contact_email, linkedin_url, author, next_scan))
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "sites").mkdir(parents=True)
    shutil.copytree(SRC / "assets", out / "assets")
    env = _env()

    def render(template: str, target: Path, **extra):
        root = "../" * (len(target.relative_to(out).parts) - 1)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(env.get_template(template).render(**ctx, root=root, **extra), encoding="utf-8")

    if share_cards and render_png(ranking_card_html(ctx), out / "share" / "ranking.png"):
        ctx["share_image"] = base + "share/ranking.png"
    if share_cards and ctx["origins"]["has_data"] and render_png(origins_card_html(ctx), out / "share" / "origins.png"):
        ctx["share_origins_image"] = base + "share/origins.png"
    render("index.html", out / "index.html", page="index")
    render("origins.html", out / "origins.html", page="origins")
    render("changes.html", out / "changes.html", page="changes")
    render("unmeasured.html", out / "unmeasured.html", page="unmeasured")
    render("method.html", out / "method.html", page="method")
    render("privacy.html", out / "privacy.html", page="privacy")
    render("about.html", out / "about.html", page="about")
    render("contact.html", out / "contact.html", page="contact")
    render("shortlinks.html", out / "shortlinks.html", page="shortlinks")
    for link in ctx["links"]:
        render("go.html", out / "go" / link["code"] / "index.html", page="go", link=link)
    (out / "data").mkdir(exist_ok=True)
    _write_csv(ctx, out / "data" / "latest.csv")
    for entry in ctx["entries"]:
        render("site.html", out / "sites" / f"{entry['stem']}.html", page="site", s=entry)
    (out / "feed.xml").write_text(_feed(ctx, base_url), encoding="utf-8")
    for date, reports in ctx["history"].items():
        (out / "data" / date).mkdir(parents=True, exist_ok=True)
        for stem, report in reports.items():
            (out / "data" / date / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return ctx


def build_dashboard(ctx: dict, out_dir: Path | str, drafts_dir: Path | str = "data/drafts",
                    pending_links: list[dict] | None = None) -> None:
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(SRC / "assets", out / "assets")
    drafts = []
    drafts_path = Path(drafts_dir)
    if drafts_path.exists():
        drafts = [f"{p.parent.name}/{p.name}" for p in sorted(drafts_path.glob("*/*.md"))]
    extra = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "total": len(ctx["entries"]),
        "all_sites": sorted(ctx["entries"], key=lambda e: (e["status"] == "ok", e["name"])), "drafts": drafts,
        "pending_links": pending_links or []}
    (out / "index.html").write_text(_env().get_template("dashboard_es.html").render(**ctx, **extra), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.site", description=__doc__)
    parser.add_argument("runs_dir", nargs="?", default="data/runs")
    parser.add_argument("--out", default="site", help="public site folder (default site)")
    parser.add_argument("--sites-file", default="data/sites.json", help="gives each site its country and type")
    parser.add_argument("--base-url", default="https://example.org/tracker-watch/", help="used in the RSS feed")
    parser.add_argument("--subscribe-url", default="", help="https page of a hosted email service; empty keeps the button disabled")
    parser.add_argument("--repo-url", default="", help="public repository URL, shown as the contact point on the privacy page")
    parser.add_argument("--contact-email", default="", help="public contact for corrections (use a dedicated address)")
    parser.add_argument("--linkedin-url", default="", help="optional public LinkedIn profile or page")
    parser.add_argument("--author", default="", help="optional name shown on the About page")
    parser.add_argument("--next-scan", default="", help="text such as 'Mondays 05:00 UTC'; hidden when empty")
    parser.add_argument("--extra-dir", default=None, help="folder with reports from other origins (default: data/extra next to the runs folder)")
    parser.add_argument("--share-cards", action="store_true", help="also render the shareable images (needs Chromium)")
    parser.add_argument("--links-file", default=None, help="approved short links (default: data/links.json next to the runs folder)")
    parser.add_argument("--dashboard", help="also write the Spanish internal dashboard to this folder (never publish it)")
    parser.add_argument("--pending-repo", default="", help="with --dashboard: read pending link requests from this OWNER/NAME using the GitHub CLI")
    args = parser.parse_args(argv)
    sites_file = args.sites_file if Path(args.sites_file).exists() else None
    ctx = build_site(args.runs_dir, args.out, sites_file=sites_file, base_url=args.base_url,
                     subscribe_url=args.subscribe_url, repo_url=args.repo_url, contact_email=args.contact_email,
                     linkedin_url=args.linkedin_url, author=args.author, next_scan=args.next_scan,
                     share_cards=args.share_cards, extra_dir=args.extra_dir, links_file=args.links_file)
    if args.share_cards and not ctx["share_image"]:
        print("warning: the shareable image was not created (Playwright or Chromium is not available)")
    print(f"site: {args.out} ({len(ctx['entries'])} sites, measurement of {ctx['date']})")
    if args.dashboard:
        from .links import pending_requests
        build_dashboard(ctx, args.dashboard,
                        pending_links=pending_requests(args.pending_repo) if args.pending_repo else None)
        print(f"dashboard (Spanish, local only): {args.dashboard}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
