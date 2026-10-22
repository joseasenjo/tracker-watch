"""What the series says beyond the medians: who changed, what is noise, which companies travel together.

Everything is deterministic and computed from the reports alone (no model, no guess): every sentence the site shows can be
traced to numbers in data/runs. Only scans taken under the same conditions are compared. With few scans the results are
labelled preliminary instead of pretending to be firm.

  * stability     a site is "stable" when its count moved within a narrow band across the scans, "variable" otherwise;
  * anomalies     a big jump between the last two scans is checked against the site's own history, against how many
                  third-party requests the page made (a page that loaded only partly is a likely measurement artifact)
                  and against persistence (a change is "confirmed" only when the next scan keeps it);
  * what changed  the biggest movers among sites and among companies between the last two scans;
  * families      groups of sites with a similar set of companies (Jaccard similarity, average-linkage clustering), each
                  described by the companies that mark it. It describes co-presence on these news sites, not any
                  business relationship.
"""
from __future__ import annotations

import statistics

PRELIMINARY_BELOW = 8          # scans: fewer than this and the page says the patterns are preliminary
MIN_SCANS_FOR_STABILITY = 3
JUMP_MIN, JUMP_RATIO = 15, 0.5  # the same rule as the alert posts: at least 15 services and half of the larger count
ARTIFACT_REQUESTS_DROP = 0.7    # third-party requests fell by this share or more: the page probably loaded only partly
FAMILY_MIN_SIZE, FAMILY_MIN_COUNT, FAMILY_MAX_COUNT = 3, 3, 6
SIGNATURE_MIN_INSIDE, SIGNATURE_MIN_LIFT = 0.6, 0.15  # a company marks a family: on 60% of its sites and 15 points above its overall reach


def tolerance(value: float) -> float:
    """How much a count may move and still count as 'the same level': 3 services or a quarter of the value."""
    return max(3.0, 0.25 * abs(value))


def preliminary(scans: int) -> bool:
    return scans < PRELIMINARY_BELOW


# --- series per company and per category -------------------------------------------------------------------------

def company_series(points: list[dict], top: int = 5) -> list[tuple[str, list[tuple[str, float | None]]]]:
    """Reach (% of the usable sites) of the companies that lead the newest scan, one value per scan."""
    if not points:
        return []
    leaders = [c for c, _ in sorted(points[-1]["reach_all"].items(), key=lambda kv: (-kv[1], kv[0]))[:top]]
    return [(company, [(p["date"], round(100 * p["reach_all"].get(company, 0) / p["usable"], 1) if p["usable"] else None)
                       for p in points]) for company in leaders]


def category_series(points: list[dict], labels: dict[str, str] | None = None) -> list[tuple[str, list[tuple[str, float | None]]]]:
    """Share of the usable sites on which at least one service of each category ran, one value per scan; the categories
    of the newest scan, widest first."""
    if not points:
        return []
    order = [c for c, _ in sorted(points[-1]["categories"].items(), key=lambda kv: (-kv[1], kv[0]))]
    labels = labels or {}
    return [(labels.get(category, category.replace("_", " ").capitalize()),
             [(p["date"], round(100 * p["categories"].get(category, 0) / p["usable"], 1) if p["usable"] else None) for p in points])
            for category in order]


# --- stability and anomalies -------------------------------------------------------------------------------------

def site_histories(points: list[dict]) -> dict[str, list[dict]]:
    """stem -> [{date, tracking, requests, name, group}] in scan order, from the usable sites of each scan."""
    histories: dict[str, list[dict]] = {}
    for p in points:
        for row in p["site_rows"]:
            histories.setdefault(row["stem"], []).append({"date": p["date"], **row})
    return histories


def stability(points: list[dict]) -> dict:
    """Per site with enough scans: stable or variable, from how far its count moved. Also the counts of each."""
    rows = []
    for stem, history in site_histories(points).items():
        values = [h["tracking"] for h in history]
        if len(values) < MIN_SCANS_FOR_STABILITY:
            continue
        median, spread = statistics.median(values), max(values) - min(values)
        label = "stable" if spread <= tolerance(median) else "variable"
        rows.append({"stem": stem, "name": history[-1]["name"], "group": history[-1]["group"], "scans": len(values),
                     "median": round(median, 1), "min": min(values), "max": max(values), "spread": spread, "label": label})
    rows.sort(key=lambda r: (r["label"] != "variable", -r["spread"], r["name"]))
    return {"rows": rows, "stable": sum(1 for r in rows if r["label"] == "stable"),
            "variable": sum(1 for r in rows if r["label"] == "variable"), "min_scans": MIN_SCANS_FOR_STABILITY}


def anomalies(points: list[dict]) -> list[dict]:
    """Big jumps between the last two scans, each with its verification: outside the site's usual range?, did the
    third-party requests fall with it?, did the next scans keep the change?"""
    if len(points) < 2:
        return []
    histories = site_histories(points)
    found = []
    # the jump of the last scan, and the one before it (which the last scan can already confirm or reverse)
    for end in range(len(points) - 1, max(len(points) - 3, 0), -1):
        last_date, prev_date = points[end]["date"], points[end - 1]["date"]
        found += _jumps(histories, prev_date, last_date, points[-1]["date"])
    found.sort(key=lambda a: (-abs(a["delta"]), a["name"]))
    return found


