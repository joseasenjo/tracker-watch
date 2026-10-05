"""Aggregation of repeated passes into one comparable, reproducible report."""
from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime, timezone

from . import SCHEMA_VERSION, __version__
from .classify import TrackerList
from .findings import build_findings


def passive_requests(run: dict) -> list[dict]:
    """Requests of the passive window that actually went out: not after a consent click, not blocked by a list."""
    return [r for r in run["requests"] if r.get("phase", "before") == "before" and not r.get("blocked_by_list")]


def _services(requests: list[dict], tracker_list: TrackerList) -> tuple[dict[str, dict], set[str]]:
    services: dict[str, dict] = {}
    for request in requests:
        tracker = request.get("tracker")
        if tracker:
            services.setdefault(tracker["service"], {"entity": tracker["entity"], "category": tracker["category"]})
    return services, {s for s, v in services.items() if tracker_list.is_tracking(v["category"])}


def summarize_run(run: dict, tracker_list: TrackerList) -> dict:
    requests = passive_requests(run)
    third = [r for r in requests if r.get("party") == "third"]
    services: dict[str, dict] = {}
    for request in third:
        tracker = request.get("tracker")
        if tracker:
            services.setdefault(tracker["service"], {"entity": tracker["entity"], "category": tracker["category"]})
    tracking_requests = [r for r in third if r.get("tracker") and tracker_list.is_tracking(r["tracker"]["category"])]
    sized = [r for r in requests if r.get("bytes") is not None]  # old reports have no sizes
    return {
        "third_party_requests": len(third),
        "tracking_requests": len(tracking_requests),
        # None when no response size was recorded at all (reports made before sizes were collected)
        "total_bytes": sum(r["bytes"] for r in sized) if sized else None,
        "third_party_bytes": sum(r["bytes"] for r in third if r.get("bytes") is not None) if sized else None,
        "tracking_bytes": sum(r["bytes"] for r in tracking_requests if r.get("bytes") is not None) if sized else None,
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
    # high: every pass measured; medium: a majority did; low: a minority (figures are indicative only)
    confidence = "high" if n == len(runs) else ("medium" if n * 2 > len(runs) else "low")
    failed_statuses = dict(Counter(r["status"] for r in runs if r["status"] != "ok"))

    def median(values) -> int:
        return int(statistics.median_low(list(values)))

    def median_or_none(values) -> int | None:
        values = list(values)
        return None if any(v is None for v in values) else median(values)

    domain_counts = Counter(d for s in summaries for d in s["third_party_domains"])
    service_counts = Counter(svc for s in summaries for svc in s["services"])
    service_info = {svc: v for s in summaries for svc, v in s["services"].items()}
    observation_counts = Counter((o["kind"], o["script_domain"], o["party"]) for r in ok for o in r["observations"])
    first_ok = ok[0]

    return {
        **base,
        "status": "ok",
        "confidence": confidence,
        "failed_passes": failed_statuses,
        "http_status": first_ok["http_status"],
        "final_url": first_ok["final_url"],
        "navigation_redirects": first_ok["navigation_redirects"],
        "security_headers": first_ok["security_headers"],
        "tls_protocol": first_ok["tls_protocol"],
        "metrics": {
            "third_party_requests": median(s["third_party_requests"] for s in summaries),
            "third_party_domains": median(len(s["third_party_domains"]) for s in summaries),
            "tracking_requests": median(s["tracking_requests"] for s in summaries),
            "total_bytes": median_or_none(s["total_bytes"] for s in summaries),
            "third_party_bytes": median_or_none(s["third_party_bytes"] for s in summaries),
            "tracking_bytes": median_or_none(s["tracking_bytes"] for s in summaries),
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


def _median(values) -> int:
    return int(statistics.median_low(list(values)))


def _majority(counter: Counter, total: int) -> list[str]:
    return sorted(k for k, c in counter.items() if c * 2 > total)


def aggregate_consent(runs: list[dict], tracker_list: TrackerList) -> dict:
    """Per consent mode: what the detector found and, for passes where the button was pressed, what was
    contacted in the window after the click compared with the passive window of the same pass."""
    result = {}
    for mode in dict.fromkeys(r["mode"] for r in runs if r.get("mode")):
        mode_runs = [r for r in runs if r.get("mode") == mode]
        ok = [r for r in mode_runs if r["status"] == "ok" and "consent" in r]
        outcomes = Counter(r["consent"]["outcome"] for r in ok)
        clicked = [r for r in ok if r["consent"]["outcome"] == "clicked" and not r["consent"].get("navigated_away")]
        if not ok:
            outcome = "not_measured"
        elif len(clicked) * 2 > len(ok):
            outcome = "clicked"
        else:
            outcome = max(outcomes, key=lambda k: (outcomes[k], k != "clicked"))
        entry = {
            "passes": len(mode_runs), "passes_ok": len(ok), "outcome": outcome, "outcomes": dict(outcomes),
            "navigated_away": sum(1 for r in ok if r["consent"].get("navigated_away")),
            "cmp": Counter(r["consent"].get("cmp") for r in ok if r["consent"].get("cmp")).most_common(1)[0][0]
            if any(r["consent"].get("cmp") for r in ok) else None,
            "button_text": Counter(r["consent"].get("button_text") for r in clicked).most_common(1)[0][0]
            if clicked else None,
            "method": Counter(r["consent"].get("method") for r in clicked).most_common(1)[0][0] if clicked else None,
            "passes_clicked": len(clicked),
            "paid_option": Counter(r["consent"].get("paid_option") for r in ok
                                   if r["consent"].get("paid_option")).most_common(1)[0][0]
            if any(r["consent"].get("paid_option") for r in ok) else None,
        }
        if outcome == "clicked":
            info: dict[str, dict] = {}
            after_counts, new_counts = Counter(), Counter()
            rows = []
            for run in clicked:
                before_services, before_tracking = _services(
                    [r for r in passive_requests(run) if r.get("party") == "third"], tracker_list)
                after_third = [r for r in run["requests"] if r.get("phase") == "after" and r.get("party") == "third"]
                after_services, after_tracking = _services(after_third, tracker_list)
                info.update(before_services)
                info.update(after_services)
                after_counts.update(after_tracking)
                new_counts.update(after_tracking - before_tracking)
                rows.append({
                    "before": len(before_tracking), "after": len(after_tracking),
                    "new": len(after_tracking - before_tracking), "requests_after": len(after_third),
                    "cookies_before": sum(1 for c in run["cookies"] if c["party"] == "third"),
                    "cookies_after": sum(1 for c in run.get("cookies_after", []) if c["party"] == "third"),
                })
            n = len(clicked)
            entry.update({
                "after_seconds": clicked[0]["consent"].get("after_seconds"),
                "metrics": {
                    "tracking_services_before": _median(r["before"] for r in rows),
                    "tracking_services_after": _median(r["after"] for r in rows),
                    "tracking_services_new_after": _median(r["new"] for r in rows),
                    "third_party_requests_after": _median(r["requests_after"] for r in rows),
                    "third_party_cookies_before": _median(r["cookies_before"] for r in rows),
                    "third_party_cookies_after": _median(r["cookies_after"] for r in rows),
                },
                "services_after": [{"service": s, **info[s]} for s in _majority(after_counts, n)],
                "services_new_after": [{"service": s, **info[s]} for s in _majority(new_counts, n)],
            })
        result[mode] = entry
    return result


def aggregate_protection(runs: list[dict]) -> dict:
    """Requests aborted because the filter list matched them (passive window only)."""
    ok = [r for r in runs if r["status"] == "ok"]
    if not ok:
        return {}
    blocked = [[r for r in run["requests"] if r.get("blocked_by_list") and r.get("phase", "before") == "before"]
               for run in ok]
    domains = Counter(d for b in blocked for d in {r.get("domain") or r["host"] for r in b})
    return {"blocked_requests": _median(len(b) for b in blocked),
            "blocked_domains": _majority(domains, len(ok))}


def build_report(site: dict, measurement: dict, runs: list[dict], tracker_list: TrackerList) -> dict:
    summary = aggregate_runs(runs, tracker_list)
    if any(r.get("mode") for r in runs):
        summary["consent"] = aggregate_consent(runs, tracker_list)
    if measurement.get("blocklist") and summary["status"] == "ok":
        summary["protection"] = aggregate_protection(runs)
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
