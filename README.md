# TraceGuard (rewrite, v0.1.0)

Passive, reproducible measurement of third-party tracking on public web pages. It loads a page like an ordinary visitor, never clicks or types, and records what happens during a fixed observation window. Reports state measured facts only; they make no legal claims and attribute no intent.

The previous prototype is kept untouched in `TraceGuard-cookies/` for reference. It is not used by this code.

## Install and run

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
python -m pytest tests -q          # unit tests, no browser needed
python -m traceguard https://example.com --passes 3 --observe 12 --vantage local-test
python -m traceguard --sites-file data/sites.example.json
```

One JSON report per site is written to `data/runs/<UTC date>/<site>.json`.

## What a scan does

- Validates the URL first: http/https only, ports 80/443/8080/8443, no credentials, host must resolve to public addresses only. Requests the page makes to private or loopback addresses are blocked and recorded.
- Runs several passes (default 3), each in a fresh browser context, and reports the median plus which domains appeared in a majority of passes (`stable`).
- Classifies each request as first or third party by registrable domain (public-suffix list, offline), using the site's own domains plus any `first_party_domains` declared for it.
- Looks hosts up in a tracker list (`data/trackers.seed.json`: a small starter list, not audited).
- Records script behaviour (canvas reads, geolocation requests, WebRTC connections, key listeners) together with the script's domain. These are observations, not accusations.
- Never stores cookie values or URL query strings.
- Identifies itself in the user agent (`HeadlessChrome ... TraceGuard/0.1.0`). No stealth or evasion. Sites that refuse the request are reported as `blocked`.

## Report layout

`schema_version`, `tool`, `generated_at`, `site`, `measurement` (vantage, locale, timezone, user agent, browser, passes, window, tracker list), `summary` (status, metrics, domains, services, observations), `findings` (code, severity, English text, evidence) and the raw `runs`.

## Known limits

- The browser resolves names again when connecting, so DNS rebinding is not fully covered: run scans on a disposable machine with no internal network access.
- Storage counts cover the main frame only. Script attribution from stack traces cannot tell an inline third-party snippet from first-party code.
- The scan never interacts with consent banners, so it measures the state before any interaction. It does not yet detect the banner itself.
- Results depend on where the scan runs (GitHub Actions runners are in the US); the vantage is recorded in every report.

## Still open

- Composite privacy score: deliberately not implemented. The methodology is undecided.
- Licensed public tracker list to replace the seed list.
- Week-over-week comparison, post generator for Bluesky and Mastodon, approval gate.
- Final outlet list.
