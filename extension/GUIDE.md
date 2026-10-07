# How to browse with less tracking

Checked on 7 October 2026 against each maker's own pages (listed at the end). Browsers and extensions change:
if a step no longer matches what you see, trust the maker's page and tell us.

No single step stops all tracking, and none of them hides your IP address from the sites you visit. Each step
below removes a part. Tracker Watch Lens shows the effect on every page: open its panel and look at
**Your protection** ("stopped N of the M tracking services met here").

## 1. Use your browser's own protection

This is the step that needs no installation.

- **Firefox**: Enhanced Tracking Protection is on by default (Standard). Standard blocks social media trackers,
  cross-site tracking cookies, cryptominers and fingerprinters, and keeps every cookie to the site that created it
  (Total Cookie Protection). **Strict** also blocks tracking content (hidden in ads, videos and other embedded
  content) in all windows, not only private ones, and adds Bounce Tracking Protection. Strict can break parts
  of some sites, such as embedded videos, comments or login boxes; you can turn the protection off for one site
  from the shield icon. Menu: Settings > Privacy and security > Enhanced Tracking Protection.
- **Chrome**: Settings > Privacy and security > Third-party cookies > **Block third-party cookies**. Google says
  this blocks the cookies of other sites unless you add exceptions, and that some sites that rely on them may
  not work.
- **Safari (Mac)**: Safari > Settings > Privacy > **Prevent cross-site tracking**. Apple says it stops
  third-party content providers from tracking you across websites, and that Safari presents a simplified view of
  your system to make fingerprinting harder.
- **Brave**: Brave Shields are on by default on every site; Brave says they block trackers and cross-site
  cookies and protect against fingerprinting.

## 2. Add a blocker

A blocker stops requests to lists of thousands of tracking and advertising domains before they leave your
browser, also before you answer a cookie banner. Install only from the official store pages, and use only one
blocker at a time.

- **uBlock Origin** (free, open source): available for Firefox, Edge and Opera. Its author says the version for
  the Chrome Web Store was removed on 31 August 2026, when Chrome dropped the older extension format (Manifest V2).
- **uBlock Origin Lite** (same project, new format): available for Chrome, Edge, Firefox and Safari. Its author
  says it is lighter but less flexible than uBlock Origin; it ships EasyList, EasyPrivacy and other lists.
- **Privacy Badger** (EFF, a digital-rights non-profit): learns what to block by watching which domains collect
  identifiers across sites, instead of using a hand-made list. Available for Chrome, Firefox, Edge, Opera and
  Brave.

## 3. Add our list (optional)

Our files `trackerwatch-verified.txt` and `trackerwatch-full.txt` list the tracking services we measured on news
sites. They only do something when a blocker that accepts added lists reads them (for example uBlock Origin).
Our list is small: use it on top of the blocker's own lists, not instead of them.

Lens also has an optional **clean mode** (Settings and your data > Clean mode) that stops the services of our
list without another extension. Its **extended** option adds the domain rules of EasyPrivacy (by The EasyList
authors) and, in Firefox, trackers disguised as the site itself. A dedicated blocker still covers more (it also
uses path rules and hides empty ad slots).

## 4. Answer cookie banners with "reject"

Where a banner offers a free refusal, rejecting usually means fewer services. The difference can be large: in one test
with Lens in a real browser, on a Spanish news site, accepting took the page from 8 to 68 tracking services. Some sites only
offer "accept or pay"; there, accepting is what opens the door to most of the tracking. Lens's **Your own banner
test** measures this on the sites you choose.

## 5. Send a "do not sell or share" signal

**Global Privacy Control** (GPC) tells sites you do not want your data sold or shared. The GPC project lists
Brave, Firefox, the DuckDuckGo browser and Privacy Badger among those that send it. It is a legal request in
California and some other US states; under the GDPR it expresses a general request to limit sale or sharing. It
does not block anything by itself. Lens shows whether your browser sends it ("What your browser told this site").

## 6. Search and accounts

- Searching while signed in can link your searches to your account, depending on your settings. Lens shows, on a
  results page, whether you are signed in and links to the engine's own pages to see, delete or download what it
  keeps.
- In the EU, the EEA and the UK you can ask any company for a copy of the personal data it holds about you
  (GDPR, article 15).

## 7. Beyond the browser

- **Filtering DNS** (for example a router-level blocker such as Pi-hole, or a DNS service with blocklists) stops
  tracking domains for every app and device on your network, not only the browser.
- **A VPN or Tor** hides your IP address from the sites you visit; a VPN moves that trust to the VPN provider.

## What none of this stops

- Tracking done on the site's own servers, or through its own domain names, which looks like the site itself.
- What you tell a site by signing in, and what a site shares with its partners on its side.
- Your IP address being seen by every server you contact, unless you use a VPN or Tor.

## Sources (read on 7 October 2026)

- Mozilla, Enhanced Tracking Protection in Firefox for desktop: https://support.mozilla.org/en-US/kb/enhanced-tracking-protection-firefox-desktop
- Google, Delete, allow and manage cookies in Chrome: https://support.google.com/chrome/answer/95647
- Apple, Prevent cross-site tracking in Safari on Mac: https://support.apple.com/guide/safari/prevent-cross-site-tracking-sfri40732/mac
- Brave Shields: https://brave.com/shields/
- uBlock Origin: https://github.com/gorhill/uBlock
- uBlock Origin Lite: https://github.com/uBlockOrigin/uBOL-home
- Privacy Badger (EFF): https://privacybadger.org/
- Global Privacy Control: https://globalprivacycontrol.org/
