"""The publisher, against a fake server: nothing here opens a real connection."""
import json
import urllib.parse
from datetime import date

import pytest

from traceguard.publish import (Bluesky, Mastodon, PublishError, link_facets, load_draft, main, png_size,
                                publish_thread, state_path, load_state)

TODAY = date(2026, 10, 5)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (1200).to_bytes(4, "big") + (630).to_bytes(4, "big") + b"rest"
POSTS = ["Weekly check: https://example.org/report.", "Changes: none.", "How: counts. Data: https://example.org/m"]
BSKY_ENV = {"BLUESKY_HANDLE": "tw.bsky.social", "BLUESKY_APP_PASSWORD": "app-pass-123"}
MASTO_ENV = {"MASTODON_URL": "https://masto.example", "MASTODON_TOKEN": "tok-secret-456"}


class FakeServer:
    """Answers like Bluesky and Mastodon; remembers every call. `fail_on` makes the n-th post fail once."""

    def __init__(self, fail_on=None, media_status=200):
        self.calls, self.fail_on, self.posted, self.media_status, self.polls = [], fail_on, 0, media_status, 0

    def __call__(self, method, url, *, headers=None, data=None, timeout=30):
        path = urllib.parse.urlsplit(url).path
        self.calls.append((method, url, headers or {}, data))
        ok = lambda body, status=200: (status, {}, json.dumps(body).encode())  # noqa: E731
        if path.endswith("createSession"):
            return ok({"accessJwt": "jwt-secret", "did": "did:plc:abc"})
        if path.endswith("uploadBlob"):
            return ok({"blob": {"$type": "blob", "ref": {"$link": "bafy"}, "mimeType": "image/png", "size": len(data)}})
        if path.endswith("createRecord"):
            self.posted += 1
            if self.fail_on == self.posted:
                self.fail_on = None
                return 500, {}, b"boom"
            return ok({"uri": f"at://did:plc:abc/app.bsky.feed.post/r{self.posted}", "cid": f"cid{self.posted}"})
        if path.endswith("getRecord"):
            return 404, {}, b"{}"
        if path.endswith("putRecord"):
            return ok({})
        if path.endswith("/api/v2/instance"):
            return ok({"configuration": {"statuses": {"max_characters": 500}, "media_attachments": {"image_size_limit": 16000000}}})
        if path.endswith("/api/v2/media"):
            return ok({"id": "m1"}, self.media_status)
        if "/api/v1/media/" in path:
            self.polls += 1
            return (206 if self.polls < 2 else 200), {}, b"{}"
        if path.endswith("/api/v1/statuses"):
            self.posted += 1
            if self.fail_on == self.posted:
                self.fail_on = None
                return 429, {"x-ratelimit-reset": "soon"}, b""
            return ok({"id": f"s{self.posted}", "url": f"https://masto.example/@tw/s{self.posted}"})
        raise AssertionError(f"unexpected call {method} {url}")


def write_draft(folder, platform, *, posts=POSTS, date_="2026-10-04", image=True, status="draft"):
    folder.mkdir(parents=True, exist_ok=True)
    if image:
        (folder / "chart.png").write_bytes(PNG)
    path = folder / f"{platform}.json"
    path.write_text(json.dumps({"date": date_, "platform": platform, "status": status, "posts": posts,
                                "image": "chart.png" if image else None, "alt_text": "Bar chart. A: 9."}), encoding="utf-8")
    return path


# --- drafts ----------------------------------------------------------------------------------------------------

def test_link_facets_use_utf8_byte_offsets_and_leave_out_closing_punctuation():
    text = "Más datos (ñ): https://example.org/a-b, fin"
    facet = link_facets(text)[0]
    raw = text.encode("utf-8")
    assert raw[facet["index"]["byteStart"]:facet["index"]["byteEnd"]] == b"https://example.org/a-b"
    assert facet["features"] == [{"$type": "app.bsky.richtext.facet#link", "uri": "https://example.org/a-b"}]
    assert link_facets("no links here") == []


def test_png_size_is_read_from_the_header():
    assert png_size(PNG) == (1200, 630) and png_size(b"not a png") is None


@pytest.mark.parametrize("change,message", [
    ({"posts": ["x" * 301]}, "limit 300"),
    ({"posts": ["Hello @someone"]}, "contains an @"),
    ({"date_": "2026-09-01"}, "stale"),
    ({"status": "published"}, "unexpected draft status"),
    ({"posts": []}, "no posts"),
])
def test_a_bad_draft_is_refused(tmp_path, change, message):
    path = write_draft(tmp_path / "d", "bluesky", **change)
    with pytest.raises(PublishError, match=message):
        load_draft(path, "bluesky", today=TODAY)


