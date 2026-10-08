"""Publishes a reviewed weekly draft (data/drafts/<date>/<platform>.json, made by traceguard.posts) as a thread on
Bluesky and/or Mastodon.

SAFE BY DEFAULT: without --send nothing is published and no connection is made; it only checks the draft and prints
what would be sent. With --send:
- the draft is validated first (platform, status, age, length of every post, no @-mentions, image present);
- every post that goes out is written at once to <platform>.posted.json next to the draft, so a thread that was cut
  short is resumed where it stopped and a post that already went out is never sent twice; a finished thread is
  never published again (delete its .posted.json by hand to redo it);
- credentials come only from environment variables (never from files or arguments) and are never printed.

    python -m traceguard.publish data/drafts/2026-10-04 --platform bluesky            # dry run
    python -m traceguard.publish data/drafts/2026-10-04 --platform both --send        # publish

Environment: BLUESKY_HANDLE, BLUESKY_APP_PASSWORD (an app password, never the main one), optional BLUESKY_SERVICE
(default https://bsky.social); MASTODON_URL (https://your.instance), MASTODON_TOKEN (scopes write:statuses and
write:media). `--label-bot` puts the "bot" self-label on the Bluesky profile (Bluesky asks automated accounts to do it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date as _date, datetime, timezone
from pathlib import Path

from .posts import LIMITS

BLUESKY_SERVICE = "https://bsky.social"
MAX_AGE_DAYS = 14  # a draft older than this is stale: it is not published
BLUESKY_IMAGE_BYTES = 2_000_000  # app.bsky.embed.images
URL_RE = re.compile(r"https?://[^\s]+")
TRAILING_PUNCTUATION = ".,;:!?)]}'\""
PLATFORMS = ("bluesky", "mastodon")
CREDENTIALS = {"bluesky": ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"), "mastodon": ("MASTODON_URL", "MASTODON_TOKEN")}


class PublishError(RuntimeError):
    pass


# --- transport -------------------------------------------------------------------------------------------------

def urllib_http(method: str, url: str, *, headers: dict | None = None, data: bytes | None = None,
                timeout: float = 30) -> tuple[int, dict, bytes]:
    """(status, lower-cased headers, body). An HTTP error status is returned, not raised; only a network failure raises."""
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()
    except urllib.error.URLError as exc:
        raise PublishError(f"network error: {exc.reason}") from exc


def _https(url: str, what: str) -> str:
    if not url.startswith("https://"):
        raise PublishError(f"{what} must start with https:// (credentials are never sent over plain http)")
    return url.rstrip("/")


def _check(status: int, body: bytes, what: str, headers: dict | None = None, ok: tuple[int, ...] = (200,)) -> bytes:
    if status in ok:
        return body
    detail = body[:200].decode("utf-8", "replace")
    if status == 429:
        reset = (headers or {}).get("x-ratelimit-reset") or (headers or {}).get("ratelimit-reset") or "unknown"
        raise PublishError(f"{what}: rate limited (HTTP 429, resets {reset}); the thread can be resumed later")
    raise PublishError(f"{what}: HTTP {status} {detail}")


# --- helpers ---------------------------------------------------------------------------------------------------

def link_facets(text: str) -> list[dict]:
    """Bluesky does not turn links into links by itself: each one needs a facet with its UTF-8 byte range."""
    facets = []
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(TRAILING_PUNCTUATION)
        start = len(text[:match.start()].encode("utf-8"))
        facets.append({"index": {"byteStart": start, "byteEnd": start + len(url.encode("utf-8"))},
                       "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}]})
    return facets


def png_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _multipart(fields: dict[str, str], file_field: str, filename: str, mime: str, content: bytes) -> tuple[bytes, str]:
    boundary = "----tw" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                  f"Content-Type: {mime}\r\n\r\n").encode("utf-8") + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# --- drafts and state ------------------------------------------------------------------------------------------

def load_draft(path: Path, platform: str, *, today: _date | None = None, max_age_days: int = MAX_AGE_DAYS) -> dict:
    """The draft as a dict, or PublishError: wrong platform, bad status, stale, too long, @-mentions, image missing."""
    try:
        draft = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublishError(f"cannot read the draft {path}: {exc}") from exc
    if draft.get("platform") != platform:
        raise PublishError(f"{path.name} is a draft for {draft.get('platform')!r}, not {platform!r}")
    if draft.get("status") not in ("draft", "approved"):
        raise PublishError(f"unexpected draft status {draft.get('status')!r}")
    posts = draft.get("posts")
    if not isinstance(posts, list) or not posts or not all(isinstance(p, str) and p.strip() for p in posts):
        raise PublishError("the draft has no posts")
    for index, text in enumerate(posts, 1):
        if len(text) > LIMITS[platform]:
            raise PublishError(f"post {index} has {len(text)} characters (limit {LIMITS[platform]})")
        if "@" in text:
            raise PublishError(f"post {index} contains an @ (posts never mention accounts)")
    try:
        age = ((today or _date.today()) - _date.fromisoformat(str(draft.get("date")))).days
    except ValueError as exc:
        raise PublishError(f"the draft has no valid date: {draft.get('date')!r}") from exc
    if age > max_age_days:
        raise PublishError(f"the draft is from {draft['date']}, {age} days old (limit {max_age_days}): it is stale")
    if draft.get("image"):
        image = path.parent / draft["image"]
        if not image.is_file():
            raise PublishError(f"the draft names the image {draft['image']} but the file is missing")
        if not str(draft.get("alt_text") or "").strip():
            raise PublishError("the image has no alt text")
    return draft


def state_path(draft_path: Path) -> Path:
    return draft_path.with_suffix(".posted.json")


def load_state(path: Path, draft: dict) -> dict:
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("date") != draft["date"] or state.get("platform") != draft["platform"]:
            raise PublishError(f"{path.name} belongs to another draft ({state.get('date')}, {state.get('platform')})")
        return state
    return {"date": draft["date"], "platform": draft["platform"], "complete": False, "posts": []}


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # the file is never left half written


# --- Bluesky ---------------------------------------------------------------------------------------------------

class Bluesky:
    name = "bluesky"

    def __init__(self, http, handle: str, password: str, service: str = BLUESKY_SERVICE):
        self.http, self.handle, self.password = http, handle, password
        self.service = _https(service, "BLUESKY_SERVICE")
        self.jwt = self.did = None

    def _call(self, nsid: str, body: dict | bytes | None = None, *, method: str = "POST", query: dict | None = None,
              content_type: str = "application/json", auth: bool = True) -> dict:
        headers = {"Content-Type": content_type} if body is not None else {}
        if auth:
            headers["Authorization"] = f"Bearer {self.jwt}"
        data = json.dumps(body).encode("utf-8") if isinstance(body, dict) else body
        url = f"{self.service}/xrpc/{nsid}" + (("?" + urllib.parse.urlencode(query)) if query else "")
        status, response_headers, raw = self.http(method, url, headers=headers, data=data)
        return json.loads(_check(status, raw, nsid, response_headers) or b"{}")

    def login(self) -> None:
        session = self._call("com.atproto.server.createSession",
                             {"identifier": self.handle, "password": self.password}, auth=False)
        self.jwt, self.did = session["accessJwt"], session["did"]

    def upload_image(self, data: bytes, mime: str = "image/png") -> dict:
        if len(data) > BLUESKY_IMAGE_BYTES:
            raise PublishError(f"the image is {len(data)} bytes, over Bluesky's limit of {BLUESKY_IMAGE_BYTES}")
        return self._call("com.atproto.repo.uploadBlob", data, content_type=mime)["blob"]

    def post(self, text: str, *, reply: dict | None = None, image: dict | None = None, alt: str = "") -> dict:
        record: dict = {"$type": "app.bsky.feed.post", "text": text, "createdAt": _now(), "langs": ["en"]}
        facets = link_facets(text)
        if facets:
            record["facets"] = facets
        if image:
            item = {"alt": alt, "image": image["blob"]}
            if image.get("size"):
                item["aspectRatio"] = {"width": image["size"][0], "height": image["size"][1]}
            record["embed"] = {"$type": "app.bsky.embed.images", "images": [item]}
        if reply:
            record["reply"] = reply
        created = self._call("com.atproto.repo.createRecord",
                             {"repo": self.did, "collection": "app.bsky.feed.post", "record": record})
        rkey = created["uri"].rsplit("/", 1)[-1]
        return {"uri": created["uri"], "cid": created["cid"], "url": f"https://bsky.app/profile/{self.did}/post/{rkey}"}

    def reply_ref(self, first: dict, previous: dict) -> dict:
        return {"root": {"uri": first["uri"], "cid": first["cid"]}, "parent": {"uri": previous["uri"], "cid": previous["cid"]}}

    def label_bot(self) -> None:
        """Bluesky asks automated accounts to carry the 'bot' self-label on their profile; the rest is kept."""
        query = {"repo": self.did, "collection": "app.bsky.actor.profile", "rkey": "self"}
        status, headers, raw = self.http("GET", f"{self.service}/xrpc/com.atproto.repo.getRecord?" + urllib.parse.urlencode(query),
                                         headers={"Authorization": f"Bearer {self.jwt}"})
        record, cid = {"$type": "app.bsky.actor.profile"}, None
        if status == 200:
            found = json.loads(raw)
            record, cid = found["value"], found.get("cid")
        elif status not in (400, 404):
            _check(status, raw, "getRecord", headers)
        record["labels"] = {"$type": "com.atproto.label.defs#selfLabels", "values": [{"val": "bot"}]}
        body = {"repo": self.did, "collection": "app.bsky.actor.profile", "rkey": "self", "record": record}
        if cid:
            body["swapRecord"] = cid
        self._call("com.atproto.repo.putRecord", body)


# --- Mastodon --------------------------------------------------------------------------------------------------

class Mastodon:
    name = "mastodon"

    def __init__(self, http, base_url: str, token: str, sleep=time.sleep):
        self.http, self.token, self.sleep = http, token, sleep
        self.base = _https(base_url, "MASTODON_URL")

    def _headers(self, extra: dict | None = None) -> dict:
        return {"Authorization": f"Bearer {self.token}", **(extra or {})}

    def limits(self) -> dict:
        """This server's own limits (they differ between instances); empty when it does not say."""
        status, headers, raw = self.http("GET", f"{self.base}/api/v2/instance", headers={})
        if status != 200:
            return {}
        config = json.loads(raw).get("configuration", {})
        return {"max_characters": config.get("statuses", {}).get("max_characters"),
                "image_size_limit": config.get("media_attachments", {}).get("image_size_limit")}

    def upload_image(self, data: bytes, alt: str, mime: str = "image/png", filename: str = "chart.png") -> str:
        body, content_type = _multipart({"description": alt}, "file", filename, mime, data)
        status, headers, raw = self.http("POST", f"{self.base}/api/v2/media", headers=self._headers({"Content-Type": content_type}),
                                         data=body)
        media = json.loads(_check(status, raw, "upload media", headers, ok=(200, 202)))
        if status == 202:  # still being processed: wait until it is ready (206 = not yet)
            for _ in range(30):
                self.sleep(1)
                status, headers, raw = self.http("GET", f"{self.base}/api/v1/media/{media['id']}", headers=self._headers())
                if status == 200:
                    break
                _check(status, raw, "media processing", headers, ok=(200, 206))
            else:
                raise PublishError("the image was not processed in time")
        return str(media["id"])

    def post(self, text: str, *, reply_to: str | None = None, media_id: str | None = None, key: str) -> dict:
        form: list[tuple[str, str]] = [("status", text), ("visibility", "public"), ("language", "en")]
        if media_id:
            form.append(("media_ids[]", media_id))
        if reply_to:
            form.append(("in_reply_to_id", reply_to))
        status, headers, raw = self.http("POST", f"{self.base}/api/v1/statuses", data=urllib.parse.urlencode(form).encode("utf-8"),
                                         headers=self._headers({"Content-Type": "application/x-www-form-urlencoded",
                                                                "Idempotency-Key": key}))
        created = json.loads(_check(status, raw, "post status", headers))
        return {"id": str(created["id"]), "url": created.get("url") or created.get("uri") or ""}