def _jumps(histories: dict[str, list[dict]], prev_date: str, last_date: str, newest_date: str) -> list[dict]:
    found = []
    for stem, history in histories.items():
        by_date = {h["date"]: h for h in history}
        if last_date not in by_date or prev_date not in by_date:
            continue
        new, old = by_date[last_date], by_date[prev_date]
        delta = new["tracking"] - old["tracking"]
        if abs(delta) < JUMP_MIN or abs(delta) < JUMP_RATIO * max(new["tracking"], old["tracking"]):
            continue
        earlier = [h["tracking"] for h in history if h["date"] < prev_date]
        usual = (min(earlier) - tolerance(min(earlier)), max(earlier) + tolerance(max(earlier))) if len(earlier) >= 2 else None
        outside = None if usual is None else not (usual[0] <= new["tracking"] <= usual[1])
        requests_change = None if not old["requests"] else (new["requests"] - old["requests"]) / old["requests"]
        run = 0  # scans, from the jump on, that stay at the new level
        for h in history:
            if h["date"] < last_date:
                continue
            if abs(h["tracking"] - new["tracking"]) <= tolerance(new["tracking"]):
                run += 1
            else:
                break
        later = last_date != newest_date
        artifact = bool(requests_change is not None and abs(requests_change) >= ARTIFACT_REQUESTS_DROP and (delta < 0) == (requests_change < 0))
        if run >= 2:
            verdict, text = "confirmed", f"the new level has held for {run} scans"
        elif later:
            verdict, text = "reverted", "the next scan went back to the earlier level: a one-scan variation, not a change"
        elif artifact:
            verdict = "probable_artifact"
            text = (f"third-party requests {'fell' if requests_change < 0 else 'rose'} by {abs(round(100 * requests_change))}% too: "
                    "the page may have loaded differently (a consent wall, a bot check or a partial load); treat it as a measurement "
                    "artifact until the next scan confirms it")
        else:
            verdict, text = "unconfirmed", "seen in one scan only: wait for the next scan before reading it as a real change"
        found.append({"stem": stem, "name": new["name"], "group": new["group"], "from": old["tracking"], "to": new["tracking"],
                      "delta": delta, "requests_change_pct": None if requests_change is None else round(100 * requests_change),
                      "outside_usual": outside, "run": run, "verdict": verdict, "explanation": text,
                      "since": prev_date, "until": last_date})
    return found


# --- what changed ------------------------------------------------------------------------------------------------

def what_changed(points: list[dict], top: int = 3) -> dict | None:
    """The biggest movers between the last two scans: sites (flagged when the move is an anomaly) and companies."""
    if len(points) < 2:
        return None
    new, old = points[-1], points[-2]
    flagged = {a["stem"]: a for a in anomalies(points) if a["until"] == new["date"]}
    old_rows = {r["stem"]: r for r in old["site_rows"]}
    moves = []
    for row in new["site_rows"]:
        before = old_rows.get(row["stem"])
        if before and row["tracking"] != before["tracking"]:
            moves.append({"stem": row["stem"], "name": row["name"], "from": before["tracking"], "to": row["tracking"],
                          "delta": row["tracking"] - before["tracking"], "verdict": flagged.get(row["stem"], {}).get("verdict")})
    up = sorted((m for m in moves if m["delta"] > 0), key=lambda m: (-m["delta"], m["name"]))[:top]
    down = sorted((m for m in moves if m["delta"] < 0), key=lambda m: (m["delta"], m["name"]))[:top]
    companies = []
    for company in set(new["reach_all"]) | set(old["reach_all"]):
        a, b = old["reach_all"].get(company, 0), new["reach_all"].get(company, 0)
        if b != a and max(a, b) >= 3:
            companies.append({"company": company, "from": a, "to": b, "delta": b - a})
    gained = sorted((c for c in companies if c["delta"] > 0), key=lambda c: (-c["delta"], c["company"]))[:top]
    lost = sorted((c for c in companies if c["delta"] < 0), key=lambda c: (c["delta"], c["company"]))[:top]
    return {"from": old["date"], "to": new["date"], "median_from": old["median_tracking"], "median_to": new["median_tracking"],
            "sites_up": up, "sites_down": down, "companies_gained": gained, "companies_lost": lost,
            "sites_compared": len(set(old_rows) & {r["stem"] for r in new["site_rows"]})}


# --- families of sites by tracker stack --------------------------------------------------------------------------