def test_a_draft_for_the_other_platform_or_with_a_missing_image_is_refused(tmp_path):
    path = write_draft(tmp_path / "d", "bluesky")
    with pytest.raises(PublishError, match="not 'mastodon'"):
        load_draft(path, "mastodon", today=TODAY)
    (tmp_path / "d" / "chart.png").unlink()
    with pytest.raises(PublishError, match="file is missing"):
        load_draft(path, "bluesky", today=TODAY)


# --- Bluesky ---------------------------------------------------------------------------------------------------

def run_bluesky(tmp_path, server, **kwargs):
    path = write_draft(tmp_path / "d", "bluesky", **kwargs)
    draft = load_draft(path, "bluesky", today=TODAY)
    client = Bluesky(server, "tw.bsky.social", "app-pass-123")
    client.login()
    state = load_state(state_path(path), draft)
    return path, draft, client, state


def records(server):
    return [json.loads(c[3])["record"] for c in server.calls if c[1].endswith("createRecord")]


def test_bluesky_thread_chains_replies_to_the_first_post_and_attaches_the_chart_to_the_first(tmp_path):
    server = FakeServer()
    path, draft, client, state = run_bluesky(tmp_path, server)
    publish_thread(client, draft, path, state, log=lambda *_: None)
    first, second, third = records(server)
    assert "reply" not in first and first["embed"]["images"][0]["alt"] == "Bar chart. A: 9."
    assert first["embed"]["images"][0]["aspectRatio"] == {"width": 1200, "height": 630}
    assert second["reply"] == {"root": {"uri": "at://did:plc:abc/app.bsky.feed.post/r1", "cid": "cid1"},
                               "parent": {"uri": "at://did:plc:abc/app.bsky.feed.post/r1", "cid": "cid1"}}
    assert third["reply"]["root"]["cid"] == "cid1" and third["reply"]["parent"]["cid"] == "cid2"
    assert "embed" not in second and first["langs"] == ["en"] and first["facets"] and third["facets"]
    assert server.calls[1][2]["Content-Type"] == "image/png" and server.calls[1][2]["Authorization"] == "Bearer jwt-secret"
    saved = json.loads(state_path(path).read_text(encoding="utf-8"))
    assert saved["complete"] is True and len(saved["posts"]) == 3


def test_a_thread_cut_short_resumes_and_never_repeats_a_post(tmp_path):
    server = FakeServer(fail_on=2)
    path, draft, client, state = run_bluesky(tmp_path, server)
    with pytest.raises(PublishError, match="HTTP 500"):
        publish_thread(client, draft, path, state, log=lambda *_: None)
    saved = json.loads(state_path(path).read_text(encoding="utf-8"))
    assert saved["complete"] is False and len(saved["posts"]) == 1  # the first went out and was recorded
    state = load_state(state_path(path), draft)
    publish_thread(client, draft, path, state, log=lambda *_: None)
    texts = [r["text"] for r in records(server) if r["text"]]
    assert texts == [POSTS[0], POSTS[1], POSTS[1], POSTS[2]]  # the failed second post was tried twice, the first once
    assert sum(1 for c in server.calls if c[1].endswith("uploadBlob")) == 1  # the image is not uploaded again
    assert records(server)[-1]["reply"]["root"]["cid"] == "cid1"


def test_a_finished_thread_is_never_published_again(tmp_path):
    server = FakeServer()
    path, draft, client, state = run_bluesky(tmp_path, server)
    publish_thread(client, draft, path, state, log=lambda *_: None)
    before = len(server.calls)
    with pytest.raises(PublishError, match="already published"):
        publish_thread(client, draft, path, load_state(state_path(path), draft), log=lambda *_: None)
    assert len(server.calls) == before


def test_the_bot_label_keeps_the_rest_of_the_profile_and_asks_for_https(tmp_path):
    server = FakeServer()
    client = Bluesky(server, "h", "p")
    client.login()
    client.label_bot()
    put = json.loads([c for c in server.calls if c[1].endswith("putRecord")][0][3])
    assert put["record"]["labels"] == {"$type": "com.atproto.label.defs#selfLabels", "values": [{"val": "bot"}]}
    assert put["rkey"] == "self" and "swapRecord" not in put  # there was no profile yet
    with pytest.raises(PublishError, match="https"):
        Bluesky(server, "h", "p", service="http://insecure.example")


# --- Mastodon --------------------------------------------------------------------------------------------------

def run_mastodon(tmp_path, server, **kwargs):
    path = write_draft(tmp_path / "d", "mastodon", **kwargs)
    draft = load_draft(path, "mastodon", today=TODAY)
    client = Mastodon(server, "https://masto.example", "tok-secret-456", sleep=lambda _s: None)
    return path, draft, client, load_state(state_path(path), draft)


def statuses(server):
    return [urllib.parse.parse_qs(c[3].decode()) for c in server.calls if c[1].endswith("/api/v1/statuses")]


