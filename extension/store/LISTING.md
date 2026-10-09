# Store listing — Tracker Watch Lens (draft, 8 October 2026: see and block)

Texts to paste into the Chrome Web Store, Microsoft Edge Add-ons and Firefox Add-ons (AMO) forms.
Form fields and limits change: check each one against the live form when submitting (step M6).

## Name
Tracker Watch Lens

## Developer and contact (decided by the user, 7 October 2026)
- Developer name: Jose Luis Asenjo Tornero (credit shown in the extension: "Designed by jlasenjo")
- Contact email for the store listings: asenjo.jose@hotmail.com
- Price: free (see the note in PENDIENTES.md about paid listings)

## Short description (Chrome: at most 132 characters; AMO summary: at most 250)
See which trackers a page contacts and what each one did. Block trackers and ads with one click. Lens itself sends nothing.
(123 characters; the same text is the extension's own description in the manifest.)

## Detailed description
Tracker Watch Lens shows you who tracks you on the page you are reading, and lets you stop it.

Open the panel on any page and you see, counted in your own browser, which third-party tracking services the page
contacted, which companies run them and what each one did, in plain words. Two buttons at the top of the panel
block trackers and ads; the page reloads and the panel shows what was stopped.

See:
- How many tracking services were contacted before your first click, and which companies run them.
- What changed after you answered the cookie banner: new services, and the cookies they left (names only).
  Your own banner test compares "reject" and "accept" on the same site.
- Services contacted from inside embedded frames (an ad slot, a video), and trackers disguised as part of the
  site itself (Firefox).
- What your browser told the site: how it describes itself, its language, the cookies and page address sent
  to third parties, and known tracking parameters (names only, never values).
- On a search results page: how your click on a result is recorded.
- How the page compares with the weekly Tracker Watch measurement of news sites.

Stop (optional, off until you press a button):
- Block trackers: your browser stops the tracking services of our list, and, in the extended level, the domains
  of EasyPrivacy. A strict "site only" level also stops other sites' scripts, frames and connections, with an
  "Allow on this site" button for what a page needs.
- Block ads: your browser stops the ad requests of EasyList and hides the empty space they leave.
- Learn trackers that are on no list from what they do (an identifier cookie or a canvas read on three different
  sites) and stop them too (blocked, or their cookies removed when blocking could break a page); off by default, and no site names are kept.
- Reject cookie banners for you: Lens presses the reject button of known banners and tells you so in a small
  notice; it never accepts and never chooses a paid option.
- Remove tracking parameters (gclid, fbclid, utm_...) from the addresses you open.
- Pause it all on a site with one click, if something breaks; when a site asks you to turn off your ad blocker,
  Lens says so and offers that pause in a notice.

What it does not do:
- It sends nothing: no analytics, no account, no server. Its lists and weekly figures are bundled with it.
- It keeps no browsing history: tab reports end with the tab; the optional features keep only counts,
  company and service names, and, for banner tests, the names of the sites you tested.
- It is not complete protection: a dedicated blocker such as uBlock Origin covers more. Lens's strength is
  showing you what happens.

Counts, not verdicts: a request to a tracking service shows that it was contacted, not what was sent or what the
company does with it. The list is limited and some entries are not verified one by one; the panel says which.

Credits: EasyList and EasyPrivacy by The EasyList authors (CC BY-SA 3.0); AdGuard cname-trackers (MIT).

Open source (MIT): https://github.com/joseasenjo/tracker-watch/tree/main/extension
Privacy policy: https://joseasenjo.github.io/tracker-watch/extension-privacy.html

## Category
Chrome: Privacy & Security (or "Tools"). AMO: Privacy & Security. Edge: Productivity / Privacy.

## Single purpose (Chrome form)
Let the user see and control the third-party tracking on the pages they visit: the panel shows which tracking
services a page contacts and what each did, and its optional blocking (trackers, ads, tracking parameters) stops
them. Everything happens locally in the browser.

## Permission justifications (Chrome form; same text for Edge)
- **Host permissions (`<all_urls>`)**: the extension's only function is to describe the third-party requests of
  whatever page the user opens; it needs to observe requests on every site and to run scripts that notice the
  user's first click and the cookie banner. It sends nothing anywhere.
- **webRequest**: to observe (not block or modify) the requests a page makes, to count third-party contacts.
- **webNavigation**: to tell when Chrome loaded a page in the background before the user opened it (prerendering),
  so its requests are attributed to the right page.
- **cookies**: to read the names and lifetimes (never values) of the cookies of contacted services, and to delete
  one site's cookies when the user presses "Clear this site's data and reload".
- **storage**: to keep each tab's report for the session and, if the user enables them, local banner tests and
  a weekly summary.
- **declarativeNetRequestWithHostAccess**: the optional "Block trackers" and "Block ads" buttons, off by default:
  rule sets bundled with the extension stop third-party requests to the tracking services of our list (and, at the
  levels the user chooses, to the domains of EasyPrivacy, to the ads of EasyList and, in the strict mode, to other
  sites' scripts), and remove tracking parameters from page addresses. Rules are static files in the package;
  nothing is downloaded.
- **scripting**: only for the optional "Block ads" setting, off by default: adds a style sheet that hides empty ad
  slots (EasyList's element hiding rules, bundled), never reads or changes the page's content.
- **dns (Firefox only)**: to check whether an address that looks like part of the visited site is a tracking
  company's server in disguise (CNAME cloaking), against a bundled list. Nothing is sent to us.
- **browsingData (optional)**: requested only when the user first presses "Clear this site's data and reload",
  to clear that site's stored data so its cookie banner shows again.
- **Remote code**: none. All code and data are in the package.

## Data usage (Chrome "Privacy practices" tab) — to confirm at submission
The extension does not collect or transmit user data: everything is processed and stored only in the browser.
Expected answers: no data categories collected; certify that data is not sold, not used for unrelated purposes and
not used for creditworthiness. If the form treats local processing of browsing activity as "Web history", say
so explicitly in the justification: "processed locally, never transmitted".

## Firefox (AMO)
- `browser_specific_settings.gecko.data_collection_permissions: {"required": ["none"]}` is already in the manifest.
- Source code: AMO may ask for it because the package is built by `tools/build.py`; the files are copied, not
  minified or bundled, so the package is readable as is.

## Images (made 9 October 2026)
- Icon 128x128: `icons/icon-128.png`.
- Screenshots, 1280x800, in `extension/store/screenshots/`: `01-panel.png` (panel on a news page, counting),
  `02-blocking.png` (blocking on, what was stopped), `03-settings.png` (settings). Made by `make_screenshots.py` with the
  real extension on a real page (the panel is composed beside the page because a toolbar popup cannot be photographed
  from outside the browser). Re-make them whenever the interface changes.
- Chrome small promotional tile 440x280: not made; only needed for the Chrome Web Store.

## Where it is listed (decision of 9 October 2026, to avoid costs for now)
- **Microsoft Edge Add-ons** and **Firefox Add-ons (AMO)**: free; to submit first. The package for each is the release
  zip (`tracker-watch-lens-chrome.zip` for Edge, `tracker-watch-lens-firefox.zip` for AMO); `web-ext lint` on the
  Firefox build: 0 errors, 0 warnings (web-ext 8, 9 October 2026).
- **Chrome Web Store**: "coming soon" on the website; it has a one-time registration fee, so it waits.
- Until the stores approve it, people install it by hand from the GitHub Release (page "Get the extension").
