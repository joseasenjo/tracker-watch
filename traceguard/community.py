"""Community origins: reports measured by other people from their own country.

Layout (committed to the repository through a pull request):

  data/community/<slug>/meta.json            {"label": "Brazil", "contributor": "handle or anonymous", "added": "2026-10-05"}
  data/community/<slug>/<date>/<site>.json   one report per site, as written by `python -m traceguard`

Contributed files are untrusted. They are checked here (shape, size, known sites, no query strings, no cookie
values) and, when the site is built, their classification is recomputed from the raw request log with this
project's own list, so a submitted summary can never reach the site. Community origins are always shown as
"community, unverified": we cannot prove where the machine was or that it ran the unmodified tool.

Usage:
  python -m traceguard.community check data/community --sites-file data/sites.json
  python -m traceguard.community add my-scan/2026-10-05 --slug brazil-home --label Brazil --contributor handle
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .classify import Classifier, TrackerList, registrable_domain
from .reclassify import reclassify_report

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
UNSAFE_TEXT = re.compile(r"[<>&\"]")
MAX_FILE_BYTES = 4_000_000
MAX_RUNS = 10
MAX_REQUESTS_PER_RUN = 4000
MIN_PASSES, MIN_OBSERVE, MAX_OBSERVE = 2, 8, 60
MAX_AGE_DAYS = 400
LABEL_SUFFIX = " (community, unverified)"


class Problem(ValueError):
    pass


def load_sites(sites_file: Path | str) -> dict[str, dict]:
    data = json.loads(Path(sites_file).read_text(encoding="utf-8"))
    return {e["url"]: e for e in (data["sites"] if isinstance(data, dict) else data)}


RUN_LISTS = ("requests", "cookies", "observations", "internal_requests_blocked", "navigation_redirects")


def _check_requests(runs: list) -> str | None:
    """First problem found in the runs (shape and request logs), or None."""
    for run in runs:
        if not isinstance(run, dict) or not all(isinstance(run.get(k), list) for k in RUN_LISTS):
            return "a run is missing one of: " + ", ".join(RUN_LISTS)
        headers, storage = run.get("security_headers"), run.get("storage_items")
        if (run.get("status") not in ("ok", "blocked", "incomplete", "error") or not isinstance(headers, dict)
                or "strict_transport_security" not in headers or "content_security_policy" not in headers
                or not isinstance(storage, dict) or "local" not in storage or "session" not in storage):
            return "a run is missing its status, security_headers or storage_items"
        if any(not isinstance(c, dict) or not isinstance(c.get("domain"), str) for c in run["cookies"]):
            return "a cookie entry is malformed"
        if any(not isinstance(o, dict) or "kind" not in o for o in run["observations"]):
            return "an observation entry is malformed"
        if len(run["requests"]) > MAX_REQUESTS_PER_RUN:
            return "a run has too many requests"
        for request in run["requests"]:
            if not isinstance(request, dict):
                return "a request is not an object"
            host, url = request.get("host"), request.get("url")
            if not isinstance(host, str) or not HOST.match(host):
                return "a request has an invalid host"
            if not isinstance(url, str) or "?" in url or "#" in url or len(url) > 600:
                return "a request URL carries a query string or fragment, or is too long"
            if urlsplit(url).hostname != host:
                return "a request URL does not match its host"
            size = request.get("bytes")
            if size is not None and not (isinstance(size, int) and 0 <= size < 2**31):
                return "a request has an invalid size"
        if any("value" in c for c in run.get("cookies", []) if isinstance(c, dict)):
            return "cookie values must never be included"
    return None


def check_report(report, slug: str, sites: dict[str, dict] | None = None, today: Date | None = None) -> list[str]:
    """Return the problems found in one contributed report (empty list = acceptable)."""
    if not isinstance(report, dict):
        return ["the file is not a JSON object"]
    try:
        url = report["site"]["url"]
        measurement, runs, tool = report["measurement"], report["runs"], report["tool"]
        generated = datetime.fromisoformat(report["generated_at"])
    except (KeyError, TypeError, ValueError):
        return ["missing or malformed site, measurement, runs, tool or generated_at"]
    if not isinstance(measurement, dict) or not isinstance(runs, list) or not isinstance(tool, dict):
        return ["malformed measurement, runs or tool"]
    problems: list[str] = []
    if tool.get("name") != "traceguard":
        problems.append("tool name is not 'traceguard'")
    if sites is not None and url not in sites:
        problems.append(f"site {url!r} is not in the list of measured sites")
    if measurement.get("vantage") != slug:
        problems.append(f"measurement.vantage must equal the folder name {slug!r}")
    if measurement.get("interaction", "none") != "none":
        problems.append("only passive scans (no interaction) are accepted")
    passes, observe = measurement.get("passes"), measurement.get("observe_seconds")
    if not isinstance(passes, int) or passes < MIN_PASSES:
        problems.append(f"at least {MIN_PASSES} passes are required")
    if not isinstance(observe, (int, float)) or not MIN_OBSERVE <= observe <= MAX_OBSERVE:
        problems.append(f"observe_seconds must be between {MIN_OBSERVE} and {MAX_OBSERVE}")
    now = datetime.now(timezone.utc) if today is None else datetime.combine(today, datetime.min.time(), timezone.utc)
    if generated.tzinfo is None or generated > now + timedelta(days=1) or generated < now - timedelta(days=MAX_AGE_DAYS):
        problems.append("generated_at is in the future, too old, or has no time zone")
    if not 1 <= len(runs) <= MAX_RUNS:
        problems.append(f"expected 1 to {MAX_RUNS} runs")
    else:
        found = _check_requests(runs)
        if found:
            problems.append(found)
    return problems


def _meta_problems(meta) -> list[str]:
    if not isinstance(meta, dict):
        return ["meta.json must be a JSON object"]
    label, contributor = meta.get("label"), meta.get("contributor")
    problems = []
    if not isinstance(label, str) or not 2 <= len(label.strip()) <= 40 or UNSAFE_TEXT.search(label):
        problems.append("label must be 2-40 characters without < > & or quotes (usually the country)")
    if not isinstance(contributor, str) or not contributor.strip() or len(contributor) > 60 or UNSAFE_TEXT.search(contributor):
        problems.append("contributor must be a short text (use 'anonymous' if you prefer)")
    return problems


def check_tree(root: Path | str, sites: dict[str, dict] | None = None) -> dict[str, list[str]]:
    """Check every origin under `root`. Returns {relative path: [problems]} (only entries with problems)."""
    root = Path(root)
    found: dict[str, list[str]] = {}
    if not root.exists():
        return found
    for origin in sorted(p for p in root.iterdir() if p.is_dir()):
        slug = origin.name
        if not SLUG.match(slug):
            found[slug] = [f"folder name {slug!r} must be 3-40 characters: a-z, 0-9 and hyphens"]
            continue
        try:
            meta_problems = _meta_problems(json.loads((origin / "meta.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            meta_problems = ["meta.json is missing or not valid JSON"]
        if meta_problems:
            found[f"{slug}/meta.json"] = meta_problems
        for path in sorted(origin.glob("*/*.json")):
            rel = f"{slug}/{path.parent.name}/{path.name}"
            try:
                Date.fromisoformat(path.parent.name)
            except ValueError:
                found[rel] = ["the parent folder must be a date (YYYY-MM-DD)"]
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                found[rel] = [f"file is larger than {MAX_FILE_BYTES // 1_000_000} MB"]
                continue
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                found[rel] = ["not valid JSON"]
                continue
            problems = check_report(report, slug, sites)
            if problems:
                found[rel] = problems
    return found


def normalise(report: dict, site: dict | None, tracker_list: TrackerList) -> dict:
    """Recompute party, tracker lookup and every summary figure from the raw request log."""
    first_party = {registrable_domain(urlsplit(report["site"]["url"]).hostname or "")}
    if site:
        first_party |= {registrable_domain(d) for d in site.get("first_party_domains", [])}
    report = json.loads(json.dumps(report))
    report["site"] = {"name": (site or {}).get("name") or report["site"].get("name") or report["site"]["url"],
                      "url": report["site"]["url"], "first_party_domains": sorted(first_party)}
    for run in report["runs"]:
        final = registrable_domain(urlsplit(run.get("final_url") or "").hostname or "")
        classifier = Classifier({*first_party, final}, tracker_list)
        for request in run["requests"]:
            request["domain"] = registrable_domain(request["host"])
            request["party"] = classifier.party(request["host"])
        for cookie in run.get("cookies", []):
            cookie["party"] = classifier.party(str(cookie.get("domain", "")))
    return reclassify_report(report, tracker_list)


def load_community(root: Path | str, tracker_list: TrackerList, sites: dict[str, dict] | None = None):
    """Return (extra, labels, warnings). `extra` has the shape of origins.load_extra:
    vantage -> date -> stem -> report. Origins and files with problems are skipped, never partly used."""
    root = Path(root)
    extra: dict[str, dict[str, dict[str, dict]]] = {}
    labels: dict[str, str] = {}
    warnings: list[str] = []
    if not root.exists():
        return extra, labels, warnings
    bad = check_tree(root, sites)
    for origin in sorted(p for p in root.iterdir() if p.is_dir()):
        slug = origin.name
        if slug in bad or f"{slug}/meta.json" in bad:
            warnings.append(f"community origin {slug!r} skipped: {bad.get(slug) or bad.get(slug + '/meta.json')}")
            continue
        meta = json.loads((origin / "meta.json").read_text(encoding="utf-8"))
        dates: dict[str, dict[str, dict]] = {}
        for day in sorted(p for p in origin.iterdir() if p.is_dir()):
            reports = {}
            for path in sorted(day.glob("*.json")):
                rel = f"{slug}/{day.name}/{path.name}"
                if rel in bad:
                    warnings.append(f"community file {rel} skipped: {bad[rel]}")
                    continue
                report = json.loads(path.read_text(encoding="utf-8"))
                try:
                    reports[path.stem] = normalise(report, (sites or {}).get(report["site"]["url"]), tracker_list)
                except (KeyError, TypeError, ValueError) as exc:  # a malformed file must never stop the site build
                    warnings.append(f"community file {rel} skipped: could not be processed ({type(exc).__name__})")
            if reports:
                dates[day.name] = reports
        if dates:
            extra[slug] = dates
            labels[slug] = meta["label"].strip() + LABEL_SUFFIX
    return extra, labels, warnings


def add(folder: Path, root: Path, slug: str, label: str, contributor: str, sites: dict[str, dict] | None) -> int:
    """Check a dated folder of reports and copy it to <root>/<slug>/<date>/."""
    if not SLUG.match(slug):
        raise Problem("slug must be 3-40 characters: a-z, 0-9 and hyphens")
    try:
        Date.fromisoformat(folder.name)
    except ValueError:
        raise Problem("point to the dated folder written by the scanner, e.g. my-scan/2026-10-05") from None
    meta = {"label": label, "contributor": contributor, "added": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    meta_problems = _meta_problems(meta)
    if meta_problems:
        raise Problem("; ".join(meta_problems))
    staged = []
    for path in sorted(folder.glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        problems = check_report(report, slug, sites)
        if problems:
            raise Problem(f"{path.name}: " + "; ".join(problems) +
                          " (run the scan again with --vantage " + slug + ")")
        staged.append((path.name, report))
    if not staged:
        raise Problem("no report files found in that folder")
    target = root / slug / folder.name
    target.mkdir(parents=True, exist_ok=True)
    (root / slug / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    for name, report in staged:
        (target / name).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(staged)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.community", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate data/community (used by the pull-request check)")
    check.add_argument("root", nargs="?", default="data/community")
    check.add_argument("--sites-file", default="data/sites.json")
    new = sub.add_parser("add", help="validate a scan folder and copy it into data/community")
    new.add_argument("folder", help="dated folder written by the scanner, e.g. my-scan/2026-10-05")
    new.add_argument("--slug", required=True, help="short id of the origin, e.g. brazil-home (must equal the scan's --vantage)")
    new.add_argument("--label", required=True, help="public name, usually the country, e.g. Brazil")
    new.add_argument("--contributor", default="anonymous", help="public handle, or 'anonymous'")
    new.add_argument("--root", default="data/community")
    new.add_argument("--sites-file", default="data/sites.json")
    args = parser.parse_args(argv)
    sites = load_sites(args.sites_file) if Path(args.sites_file).exists() else None
    if args.command == "check":
        found = check_tree(args.root, sites)
        for rel, problems in found.items():
            print(f"{rel}: " + "; ".join(problems), file=sys.stderr)
        print("community data OK" if not found else f"{len(found)} problem file(s)")
        return 1 if found else 0
    try:
        count = add(Path(args.folder), Path(args.root), args.slug, args.label, args.contributor, sites)
    except Problem as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 1
    print(f"{count} reports copied to {args.root}/{args.slug}/. Open a pull request with that folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
