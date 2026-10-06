"""Build the data snapshot that ships inside the Tracker Watch Lens extension.

The extension works offline and never asks the website for anything, so everything it needs to classify
and explain a page travels with it, generated from this repository (one source of truth):

    extension/data/trackers.json   the classification list (suffix -> operator, category, verified)
    extension/data/glossary.json   categories, bands and the plain-language phrases of the site
    extension/data/sites.json      the latest weekly figures per measured site, with a short history
    extension/data/psl.json        public-suffix rules (ICANN section), to find the registrable domain
    extension/data/index.json      versions, counts and SHA-256 of the files above
    extension/tests/fixtures/parity.json   hosts with the answers the Python engine gives (parity tests)

Output is deterministic: no timestamps, sorted keys, versions taken from the data itself. `--check`
rebuilds in memory and fails if the files on disk are stale (for CI).

    python extension/tools/build_data.py [--check]
"""
from __future__ import annotations

import argparse
import encodings.idna
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import tldextract  # noqa: E402

from traceguard import bands, behaviour, categories, consent  # noqa: E402
from traceguard.classify import DEFAULT_TRACKER_LIST, TrackerList, registrable_domain  # noqa: E402
from traceguard.findings import SCRIPT_BEHAVIOURS, SCRIPT_CAVEAT  # noqa: E402
from traceguard.report import passive_requests, summarize_run  # noqa: E402

EXT = ROOT / "extension"
DATA_DIR = EXT / "data"
PARITY_FILE = EXT / "tests" / "fixtures" / "parity.json"
RUNS_DIR = ROOT / "data" / "runs"
SITES_FILE = ROOT / "data" / "sites.json"
HISTORY_WEEKS = 8
SCHEMA = 1

# Hosts that exercise the edge cases of suffix matching and of the public-suffix rules.
EDGE_HOSTS = [
    "", ".", "localhost", "127.0.0.1", "192.168.1.20", "[::1]", "site.test", "a.b.site.test",
    "COM", "co.uk", "bbc.co.uk", "news.bbc.co.uk", "WWW.BBC.CO.UK.", "x.y.z.bbc.co.uk",
    "example.com.", "www.ck", "foo.ck", "a.foo.ck", "city.kawasaki.jp", "a.b.kawasaki.jp",
    "s3.amazonaws.com", "foo.blogspot.com", "foo.github.io", "xn--fiqs8s", "foo.xn--fiqs8s",
    "a.b.xn--fiqs8s", "foo.xn--p1ai", "elmundo.es", "e00-elmundo.uecdn.es",
    "googletagmanager.com", "www.googletagmanager.com", "notgoogletagmanager.com",
    "doubleclick.net.evil.example", "ad.doubleclick.net", "stats.g.doubleclick.net",
]


def dumps(obj, compact: bool = False) -> str:
    if compact:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def build_trackers(seed: dict) -> dict:
    domains = {}
    for suffix, entry in sorted(seed["domains"].items()):
        domains[suffix] = {"entity": entry["entity"], "category": entry["category"],
                           "verified": not entry.get("evidence")}
    meta = seed["_meta"]
    return {"schema": SCHEMA, "tracking_categories": sorted(meta["tracking_categories"]),
            "other_categories": sorted(meta.get("other_categories", [])), "domains": domains,
            "entries": len(domains), "unverified": sum(1 for d in domains.values() if not d["verified"])}


def build_glossary() -> dict:
    return {
        "schema": SCHEMA,
        "categories": categories.CATEGORIES,
        "order": categories.ORDER,
        "bands": [{"label": label, "low": low, "high": high} for label, low, high in bands.BANDS],
        "banded_confidence": list(bands.BANDED_CONFIDENCE),
        "request_kinds": list(behaviour.KINDS),
        "kind_phrases": {k: {"singular": s, "plural": p, "text": t} for k, (s, p, t) in behaviour.PHRASES.items()},
        "script_behaviours": SCRIPT_BEHAVIOURS,
        "script_caveat": SCRIPT_CAVEAT,
        # documented ids/classes of consent tools (traceguard.consent): the extension only labels them
        "consent_tools": [{"name": n, "banner": b, "reject": r, "accept": a} for n, b, r, a in consent.CMPS],
    }


def load_history() -> list[tuple[str, dict[str, dict]]]:
    """[(date, {stem: report})], oldest first."""
    out = []
    for folder in sorted(p for p in RUNS_DIR.iterdir() if p.is_dir()):
        out.append((folder.name, {p.stem: json.loads(p.read_text(encoding="utf-8"))
                                  for p in sorted(folder.glob("*.json"))}))
    return out


