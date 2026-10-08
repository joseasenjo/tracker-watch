# Tracker Watch Lens: privacy policy

Last updated: 7 October 2026. This policy describes version 0.1 of the extension. It is checked against the code
before every release; if the code and this text ever disagree, that is a bug and should be reported.

## In one sentence

Tracker Watch Lens looks at what the page you are viewing loads, shows it to you, and sends nothing anywhere:
no analytics, no accounts, no servers of ours, no third parties.

## What the extension reads, and why

- **The requests a page makes** (address, type, size, whether it was blocked), to count which third parties
  the page contacts. The full address is used in the moment to find the host name and the names of known
  tracking parameters (such as `gclid`); it is not kept.
- **The headers your browser sends** (user agent, language, client hints, Global Privacy Control, Do Not Track,
  the address of the referring page, cookies), to show you "what your browser told this site". Cookie values
  and full referring addresses are never kept: only the number of cookie names and the referring site's domain.
- **The cookies stored in your browser** for the services the page contacted: names and lifetimes only, to
  describe what each service left behind. Values are never read into the extension's reports.
- **On the page itself**, through scripts that only observe:
  - the time of your first click or key press, and, if that click was on a cookie banner, which button
    (reject, accept, pay, other), told apart by the button's label;
  - whether a known cookie banner is on screen (by its documented element id);
  - on a search results page, how result links record a click (counted, never the links themselves);
  - whether a script reads a canvas, asks for your location or opens a WebRTC connection, with the host name
    of that script (these calls are not changed or blocked).
  The extension never clicks, types, changes or removes anything on a page.

## What is kept, where, and for how long

| What | Where | How long |
|---|---|---|
| The report of each open tab (host names, counts, service names, the summaries above) and the tab's journey (site names and counts of the pages opened in it); for your banner test, the site name and time of the last "Clear this site's data" | Session memory of your browser (`storage.session`) | Until the tab is closed or the browser quits |
| **Your own banner test**, only if you turn it on: per site, the date, counts, tracking-service names and cookie names after you answered its banner | Your browser (`storage.local`) | 7, 30 (default) or 90 days, as you choose; deletable at any time |
| **Your week**, only if you turn it on: per day, the number of pages counted and, per company, on how many of them it was contacted. Which pages or sites is not kept | Your browser (`storage.local`) | 35 days; deletable at any time |
| **Learned trackers**, only if you turn learning on: third-party domain names that behaved like trackers, with counts of what they did and dates; for a domain not yet learned, up to 3 short scrambled codes of the sites where it was seen (made with a random value kept in your browser), never the site names, dropped once it is learned | Your browser (`storage.local`) | Until you forget them or delete all your data |

Blocking settings (which list is on, the sites where you paused it, and in the site only mode which other site you allowed on which site) are kept as the browser's own
blocking rules, not as Lens data. Which sections of the panel you leave open is kept in the panel's local
storage. Nothing else is stored. In particular: no full addresses, no search queries, no cookie values, no page content.

"Delete all my data" on the settings page removes everything in `storage.local`. Tab reports end with the tab.

## What is sent, and to whom

Nothing. The extension makes no network requests of its own: its lists and weekly figures are bundled with it.

Two buttons open something only because you press them:
- **Export** buttons save a JSON file on your computer, containing counts and service names.
- **Report a list mistake** opens a pre-filled page on GitHub (github.com) in a new tab, listing the site name
  and the tracking services seen on it. Nothing is submitted unless you submit it yourself on GitHub, where
  GitHub's own privacy policy applies.
- **Links to a search engine's own pages** (for example Google's My Activity), shown on its results page,
  open that page only when you click them.

## Permissions, and why each one is needed

- **Access to all sites** (`<all_urls>`): to see the requests of whatever page you open and to run the observing
  scripts described above. Without it the extension sees nothing. You can withdraw it in your browser's settings.
- **webRequest**: to observe requests (not to block or change them).
- **webNavigation**: to tell when a page was loaded in the background before you opened it (prerendering).
- **cookies**: to read the names and lifetimes of the cookies of the services a page contacted, and to delete a
  site's cookies when you press "Clear this site's data and reload" in your own banner test.
- **storage**: to keep the tab reports and, if you turn them on, your banner tests and your week.
- **declarativeNetRequestWithHostAccess**: for blocking ("Block trackers" and "Block ads"), off by default. When you turn it on, your browser
  itself stops requests to the tracking services of our list (and, in the extended mode, to the domains of
  EasyPrivacy, bundled with the extension; in the site only mode, to scripts, frames and connections of other
  sites, except the ones you allow on a site, which your browser keeps as its own rules) and removes known
  tracking parameters from the address of pages you open; Lens does not read or change those requests.
- **scripting**: only for "Block ads", off by default: your browser adds to each page a style sheet that hides
  empty ad slots (EasyList's element hiding rules, bundled with the extension). Lens does not read or change the
  page's content.
- **dns** (Firefox only): to check whether an address that looks like part of the site you are visiting is in fact a
  tracking company's server in disguise (CNAME cloaking). Firefox looks the name up as it does for the page itself,
  usually from its own cache; the answer is compared with a bundled list and only the address and the company are kept.
- **browsingData** (optional, asked the first time you use "Clear this site's data"): to also clear that site's
  other stored data so its cookie banner shows again. Only the site of the tab is cleared.

## Children, sale of data, advertising

The extension collects no personal data, so it sells none, shares none and shows no advertising.

## Changes and contact

Changes to this policy are listed in the repository's history. Questions and reports: open an issue at
https://github.com/joseasenjo/tracker-watch/issues.

The code is open source (MIT): https://github.com/joseasenjo/tracker-watch/tree/main/extension
