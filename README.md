# Tracker Watch (TraceGuard engine)

Passive, reproducible measurement of third-party tracking on news websites. Every week the engine loads each page like an ordinary visitor, with no clicks and no consent given, and counts the third-party tracking services the page contacts. Reports state measured facts only: they make no legal claims and attribute no intent.

> **Early version (v0.1).** The method and the classification list are small and still changing. Read the method page and its limits before drawing conclusions from any number.

- **Code:** MIT licence (`LICENSE`).
- **Data** (`data/`): CC BY 4.0 (`DATA_LICENSE.md`). Please credit "Tracker Watch".
- **Corrections and suggestions:** open an issue.

## Install and run

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
python -m pytest tests -q          # unit tests, no browser needed
python -m traceguard https://example.com --passes 3 --observe 12 --vantage local-test
python -m traceguard --sites-file data/sites.example.json
```

One JSON report per site is written to `data/runs/<UTC date>/<site>.json`.

## Other commands

```bash
python -m traceguard.diff data/runs                                   # week-over-week changes (stable ones only)
python -m traceguard.posts data/runs --platform bluesky --dry-run     # draft thread, never publishes
python -m traceguard.site data/runs --out site --dashboard dashboard_local   # static site (English) + local Spanish dashboard
python -m traceguard.ondemand --help                                  # one-address analysis requested through an issue
```

The site is static, makes no third-party requests and sets no cookies. `dashboard_local/` is an internal dashboard and is never published. Bands (A 0–2, B 3–5, C 6–9, D 10–14, E 15+) are fixed ranges of the count of tracking services, not a verdict; see `traceguard/bands.py`. The workflow in `.github/workflows/weekly-scan.yml` scans, commits the reports and builds the site; it publishes only when the repository variable `PUBLISH_SITE` is `true`.

## What a scan does

- Validates the URL first: http/https only, ports 80/443/8080/8443, no credentials, host must resolve to public addresses only. Requests the page makes to private or loopback addresses are blocked and recorded.
- Runs several passes (default 3), each in a fresh browser context, and reports the median plus which domains appeared in a majority of passes (`stable`).
- Classifies each request as first or third party by registrable domain (public-suffix list, offline), using the site's own domains plus any `first_party_domains` declared for it.
- Looks hosts up in a tracker list (`data/trackers.seed.json`: a small hand-built starter list, not audited; entries marked `evidence` are inferred).
- Records script behaviour (canvas reads, geolocation requests, WebRTC connections, key listeners) together with the script's domain. These are observations, not accusations.
- Never stores cookie values or URL query strings.
- Identifies itself in the user agent (`Chrome/... Safari/537.36 TraceGuard/<version>`). The default `HeadlessChrome` token is dropped because some CDNs answer it with an error page; the `TraceGuard` token stays. No stealth or evasion. Sites that refuse the request are reported as `blocked`; pages that serve an error page with HTTP 200 are reported as `incomplete`.

## Responsible use

This tool is meant for measuring public pages as an ordinary visitor would load them. Do not use it to probe networks you do not own or to bypass a site's access controls. If you run it elsewhere, run it on a disposable machine with no access to internal services, and keep the request volume low.

## Report layout

`schema_version`, `tool`, `generated_at`, `site`, `measurement` (vantage, locale, timezone, user agent, browser, passes, window, tracker list), `summary` (status, metrics, domains, services, observations), `findings` (code, severity, English text, evidence) and the raw `runs`.

## Known limits

- The browser resolves names again when connecting, so DNS rebinding is not fully covered: run scans on a disposable machine with no internal network access.
- Storage counts cover the main frame only. Script attribution from stack traces cannot tell an inline third-party snippet from first-party code.
- The scan never interacts with consent banners, so it measures the state before any interaction. It does not yet detect the banner itself.
- Results depend on where the scan runs; the vantage is recorded in every report.
- The classification list is small, so every count is a minimum.

## Still open

- A larger, licensed classification list.
- Detection of the consent banner and measurement after consent.
- Publishing drafts to Bluesky and Mastodon (drafts are generated; posting is not implemented).
- Live analysis of any address (a server is needed); a limited on-demand mode through issues is being prepared.