def build_sites(history: list[tuple[str, dict[str, dict]]]) -> dict:
    meta = {e["url"]: e for e in json.loads(SITES_FILE.read_text(encoding="utf-8"))["sites"]}
    latest_date, latest = history[-1]
    sites = {}
    for stem, rep in sorted(latest.items()):
        summary, site, measurement = rep["summary"], rep["site"], rep["measurement"]
        extra = meta.get(site["url"], {})
        count = summary.get("metrics", {}).get("tracking_services")
        points = []
        for date, reports in history[-HISTORY_WEEKS:]:
            r = reports.get(stem)
            if r and r["summary"].get("status") == "ok":
                points.append({"date": date, "tracking_services": r["summary"]["metrics"]["tracking_services"]})
        sites[stem] = {
            "name": site["name"], "url": site["url"],
            "host": registrable_domain(site["url"].split("//", 1)[-1].split("/", 1)[0]),
            "first_party_domains": sorted(site.get("first_party_domains", [])),
            "group": extra.get("group"), "kind": extra.get("kind"),
            "date": latest_date, "vantage": measurement.get("vantage"), "browser": measurement.get("browser"),
            "status": summary.get("status"), "confidence": summary.get("confidence"),
            "tracking_services": count, "third_party_domains": summary.get("metrics", {}).get("third_party_domains"),
            "band": bands.band_for(count, summary.get("confidence")) if summary.get("status") == "ok" else None,
            "services": sorted(s["service"] for s in summary.get("services", []) if s.get("tracking")),
            "history": points,
        }
    return {"schema": SCHEMA, "date": latest_date, "sites": sites}


def _idna(rule: str) -> str | None:
    """Punycode form of a rule with non-ASCII labels (browsers give hosts in punycode)."""
    try:
        labels = [lab if lab in ("*",) or lab.startswith("!") or lab.isascii()
                  else encodings.idna.ToASCII(lab).decode("ascii") for lab in rule.split(".")]
    except UnicodeError:
        return None
    return ".".join(labels)


def build_psl() -> dict:
    """ICANN rules of the snapshot bundled with tldextract: the same list the engine uses."""
    path = Path(tldextract.__file__).with_name(".tld_set_snapshot")
    text = path.read_text(encoding="utf-8")
    start, end = text.index("===BEGIN ICANN DOMAINS==="), text.index("===END ICANN DOMAINS===")
    rules = set()
    for line in text[start:end].splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        rule = line.split()[0].lower()
        rules.add(rule)
        if not rule.isascii():
            ascii_rule = _idna(rule.lstrip("!"))
            if ascii_rule:
                rules.add(("!" if rule.startswith("!") else "") + ascii_rule)
    version = next((ln.split(":", 1)[1].strip() for ln in text.splitlines()[:20] if "VERSION:" in ln), None)
    import importlib.metadata as md
    return {"schema": SCHEMA, "source": "Public Suffix List (ICANN section), snapshot bundled with tldextract "
            f"{md.version('tldextract')}", "list_version": version, "license": "MPL-2.0",
            "rules": sorted(rules)}


def build_parity(tracker_list: TrackerList, sites: dict, history) -> dict:
    hosts = set(EDGE_HOSTS)
    for suffix in tracker_list.domains:
        hosts.update({suffix, "www." + suffix, "a.b." + suffix, "x" + suffix})
    for _, reports in history:
        for rep in reports.values():
            hosts.update(d["domain"] for d in rep["summary"].get("third_party_domains") or [])
            for run in rep.get("runs") or []:
                hosts.update(r["host"] for r in run.get("requests") or [] if r.get("host"))
    for site in sites["sites"].values():
        hosts.add(site["url"].split("//", 1)[-1].split("/", 1)[0])
        hosts.update(site["first_party_domains"])
    cases = []
    for host in sorted(h for h in hosts if isinstance(h, str)):
        cases.append({"host": host, "registrable": registrable_domain(host), "lookup": tracker_list.lookup(host)})
    band_cases = [{"count": n, "band": bands.band_for(n)} for n in range(0, 61)]
    return {"schema": SCHEMA, "cases": cases, "bands": band_cases, "phrases": build_phrase_cases(),
            "replays": build_replays(tracker_list, history)}


