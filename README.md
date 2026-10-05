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
python -m traceguard.community check data/community                   # validate community-contributed origins
python -m traceguard.community add my-scan/2026-10-05 --slug brazil-home --label Brazil --contributor handle
```

## Two separate tests (never part of the weekly ranking)

```bash
# After the banner: press the consent banner's reject / accept button once and record the next window
python -m traceguard --sites-file data/sites.json --consent reject,accept --vantage local-windows-spain   # -> data/consent/<vantage>/
# With a blocking list: block what a public filter list targets (domain rules only) and compare
curl -sSfL https://easylist.to/easylist/easyprivacy.txt -o /tmp/easyprivacy.txt   # not stored in the repository
python -m traceguard --sites-file data/sites.json --blocklist /tmp/easyprivacy.txt --blocklist-name EasyPrivacy \
    --blocklist-url https://easylist.to/easylist/easyprivacy.txt --vantage github-actions-us                # -> data/protected/<vantage>/
```

The consent detector (`traceguard/consent.py`) tries known consent tools by their button ids, then exact button texts in five languages inside a cookie/consent/privacy block. It presses one button, never follows a link off the page and never presses a paid option; "not found" means "not found by our detector". The blocking test (`traceguard/blocker.py`) applies only `||domain^` rules with party and resource-type options and records the list's name, address and SHA-256. Neither writes to `data/runs`.

## Public requests: analyse an address, create a short link (no approval)

Anyone with a GitHub account can open an issue from the site's *Try it* page. A workflow (`.github/workflows/requests.yml`) answers it automatically:

- **Analyse an address** (`traceguard/ondemand.py`): validates the address (public sites only), applies the limits, measures it once, replies in the issue and closes it. The result is not added to the ranking.
- **Short link** (`traceguard/links.py request`): validates the address and code (no other shorteners, no blocked domains, no private addresses), measures the target, adds it to `data/links.json`, pushes, and starts `publish-site.yml` to republish the site. The link page always shows the destination and never redirects by itself. Remove one with `python -m traceguard.links remove CODE`.
- **Limits** live in `data/limits.json`: per GitHub account (requests per window, minimum account age), a daily cap for the whole site, a total cap for links, an on/off switch per feature and a list of blocked accounts. A broken file pauses both features instead of lifting the limits. The public pages show the current values.
- **Edit the limits from the local dashboard:**

```bash
python -m traceguard.site data/runs --out site --dashboard dashboard_local
python -m traceguard.admin          # opens http://127.0.0.1:8765/ ; saves data/limits.json, shows the last 24 h of requests
python -m traceguard.limits set scan.per_account=1 links.enabled=false     # same thing from the command line
```

The admin server listens on 127.0.0.1 only, needs a token for changes and never commits or pushes: commit `data/limits.json` yourself for the workflows to use it. Issue text never reaches a shell command (it travels through environment variables and files), and the labels `scan-request` and `link-request` are created by the workflow on the first request.

`traceguard/categories.py` holds the plain-language explanation of each category shown on the *What the categories mean* page (the type of service, not any company, and no invasiveness score).

## Filter lists for ad and tracker blockers

`python -m traceguard.filterlist --out some/folder` (the site build does it too, into `data/`) writes `trackerwatch-verified.txt` and `trackerwatch-full.txt` in Adblock syntax (uBlock Origin, AdGuard): only the tracking categories, never tag managers, consent tools or paywalls, every rule `||domain^$third-party`. The verified file has only entries verified one by one; the full file adds the ones marked as inferred, listed separately at the end. They do nothing by themselves and are not complete protection; see the site's *Filter lists* page.

## What the site adds beyond the ranking

- **Weight of third-party content:** every request records the transfer size of its response (compressed, if it finished inside the window); reports show the third-party share and the part from tracking services. Reports made before schema 0.2 have no sizes.
- **Reach by company** (`traceguard/entities.py`, the *Companies* page and `entities.csv`): on how many measured sites an operator's tracking services were contacted. Operators are named as in the list, not merged.
- **Downloads** (`data/latest.csv`, `history.csv`, `entities.csv`, `index.json` plus the raw JSON of every origin), all CC BY 4.0.
- **Community origins** (`traceguard/community.py`): anyone can measure the same list from their own country and send the reports in a pull request. Files are validated, their classification is recomputed from the raw request log with our list, and the origin is always labelled "community, unverified" and kept out of the weekly ranking.

The site is static, makes no third-party requests and sets no cookies. `dashboard_local/` is an internal dashboard and is never published. Bands (A 0–2, B 3–9, C 10–24, D 25–49, E 50+) are fixed ranges of the count of tracking services, not a verdict; see `traceguard/bands.py`. The workflow in `.github/workflows/weekly-scan.yml` scans, commits the reports and builds the site; it publishes only when the repository variable `PUBLISH_SITE` is `true`.

## What a scan does

- Validates the URL first: http/https only, ports 80/443/8080/8443, no credentials, host must resolve to public addresses only. Requests the page makes to private or loopback addresses are blocked and recorded.
- Runs several passes (default 3), each in a fresh browser context, and reports the median plus which domains appeared in a majority of passes (`stable`).
- Classifies each request as first or third party by registrable domain (public-suffix list, offline), using the site's own domains plus any `first_party_domains` declared for it.
- Looks hosts up in a tracker list (`data/trackers.seed.json`: a limited hand-built list, not audited; entries marked `evidence` are inferred).
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
- The weekly scan never interacts with consent banners, so it measures the state before any interaction. The separate consent test presses one banner button and depends on a detector that can miss banners or buttons.
- Results depend on where the scan runs; the vantage is recorded in every report.
- The classification list is limited, so every count is a minimum.

## Still open

- A larger, licensed classification list.
- Running the two separate tests on a schedule, from more than one origin.
- Publishing drafts to Bluesky and Mastodon (drafts are generated; posting is not implemented).
- Live analysis of any address without a GitHub issue (needs a server); today it runs through issues and is not instant.
- A page per company with sourced descriptions (phase 2 of the explanations; phase 1 is the category glossary).