# --- the thread ------------------------------------------------------------------------------------------------

def describe(draft: dict, platform: str, state: dict, creds_ok: dict[str, bool]) -> str:
    done = len(state["posts"])
    lines = [f"[{platform}] DRY RUN: nothing was sent, no connection was made.",
             f"  draft of {draft['date']}: {len(draft['posts'])} posts, image: {draft.get('image') or 'none'}",
             "  credentials in the environment: " + ", ".join(f"{k} {'set' if v else 'MISSING'}" for k, v in creds_ok.items())]
    if state["complete"]:
        lines.append("  already published: this thread would NOT be sent again.")
    elif done:
        lines.append(f"  {done} of {len(draft['posts'])} posts already went out: --send would resume with post {done + 1}.")
    for index, text in enumerate(draft["posts"], 1):
        mark = "sent" if index <= done else "to send"
        extra = f", {len(link_facets(text))} link(s)" if platform == "bluesky" else ""
        lines.append(f"  post {index} ({len(text)}/{LIMITS[platform]}{extra}) [{mark}]: {text}")
    return "\n".join(lines)


def publish_thread(client, draft: dict, draft_path: Path, state: dict, *, log=print) -> dict:
    """Send the posts not yet recorded in the state, recording each one as soon as it is out."""
    platform = client.name
    path = state_path(draft_path)
    if state["complete"]:
        raise PublishError(f"the {platform} thread of {draft['date']} was already published; nothing sent")
    posts = draft["posts"]
    image_bytes = (draft_path.parent / draft["image"]).read_bytes() if draft.get("image") else None
    alt = draft.get("alt_text", "")
    if platform == "mastodon":
        limits = client.limits()
        if limits.get("max_characters") and max(map(len, posts)) > limits["max_characters"]:
            raise PublishError(f"this server allows {limits['max_characters']} characters per post; a post is longer")
        if image_bytes and limits.get("image_size_limit") and len(image_bytes) > limits["image_size_limit"]:
            raise PublishError("the image is larger than this server allows")
    for index in range(len(state["posts"]), len(posts)):
        text, previous = posts[index], (state["posts"][index - 1] if index else None)
        if platform == "bluesky":
            image = None
            if index == 0 and image_bytes:
                image = {"blob": client.upload_image(image_bytes), "size": png_size(image_bytes)}
            reply = client.reply_ref(state["posts"][0], previous) if previous else None
            result = client.post(text, reply=reply, image=image, alt=alt)
        else:
            media_id = client.upload_image(image_bytes, alt) if index == 0 and image_bytes else None
            key = hashlib.sha256(f"{draft['date']}|{platform}|{index}|{text}".encode("utf-8")).hexdigest()
            result = client.post(text, reply_to=previous["id"] if previous else None, media_id=media_id, key=key)
        state["posts"].append(result)
        save_state(path, state)  # recorded at once: a failure later in the thread loses nothing
        log(f"[{platform}] post {index + 1}/{len(posts)} published: {result.get('url')}")
    state["complete"] = True
    state["finished_at"] = _now()
    save_state(path, state)
    return state