# Playwright resource type (engine) -> webRequest type (extension), to replay measured passes.
REPLAY_TYPE = {"document": "sub_frame", "xhr": "xmlhttprequest", "fetch": "xmlhttprequest",
               "eventsource": "xmlhttprequest", "ping": "ping", "websocket": "websocket", "script": "script",
               "image": "image", "stylesheet": "stylesheet", "font": "font", "media": "media"}


def build_phrase_cases() -> list[dict]:
    """Synthetic activities with the engine's wording (behaviour.phrases)."""
    zero = {k: 0 for k in behaviour.KINDS}
    activities = [
        {"requests": {**zero, "script": 1}, "cookies": [], "behaviours": []},
        {"requests": {**zero, "script": 3, "image": 1, "background": 2, "frame": 1, "other": 4},
         "cookies": [{"name": "a", "persistent": False, "days": None}], "behaviours": ["canvas_read"]},
        {"requests": {**zero, "image": 2}, "cookies": [{"name": "a", "persistent": True, "days": 390},
                                                        {"name": "b", "persistent": True, "days": 30},
                                                        {"name": "c", "persistent": False, "days": None}],
         "behaviours": ["geolocation_request", "webrtc_connection"]},
        {"requests": {**zero, "background": 1}, "cookies": [{"name": "a", "persistent": True, "days": None}],
         "behaviours": []},
        {"requests": dict(zero), "cookies": [], "behaviours": []},
    ]
    return [{"activity": a, "phrases": behaviour.phrases(a)} for a in activities]


def build_replays(tracker_list: TrackerList, history) -> list[dict]:
    """First good pass of every site in the latest run, with the engine's per-pass counts."""
    _, latest = history[-1]
    replays = []
    for stem, rep in sorted(latest.items()):
        run = next((r for r in rep.get("runs") or [] if r["status"] == "ok"), None)
        if run is None:
            continue
        summary = summarize_run(run, tracker_list)
        kinds: dict[str, dict[str, int]] = {}
        for r in passive_requests(run):
            t = r.get("tracker")
            if r.get("party") == "third" and t and t["service"] in summary["tracking_services"]:
                k = behaviour.KIND_OF.get(r.get("resource_type"), "other")
                kinds.setdefault(t["service"], {}).setdefault(k, 0)
                kinds[t["service"]][k] += 1
        replays.append({
            "site": stem, "url": rep["site"]["url"], "final_url": run.get("final_url"),
            "first_party_domains": rep["site"]["first_party_domains"],
            "requests": [[r["host"], REPLAY_TYPE.get(r["resource_type"], "other")] for r in passive_requests(run)],
            "expected": {"tracking_services": sorted(summary["tracking_services"]),
                         "third_party_requests": summary["third_party_requests"],
                         "third_party_domains": len(summary["third_party_domains"]), "kinds": kinds},
        })
    return replays


def build_all() -> dict[Path, str]:
    seed = json.loads(DEFAULT_TRACKER_LIST.read_text(encoding="utf-8"))
    tracker_list = TrackerList.load()
    history = load_history()
    trackers, glossary, sites, psl = build_trackers(seed), build_glossary(), build_sites(history), build_psl()
    files = {
        DATA_DIR / "trackers.json": dumps(trackers),
        DATA_DIR / "glossary.json": dumps(glossary),
        DATA_DIR / "sites.json": dumps(sites),
        DATA_DIR / "psl.json": dumps(psl, compact=True),
    }
    index = {"schema": SCHEMA, "data_version": sites["date"].replace("-", "."),
             "list_entries": trackers["entries"], "list_unverified": trackers["unverified"],
             "sites": len(sites["sites"]), "psl_rules": len(psl["rules"]),
             "files": {p.name: hashlib.sha256(t.encode("utf-8")).hexdigest() for p, t in files.items()}}
    files[DATA_DIR / "index.json"] = dumps(index)
    files[PARITY_FILE] = dumps(build_parity(tracker_list, sites, history), compact=True)
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail if the files on disk are not up to date")
    args = ap.parse_args(argv)
    files = build_all()
    if args.check:
        stale = [str(p.relative_to(ROOT)) for p, t in files.items()
                 if not p.exists() or p.read_text(encoding="utf-8") != t]
        if stale:
            print("stale (run extension/tools/build_data.py):", *stale, sep="\n  ")
            return 1
        print("extension data is up to date")
        return 0
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"{path.relative_to(ROOT)}  {len(text.encode('utf-8')):>8} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