def test_mastodon_thread_replies_to_the_previous_post_with_one_key_per_post(tmp_path):
    server = FakeServer(media_status=202)  # the image takes a moment to be processed
    path, draft, client, state = run_mastodon(tmp_path, server)
    publish_thread(client, draft, path, state, log=lambda *_: None)
    first, second, third = statuses(server)
    assert first["media_ids[]"] == ["m1"] and "in_reply_to_id" not in first
    assert second["in_reply_to_id"] == ["s1"] and third["in_reply_to_id"] == ["s2"]
    keys = [c[2]["Idempotency-Key"] for c in server.calls if c[1].endswith("/api/v1/statuses")]
    assert len(set(keys)) == 3 and server.polls == 2
    upload = [c for c in server.calls if c[1].endswith("/api/v2/media")][0]
    assert b'name="description"' in upload[3] and b"Bar chart. A: 9." in upload[3] and b"image/png" in upload[3]


def test_a_rate_limit_stops_the_thread_and_it_can_be_resumed(tmp_path):
    server = FakeServer(fail_on=2)
    path, draft, client, state = run_mastodon(tmp_path, server)
    with pytest.raises(PublishError, match="rate limited"):
        publish_thread(client, draft, path, state, log=lambda *_: None)
    publish_thread(client, draft, path, load_state(state_path(path), draft), log=lambda *_: None)
    assert [s["status"][0] for s in statuses(server)] == [POSTS[0], POSTS[1], POSTS[1], POSTS[2]]
    assert statuses(server)[-1]["in_reply_to_id"] == ["s3"]


def test_a_server_with_a_lower_limit_is_respected_before_anything_is_sent(tmp_path):
    server = FakeServer()
    path, draft, client, state = run_mastodon(tmp_path, server, posts=["x" * 400])
    server_limits = lambda: {"max_characters": 300, "image_size_limit": None}  # noqa: E731
    client.limits = server_limits
    with pytest.raises(PublishError, match="300 characters"):
        publish_thread(client, draft, path, state, log=lambda *_: None)
    assert not statuses(server)


# --- the command -----------------------------------------------------------------------------------------------

def test_the_default_is_a_dry_run_that_opens_no_connection(tmp_path, capsys):
    write_draft(tmp_path / "d", "bluesky")
    write_draft(tmp_path / "d", "mastodon")

    def never(*_a, **_k):
        raise AssertionError("a dry run must not use the network")
    assert main([str(tmp_path / "d")], env=BSKY_ENV, http=never, today=TODAY) == 0
    out = capsys.readouterr().out
    assert out.count("DRY RUN") == 2 and "BLUESKY_HANDLE set" in out and "MASTODON_URL MISSING" in out
    assert "app-pass-123" not in out and not list((tmp_path / "d").glob("*.posted.json"))


def test_send_publishes_both_threads_and_never_prints_a_secret(tmp_path, capsys):
    write_draft(tmp_path / "d", "bluesky")
    write_draft(tmp_path / "d", "mastodon")
    server = FakeServer()
    assert main([str(tmp_path / "d"), "--send"], env={**BSKY_ENV, **MASTO_ENV}, http=server, today=TODAY) == 0
    streams = capsys.readouterr()
    assert all(s not in streams.out + streams.err for s in ("app-pass-123", "tok-secret-456", "jwt-secret"))
    assert server.posted == 6
    assert main([str(tmp_path / "d"), "--send"], env={**BSKY_ENV, **MASTO_ENV}, http=server, today=TODAY) == 1  # again: refused
    assert server.posted == 6 and "already published" in capsys.readouterr().err


def test_missing_credentials_or_a_stale_draft_publish_nothing(tmp_path, capsys):
    write_draft(tmp_path / "d", "bluesky")
    server = FakeServer()
    assert main([str(tmp_path / "d"), "--platform", "bluesky", "--send"], env={}, http=server, today=TODAY) == 1
    assert "missing environment variable" in capsys.readouterr().err and not server.calls
    assert main([str(tmp_path / "d"), "--platform", "bluesky", "--send"], env=BSKY_ENV, http=server,
                today=date(2026, 12, 1)) == 1
    assert "stale" in capsys.readouterr().err and not server.calls


def test_only_label_marks_the_profile_without_a_draft_and_publishes_nothing(capsys):
    server = FakeServer()
    assert main(["--only-label"], env=BSKY_ENV, http=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network"))) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert main(["--only-label", "--send"], env=BSKY_ENV, http=server) == 0
    assert [c[1].rsplit("/", 1)[-1].split("?")[0] for c in server.calls] == ["com.atproto.server.createSession", "com.atproto.repo.getRecord",
                                                              "com.atproto.repo.putRecord"]
    assert not any(c[1].endswith("createRecord") for c in server.calls) and server.posted == 0
    assert main(["--only-label", "--send"], env={}, http=server) == 1
    assert "NOT LABELLED" in capsys.readouterr().err
