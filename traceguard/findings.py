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
    if status != "ok":
        findings.append(_finding(
            "MEASUREMENT_ERROR", "info",
            "The page could not be measured.", {"error": summary.get("error")}))
        return findings

    window = measurement["observe_seconds"]
    metrics = summary["metrics"]
    findings.append(_finding(
        "THIRD_PARTY_REQUESTS", "info",
        f"During a {window:g}-second window with no user interaction, the page made "
        f"{metrics['third_party_requests']} requests to {metrics['third_party_domains']} third-party domains "
        f"(median of {summary['passes_ok']} passes).",
        {"requests": metrics["third_party_requests"], "domains": metrics["third_party_domains"]}))

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
