"""Neutral, factual findings. Text states what was measured, never intent or legal conclusions."""
from __future__ import annotations

# Keyboard listeners are recorded in the report data but not turned into findings: nearly every
# page registers some, so reporting them would be noise (first live scans confirmed this).
SCRIPT_BEHAVIOURS = {
    "canvas_read": "read image data from a canvas element",
    "geolocation_request": "requested the device location",
    "webrtc_connection": "created a WebRTC connection",
}
SCRIPT_CAVEAT = "This API is also used for legitimate purposes and does not by itself indicate tracking."


def format_bytes(n: int | float | None) -> str:
    """Compact human size: 820 B, 41 kB, 1.3 MB (decimal units, as network figures usually are)."""
    if n is None:
        return "–"
    if n < 1000:
        return f"{int(n)} B"
    if n < 1_000_000:
        return f"{n / 1000:.0f} kB"
    return f"{n / 1_000_000:.1f} MB"


def _finding(code: str, severity: str, text: str, evidence: dict | None = None) -> dict:
    return {"code": code, "severity": severity, "text": text, "evidence": evidence or {}}


def build_findings(summary: dict, measurement: dict) -> list[dict]:
    status = summary["status"]
    findings: list[dict] = []

    if summary.get("internal_requests_blocked"):
        findings.append(_finding(
            "INTERNAL_REQUESTS_BLOCKED", "notable",
            f"The page attempted {summary['internal_requests_blocked']} request(s) to private or loopback "
            "addresses. TraceGuard blocked them.",
            {"count": summary["internal_requests_blocked"]}))

    if status == "blocked":
        findings.append(_finding(
            "MEASUREMENT_BLOCKED", "info",
            f"The site did not serve the page to the automated browser (HTTP {summary['http_status']}). "
            "No further measurements were taken.", {"http_status": summary["http_status"]}))
        return findings
    if status == "incomplete":
        findings.append(_finding(
            "MEASUREMENT_INCOMPLETE", "info",
            "The page did not finish loading in the automated browser (it may have served a challenge or "
            f"interstitial page): {summary.get('error')}. No figures are reported.",
            {"error": summary.get("error")}))
        return findings
    if status != "ok":
        findings.append(_finding(
            "MEASUREMENT_ERROR", "info",
            "The page could not be measured.", {"error": summary.get("error")}))
        return findings

    window = measurement["observe_seconds"]
    metrics = summary["metrics"]
    if summary["confidence"] != "high":
        failed = ", ".join(f"{n} {s}" for s, n in summary["failed_passes"].items())
        findings.append(_finding(
            "LOW_CONFIDENCE", "notable" if summary["confidence"] == "low" else "info",
            f"Only {summary['passes_ok']} of {summary['passes_total']} passes could be measured ({failed}). "
            + ("The figures come from a minority of passes and are indicative only."
               if summary["confidence"] == "low" else "Figures are medians of the passes that worked."),
            {"confidence": summary["confidence"], "failed_passes": summary["failed_passes"]}))
    findings.append(_finding(
        "THIRD_PARTY_REQUESTS", "info",
        f"During a {window:g}-second window with no user interaction, the page made "
        f"{metrics['third_party_requests']} requests to {metrics['third_party_domains']} third-party domains "
        f"(median of {summary['passes_ok']} passes).",
        {"requests": metrics["third_party_requests"], "domains": metrics["third_party_domains"]}))

    if metrics.get("total_bytes"):
        third, tracked, total = metrics["third_party_bytes"], metrics["tracking_bytes"], metrics["total_bytes"]
        findings.append(_finding(
            "THIRD_PARTY_WEIGHT", "info",
            f"Of {format_bytes(total)} transferred in that window (compressed responses that finished loading), "
            f"{format_bytes(third)} ({round(100 * third / total)}%) came from third-party domains, "
            f"{format_bytes(tracked)} of it from services classified as tracking "
            f"({metrics['tracking_requests']} requests).",
            {"total_bytes": total, "third_party_bytes": third, "tracking_bytes": tracked,
             "tracking_requests": metrics["tracking_requests"]}))

    tracking = [s for s in summary["services"] if s["tracking"] and s["stable"]]
    if tracking:
        shown = ", ".join(f"{s['service']} ({s['entity']}, {s['category'].replace('_', ' ')})" for s in tracking[:10])
        findings.append(_finding(
            "TRACKING_SERVICES", "notable",
            f"{len(tracking)} third-party services classified as advertising, analytics, social or "
            f"audience measurement were contacted before any interaction: {shown}.",
            {"services": tracking}))

    replay = [s for s in summary["services"] if s["category"] == "session_replay" and s["stable"]]
    if replay:
        findings.append(_finding(
            "SESSION_REPLAY_SERVICES", "notable",
            "Domains classified as session-replay services were contacted: "
            + ", ".join(s["service"] for s in replay) + ".", {"services": replay}))

    if metrics["third_party_cookies"]:
        findings.append(_finding(
            "THIRD_PARTY_COOKIES", "notable",
            f"{metrics['third_party_cookies']} cookies were set by third-party domains "
            f"({metrics['cookies_total']} cookies in total).",
            {"third_party": metrics["third_party_cookies"], "total": metrics["cookies_total"]}))

    for obs in summary["observations"]:
        if obs["party"] == "third" and obs["stable"] and obs["kind"] in SCRIPT_BEHAVIOURS:
            findings.append(_finding(
                "SCRIPT_BEHAVIOUR", "info",
                f"A script served from {obs['script_domain']} {SCRIPT_BEHAVIOURS[obs['kind']]}. {SCRIPT_CAVEAT}",
                obs))

    redirects = summary["navigation_redirects"]
    if redirects:
        path = " -> ".join([redirects[0]["from"]] + [r["to"] for r in redirects])
        findings.append(_finding(
            "NAVIGATION_REDIRECTS", "info",
            f"The address redirected {len(redirects)} time(s): {path}", {"redirects": redirects}))

    headers = summary["security_headers"]
    if not headers["strict_transport_security"]:
        findings.append(_finding(
            "NO_HSTS", "info",
            "The main document response did not include a Strict-Transport-Security header."))
    if headers["content_security_policy"] == "absent":
        findings.append(_finding(
            "NO_CSP", "info",
            "The main document response did not include a Content-Security-Policy header or meta tag."))
    return findings
