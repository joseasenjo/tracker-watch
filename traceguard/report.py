"""Aggregation of repeated passes into one comparable, reproducible report."""
from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime, timezone

from . import SCHEMA_VERSION, __version__
from .classify import TrackerList
from .findings import build_findings


def summarize_run(run: dict, tracker_list: TrackerList) -> dict:
    third = [r for r in run["requests"] if r["party"] == "third"]
    services: dict[str, dict] = {}
    for request in third:
        tracker = request.get("tracker")
        if tracker:
            services.setdefault(tracker["service"], {"entity": tracker["entity"], "category": tracker["category"]})
    return {
        "third_party_requests": len(third),
        "third_party_domains": {r["domain"] for r in third},
        "services": services,
        "tracking_services": {s for s, v in services.items() if tracker_list.is_tracking(v["category"])},
        "third_party_cookies": sum(1 for c in run["cookies"] if c["party"] == "third"),
        "cookies_total": len(run["cookies"]),
        "storage_items": run["storage_items"]["local"] + run["storage_items"]["session"],
    }


def aggregate_runs(runs: list[dict], tracker_list: TrackerList) -> dict:
    ok = [r for r in runs if r["status"] == "ok"]
    base = {"passes_total": len(runs), "passes_ok": len(ok)}
    if not ok:
        statuses = [r["status"] for r in runs]
        first = runs[0] if runs else {}
        return {**base, "status": max(set(statuses), key=statuses.count) if statuses else "error",
                "http_status": first.get("http_status"), "error": first.get("error"),
                "final_url": first.get("final_url"),
                "internal_requests_blocked": sum(len(r["internal_requests_blocked"]) for r in runs)}

    summaries = [summarize_run(r, tracker_list) for r in ok]
    n = len(ok)

    def median(values) -> int:
        return int(statistics.median_low(list(values)))

    domain_counts = Counter(d for s in summaries for d in s["third_party_domains"])
    service_counts = Counter(svc for s in summaries for svc in s["services"])
    service_info = {svc: v for s in summaries for svc, v in s["services"].items()}
    observation_counts = Counter((o["kind"], o["script_domain"], o["party"]) for r in ok for o in r["observations"])
    first_ok = ok[0]

    return {
        **base,
        "status": "ok",
        "http_status": first_ok["http_status"],
        "final_url": first_ok["final_url"],
        "navigation_redirects": first_ok["navigation_redirects"],
        "security_headers": first_ok["security_headers"],
        "tls_protocol": first_ok["tls_protocol"],
        "metrics": {
            "third_party_requests": median(s["third_party_requests"] for s in summaries),
            "third_party_domains": median(len(s["third_party_domains"]) for s in summaries),
            "tracking_services": median(len(s["tracking_services"]) for s in summaries),
            "third_party_cookies": median(s["third_party_cookies"] for s in summaries),
            "cookies_total": median(s["cookies_total"] for s in summaries),
            "storage_items": median(s["storage_items"] for s in summaries),
        },
        "third_party_domains": [
            {"domain": d, "passes_seen": c, "stable": c * 2 > n} for d, c in sorted(domain_counts.items())],
        "services": [
            {"service": svc, "entity": service_info[svc]["entity"], "category": service_info[svc]["category"],
             "tracking": tracker_list.is_tracking(service_info[svc]["category"]),
             "passes_seen": c, "stable": c * 2 > n}
            for svc, c in sorted(service_counts.items())],
        "observations": [
            {"kind": k, "script_domain": d, "party": p, "passes_seen": c, "stable": c * 2 > n}
            for (k, d, p), c in sorted(observation_counts.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))],
        "internal_requests_blocked": sum(len(r["internal_requests_blocked"]) for r in runs),
    }


def build_report(site: dict, measurement: dict, runs: list[dict], tracker_list: TrackerList) -> dict:
    summary = aggregate_runs(runs, tracker_list)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "traceguard", "version": __version__},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "site": site,
        "measurement": measurement,
        "summary": summary,
        "findings": build_findings(summary, measurement),
        "runs": runs,
    }
