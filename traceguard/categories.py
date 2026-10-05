"""Plain-language descriptions of the categories used in the classification list.

These describe the TYPE of service, not any particular company. They say what such services typically do and
what data they typically receive, hedged on purpose: what a given service actually receives depends on how each
site configures it, which a passive measurement cannot see. No category is a judgement about a site.
"""
from __future__ import annotations

# id -> (label, what it does, what it typically receives, what the count means)
CATEGORIES: dict[str, dict] = {
    "advertising": {
        "label": "Advertising",
        "what": "Services that take part in buying, selling or delivering online ads: ad servers, ad exchanges, "
                "bidding platforms and the companies that match ads to audiences. When a page loads, several of "
                "them can be contacted within milliseconds to decide which ad to show.",
        "data": "Typically the address of the page, browser and device details, an approximate location derived "
                "from the connection, and an identifier kept in a cookie or similar. Some companies also match "
                "their identifiers with each other (often called “cookie syncing”), which can let one company "
                "recognise a visitor another one already knows.",
        "counted": True,
    },
    "analytics": {
        "label": "Analytics",
        "what": "Services that count visits, pages and interactions to produce statistics for the site's operator.",
        "data": "Typically the page address, where the visitor came from, device and browser details and an "
                "identifier that recognises returning visitors. Some analytics services belong to advertising "
                "companies and can combine this with data from their other services.",
        "counted": True,
    },
    "audience_measurement": {
        "label": "Audience measurement",
        "what": "Services that measure how many people, and what kind of audience, visit sites, often to produce the "
                "ratings used to sell advertising.",
        "data": "Typically page views with an identifier, sometimes combined with panel data from volunteers. "
                "Because the same service is present on many sites, it can build a picture of browsing across them.",
        "counted": True,
    },
    "social": {
        "label": "Social networks",
        "what": "Buttons, embeds and tracking pixels of social networks.",
        "data": "Contacting them can tell the network that the page was loaded, even if the visitor never presses "
                "anything. For a visitor who is logged in to that network, the request may be linked to their "
                "account, depending on their settings.",
        "counted": True,
    },
    "session_replay": {
        "label": "Session replay",
        "what": "Services that record or reconstruct what a visitor does on a page (mouse movements, clicks, "
                "scrolling and, unless masked, sometimes typed text) so the site's operator can watch it back.",
        "data": "Interaction data from the page. Vendors offer ways to mask sensitive fields; whether a site uses "
                "them is not visible to a passive measurement, so we only report that the service was contacted.",
        "counted": True,
    },
    "tag_manager": {
        "label": "Tag managers",
        "what": "Containers that load other scripts on behalf of the site.",
        "data": "A tag manager is not tracking by itself, so it is not counted. What it loads can belong to any "
                "other category; when those services are contacted they are classified and counted separately.",
        "counted": False,
    },
    "consent_management": {
        "label": "Consent management",
        "what": "Services that show the cookie banner and remember the visitor's choice.",
        "data": "They are contacted to display the banner and to store the choice, so they are listed but not "
                "counted. Our “After the banner” test uses their buttons.",
        "counted": False,
    },
    "paywall": {
        "label": "Paywalls and subscriptions",
        "what": "Services that manage subscriptions and limits on free articles.",
        "data": "They often need to recognise returning visitors to count free articles, so they are listed but not "
                "counted as tracking.",
        "counted": False,
    },
}
ORDER = list(CATEGORIES)


def label(category: str) -> str:
    return CATEGORIES.get(category, {}).get("label", category.replace("_", " ").capitalize())


def describe(tracker_rows: list[dict]) -> list[dict]:
    """The glossary: every category with how many list entries and operators belong to it."""
    rows = []
    for key in ORDER:
        entries = [t for t in tracker_rows if t["category"] == key]
        rows.append({"id": key, **CATEGORIES[key], "entries": len(entries),
                     "operators": len({t["entity"] for t in entries})})
    return rows