def _jaccard(a: set, b: set) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _cluster(names: list[str], sim: dict, threshold: float) -> list[list[str]]:
    """Average-linkage agglomeration: clusters merge while their average similarity is at least `threshold`.
    Deterministic: ties are broken by position, and `names` is already sorted."""
    clusters = [[n] for n in names]
    while len(clusters) > 1:
        best, pair = threshold, None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                avg = statistics.mean(sim[(a, b)] for a in clusters[i] for b in clusters[j])
                if avg > best or (pair is None and avg == best):
                    best, pair = avg, (i, j)
        if pair is None:
            break
        i, j = pair
        clusters[i] = clusters[i] + clusters[j]
        del clusters[j]
    return clusters


def families(point: dict | None, *, min_size: int = FAMILY_MIN_SIZE) -> dict | None:
    """Group the sites of one scan by the set of companies they run, and describe each group by the companies that mark
    it (its 'signature'). Sites with similar stacks (Jaccard similarity of their company sets) end up together; the
    similarity threshold is the highest one that gives between 3 and 6 families of at least `min_size` sites. It describes
    co-presence on these sites, not any relationship between the companies."""
    if not point:
        return None
    by_site: dict[str, set] = {}
    for company, stems in point["presence"].items():
        for stem in stems:
            by_site.setdefault(stem, set()).add(company)
    names = sorted(by_site)
    if len(names) < 2 * min_size:
        return None
    sim = {(a, b): _jaccard(by_site[a], by_site[b]) for a in names for b in names}
    chosen = None
    for step in range(16, 3, -1):  # thresholds 0.80, 0.75, ... 0.20
        threshold = step * 0.05
        clusters = _cluster(names, sim, threshold)
        big = [c for c in clusters if len(c) >= min_size]
        if FAMILY_MIN_COUNT <= len(big) <= FAMILY_MAX_COUNT:
            chosen = (threshold, clusters)
            break
    if chosen is None:
        threshold = 0.5
        chosen = (threshold, _cluster(names, sim, threshold))
    threshold, clusters = chosen
    labels = {r["stem"]: r for r in point["site_rows"]}
    total = len(names)
    overall = {c: len(stems) / total for c, stems in point["presence"].items()}
    families_out, rest = [], []
    for members in sorted(clusters, key=lambda c: (-len(c), c[0])):
        if len(members) < min_size:
            rest.extend(members)
            continue
        pct = {}
        for company in overall:
            inside = sum(1 for stem in members if company in by_site[stem]) / len(members)
            pct[company] = inside
        signature = sorted(((c, v) for c, v in pct.items() if v >= SIGNATURE_MIN_INSIDE and v - overall[c] >= SIGNATURE_MIN_LIFT),
                           key=lambda kv: (-(kv[1] - overall[kv[0]]), kv[0]))[:6]
        counts = [labels[m]["tracking"] for m in members]
        groups: dict[str, int] = {}
        for m in members:
            groups[labels[m]["group"] or "?"] = groups.get(labels[m]["group"] or "?", 0) + 1
        lead_group, lead_n = sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        families_out.append({
            "id": f"F{len(families_out) + 1}", "size": len(members), "sites": sorted(labels[m]["name"] for m in members),
            "median_tracking": round(statistics.median(counts), 1), "min_tracking": min(counts), "max_tracking": max(counts),
            "groups": dict(sorted(groups.items())), "lead_group": lead_group, "lead_group_n": lead_n,
            "signature": [{"company": c, "pct_inside": round(100 * v), "pct_overall": round(100 * overall[c])} for c, v in signature],
            "pct": pct})
    rest_names = sorted(labels[m]["name"] for m in rest)
    shown = sorted({m["company"] for f in families_out for m in f["signature"]} |
                   {c for c, _ in sorted(overall.items(), key=lambda kv: (-kv[1], kv[0]))[:8]})
    shown.sort(key=lambda c: (-overall[c], c))
    heat = [[round(100 * f["pct"].get(c, 0)) for f in families_out] for c in shown]
    for f in families_out:
        f.pop("pct")
    return {"date": point["date"], "sites": total, "threshold": round(threshold, 2), "families": families_out,
            "rest": rest_names, "companies": shown, "heat": heat, "overall": [round(100 * overall[c]) for c in shown]}


# --- everything together -----------------------------------------------------------------------------------------

def analyse(points: list[dict], category_labels: dict[str, str] | None = None) -> dict:
    """All the extra analysis for the comparable scans of the series, in one dict for the page."""
    return {
        "scans": len(points), "preliminary": preliminary(len(points)), "min_scans": PRELIMINARY_BELOW,
        "company_series": company_series(points), "category_series": category_series(points, category_labels),
        "stability": stability(points), "anomalies": anomalies(points), "changed": what_changed(points),
        "families": families(points[-1] if points else None),
        "quartiles": points[-1]["quartiles"] if points else {},
        "bands": [(p["date"], p["bands"]) for p in points],
    }


def sites_rows(points: list[dict]) -> list[list]:
    """One row per site and scan, for the CSV: date, site, country group, tracking services and third-party requests."""
    return [[p["date"], r["stem"], r["name"], r["group"], r["tracking"], r["requests"]] for p in points for r in p["site_rows"]]


SITES_COLUMNS = ["date", "site_id", "name", "group", "tracking_services", "third_party_requests"]
