# Store listing — Tracker Watch Lens (draft, 7 October 2026)

Texts to paste into the Chrome Web Store, Microsoft Edge Add-ons and Firefox Add-ons (AMO) forms.
Form fields and limits change: check each one against the live form when submitting (step M6).

## Name
Tracker Watch Lens

## Developer and contact (decided by the user, 7 October 2026)
- Developer name: Jose Luis Asenjo Tornero (credit shown in the extension: "Designed by jlasenjo")
- Contact email for the store listings: asenjo.jose@hotmail.com
- Price: free (see the note in PENDIENTES.md about paid listings)

## Short description (Chrome: at most 132 characters; AMO summary: at most 250)
See which tracking services the page you are reading contacts, before and after you answer its cookie banner. Nothing is sent anywhere.

## Detailed description
Tracker Watch Lens counts, on the page you are viewing and in your own browser, which third-party tracking
services the page contacts, explains in plain words what each one did, and compares the result with the weekly
Tracker Watch measurement of news sites.

What it shows:
- How many tracking services were contacted before your first click, and which companies run them.
- What changed after you answered the cookie banner: new services, and the cookies they left (names only).
- What your browser told the site: how it describes itself, its language, the cookies and page address sent
  to third parties, and known tracking parameters (names only, never values).
- On a search results page: how your click on a result is recorded, and whether it was announced with a ping.
- How much of the page's third-party traffic our filter lists would stop (a simulation: nothing is blocked).
- Optional: your own before/after banner tests, and a weekly summary of the companies that reached you.

What it does not do:
- By default it does not block, change or click anything. An optional clean mode stops the tracking services of
  our small list; it is not complete protection, and the panel says so.
- It sends nothing: no analytics, no account, no server. Its lists and weekly figures are bundled with it.
- It keeps no browsing history: tab reports end with the tab; the optional features keep only counts,
  company and service names, and, for banner tests, the names of the sites you tested.

Counts, not verdicts: a request to a tracking service shows that it was contacted, not what was sent or what the
company does with it. The list is limited and some entries are not verified one by one; the panel says which.

Open source (MIT): https://github.com/joseasenjo/tracker-watch/tree/main/extension
Privacy policy: [URL of the published PRIVACY page — to fill in at M6]

## Category
Chrome: Privacy & Security (or "Tools"). AMO: Privacy & Security. Edge: Productivity / Privacy.

## Single purpose (Chrome form)
Show the user which tracking services the page they are viewing contacts, and explain what each did, locally.

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
- **declarativeNetRequestWithHostAccess**: optional clean mode, off by default: rule sets bundled with the
  extension stop third-party requests to the tracking services of our list and remove tracking parameters from
  page addresses. Rules are static files in the package; nothing is downloaded.
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

## Images still needed (M4/M6)
- Icon 128x128: `icons/icon-128.png` (exists).
- Screenshots: Chrome 1280x800 or 640x400 (1 to 5); AMO and Edge accept similar sizes. Take them on real sites in
  the user's browser (panel open on a news site, the banner test, the settings page). Not from the test fixtures.
- Chrome small promotional tile 440x280 (optional but recommended).