def make_client(platform: str, env, http):
    names = CREDENTIALS[platform]
    missing = [n for n in names if not env.get(n)]
    if missing:
        raise PublishError(f"missing environment variable(s): {', '.join(missing)}")
    if platform == "bluesky":
        client = Bluesky(http, env["BLUESKY_HANDLE"], env["BLUESKY_APP_PASSWORD"], env.get("BLUESKY_SERVICE") or BLUESKY_SERVICE)
        client.login()
        return client
    return Mastodon(http, env["MASTODON_URL"], env["MASTODON_TOKEN"])


def main(argv: list[str] | None = None, *, env=None, http=urllib_http, today: _date | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceguard.publish", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("draft_dir", nargs="?", help="a dated folder of drafts, e.g. data/drafts/2026-10-04")
    parser.add_argument("--platform", choices=[*PLATFORMS, "both"], default="both")
    parser.add_argument("--send", action="store_true", help="really publish (without it: dry run, no connection)")
    parser.add_argument("--label-bot", action="store_true", help="also put the 'bot' self-label on the Bluesky profile")
    parser.add_argument("--only-label", action="store_true",
                        help="only put the 'bot' self-label on the Bluesky profile; no draft needed, nothing is published")
    parser.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    if args.only_label:
        if not args.send:
            print("[bluesky] DRY RUN: --send would add the 'bot' self-label to the profile; nothing is published.\n"
                  "  credentials in the environment: " + ", ".join(f"{n} {'set' if env.get(n) else 'MISSING'}" for n in CREDENTIALS["bluesky"]))
            return 0
        try:
            make_client("bluesky", env, http).label_bot()
        except PublishError as exc:
            print(f"[bluesky] NOT LABELLED: {exc}", file=sys.stderr)
            return 1
        print("[bluesky] the 'bot' self-label is on the profile.")
        return 0
    if not args.draft_dir:
        parser.error("a draft folder is needed (or use --only-label)")
    folder = Path(args.draft_dir)
    platforms = PLATFORMS if args.platform == "both" else (args.platform,)
    failed = False
    for platform in platforms:
        draft_path = folder / f"{platform}.json"
        try:
            draft = load_draft(draft_path, platform, today=today, max_age_days=args.max_age_days)
            state = load_state(state_path(draft_path), draft)
            if not args.send:
                print(describe(draft, platform, state, {n: bool(env.get(n)) for n in CREDENTIALS[platform]}))
                if args.label_bot and platform == "bluesky":
                    print("[bluesky] DRY RUN: --send would add the 'bot' self-label to the profile.")
                continue
            client = make_client(platform, env, http)
            if args.label_bot and platform == "bluesky":
                client.label_bot()
                print("[bluesky] the 'bot' self-label is on the profile.")
            publish_thread(client, draft, draft_path, state)
        except PublishError as exc:
            failed = True
            print(f"[{platform}] NOT PUBLISHED: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
