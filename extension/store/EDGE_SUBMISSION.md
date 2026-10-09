# Edge Add-ons submission: everything to paste (made 9 October 2026)

Follow the numbered steps of the guide in the chat; each field below says where it goes. Official process:
https://learn.microsoft.com/en-us/microsoft-edge/extensions/publish/publish-extension

## 0. Files
- Package: `tracker-watch-lens-chrome.zip` from https://github.com/joseasenjo/tracker-watch/releases/tag/lens-v0.1.0
- Logo: `extension/icons/icon-128.png` (128x128, the minimum; 300x300 recommended)
- Screenshots (1280x800): `extension/store/screenshots/01-panel.png`, `02-blocking.png`, `03-settings.png`

## 1. Availability
Visibility: Public. Markets: all markets (default).

## 2. Properties
- Category: Productivity (Edge has no privacy category).
- Website: https://joseasenjo.github.io/tracker-watch/extension.html
- Support contact detail: asenjo.jose@hotmail.com
- Mature content: not checked.

## 3. Privacy
**Single Purpose Description**

Let the user see and control the third-party tracking on the pages they visit: the panel shows which tracking
services a page contacts and what each did, and its optional blocking (trackers, ads, tracking parameters) stops
them. Everything happens locally in the browser.

**Permission justification** (one box per permission the form lists; paste the matching line)

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
- **browsingData (optional)**: requested only when the user first presses "Clear this site's data and reload",
  to clear that site's stored data so its cookie banner shows again.
- **Remote code**: none. All code and data are in the package.

**Are you using remote code?** No, I am not using remote code.

**Data usage**: select no data category (the extension collects and transmits nothing). If a box about "web history" or
"website content" seems to apply, it is processed only inside the browser and never transmitted: do not tick it, and say so in the
certification notes. Tick the three certification boxes (not sold, not used for unrelated purposes, not used for creditworthiness).

**Privacy Policy URL**: https://joseasenjo.github.io/tracker-watch/extension-privacy.html

## 4. Store listing (English)
- Extension name: Tracker Watch Lens (comes from the package)
- Short description (comes from the package): See which trackers a page contacts and what each one did. Block trackers and ads with one click. Lens itself sends nothing.
- Description (250 to 10,000 characters; paste this text):

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

- Extension logo: icon-128.png
- Screenshots: the three PNGs, in this order.
- Search terms (optional): tracker, privacy, ad blocker, cookies, tracking, third-party

## 5. Notes for certification

Tracker Watch Lens needs no account and no test credentials. It sends nothing anywhere.
To see it work: open any news website (for example a national newspaper's home page), wait about ten seconds, and click the toolbar icon. The panel shows how many third-party tracking services the page contacted before the first click, which companies run them, and what changed after the cookie banner.
Blocking is OFF until the user presses "Block trackers" or "Block ads" at the top of the panel; the page then reloads and the panel shows what was stopped. "Reject banners" and "Learn trackers" are optional and off by default. Settings are in the "Settings" link of the panel.
The extension is built from public source code: https://github.com/joseasenjo/tracker-watch/tree/main/extension . The package is the one in the GitHub Release lens-v0.1.0 (SHA-256 sums are listed there). Privacy policy: https://joseasenjo.github.io/tracker-watch/extension-privacy.html
There is no remote code: all code and rule lists are in the package.
