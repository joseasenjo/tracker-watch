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


CONSENT_ACTION_TEXT = {"reject": "reject", "accept": "accept"}


def consent_findings(consent: dict) -> list[dict]:
    """Findings of the optional consent measurement. "Not found" always means "not found by our detector"."""
    findings = []
    for mode, c in consent.items():
        button = f" (“{c['button_text']}”)" if c.get("button_text") else ""
        tool = f" The consent tool appeared to be {c['cmp']}." if c.get("cmp") else ""
        if c["outcome"] == "clicked":
            m = c["metrics"]
            new = ", ".join(s["service"] for s in c["services_new_after"][:8])
            findings.append(_finding(
                f"CONSENT_{mode.upper()}_CLICKED", "notable" if m["tracking_services_after"] else "info",
                f"After the banner's {mode} button{button} was pressed, {m['tracking_services_after']} tracking "
                f"services were contacted in the following {c['after_seconds']:g} seconds, "
                f"{m['tracking_services_new_after']} of them not seen before the click"
                + (f" ({new})" if new else "") + f". Before the click: {m['tracking_services_before']}. "
                f"Third-party cookies went from {m['third_party_cookies_before']} to {m['third_party_cookies_after']} "
                f"(median of {c['passes_clicked']} passes).{tool}",
                {"mode": mode, **m}))
        elif c["outcome"] == "no_button":
            findings.append(_finding(
                f"CONSENT_{mode.upper()}_NOT_FOUND", "info",
                f"A consent banner was detected, but our detector found no one-click {mode} button on its first "
                f"layer (it may sit behind a settings or options button). Nothing was clicked."
                + (f" The banner offered a paid option instead (“{c['paid_option']}”)." if c.get("paid_option") else "")
                + tool,
                {"mode": mode, "outcomes": c["outcomes"]}))
        elif c["outcome"] == "no_banner":
            findings.append(_finding(
                f"CONSENT_NO_BANNER", "info",
                "Our detector found no consent banner from this origin, so nothing was clicked.",
                {"mode": mode}))
        else:
            findings.append(_finding(
                f"CONSENT_{mode.upper()}_NOT_MEASURED", "info",
                f"The {mode} button could not be pressed reliably in most passes.",
                {"mode": mode, "outcomes": c.get("outcomes", {})}))
    return [f for i, f in enumerate(findings) if f["code"] != "CONSENT_NO_BANNER"
            or all(g["code"] != "CONSENT_NO_BANNER" for g in findings[:i])]


def protection_findings(protection: dict, measurement: dict, metrics: dict) -> list[dict]:
    if not protection:
        return []
    info = measurement["blocklist"]
    return [_finding(
        "PROTECTION_LIST_APPLIED", "info",
        f"With the {info['name']} filter list applied (domain rules only, {info['rules_applied']} rules), "
        f"{protection['blocked_requests']} requests were blocked. The page still made "
        f"{metrics['third_party_requests']} requests to {metrics['third_party_domains']} third-party domains, "
        f"including {metrics['tracking_services']} tracking services in our list.",
        {"blocked_requests": protection["blocked_requests"], "blocked_domains": protection["blocked_domains"]})]


def build_findings(summary: dict, measurement: dict) -> list[dict]:
    findings = _passive_findings(summary, measurement)
    if summary["status"] == "ok":
        findings += protection_findings(summary.get("protection", {}), measurement, summary["metrics"])
    findings += consent_findings(summary.get("consent", {}))
    return findings


def _passive_findings(summary: dict, measurement: dict) -> list[dict]:
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
