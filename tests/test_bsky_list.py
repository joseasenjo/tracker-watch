import json

from traceguard import bsky_list
from traceguard.publish import Bluesky


class FakeBluesky:
    """A tiny in-memory stand-in for the records of one Bluesky repo."""

    def __init__(self, lists=(), items=()):
        self.records = {"app.bsky.graph.list": list(lists), "app.bsky.graph.listitem": list(items)}
        self.did, self.created = "did:plc:me", []

    def _call(self, nsid, body=None, *, method="POST", query=None, **kwargs):
        if nsid == "com.atproto.repo.listRecords":
            return {"records": self.records[query["collection"]]}
        self.created.append((body["collection"], body["record"]))
        uri = f"at://did:plc:me/{body['collection']}/{len(self.created)}"
        return {"uri": uri, "cid": "c"}


ACCOUNTS = [{"site": "A", "handle": "a.example", "did": "did:plc:a"}, {"site": "B", "handle": "b.example", "did": "did:plc:b"}]


def test_sync_creates_the_list_and_adds_every_account():
    client = FakeBluesky()
    result = bsky_list.sync(client, ACCOUNTS, log=lambda *_: None)
    kinds = [c for c, _ in client.created]
    assert kinds == ["app.bsky.graph.list", "app.bsky.graph.listitem", "app.bsky.graph.listitem"]
    assert client.created[0][1]["name"] == bsky_list.LIST_NAME and result["added"] == ["a.example", "b.example"]


def test_sync_only_adds_what_is_missing_and_never_creates_a_second_list():
    uri = "at://did:plc:me/app.bsky.graph.list/1"
    client = FakeBluesky(lists=[{"uri": uri, "value": {"name": bsky_list.LIST_NAME}}],
                         items=[{"value": {"subject": "did:plc:a", "list": uri}}])
    result = bsky_list.sync(client, ACCOUNTS, log=lambda *_: None)
    assert [c for c, _ in client.created] == ["app.bsky.graph.listitem"] and result["added"] == ["b.example"]


def test_dry_run_changes_nothing():
    client = FakeBluesky()
    bsky_list.sync(client, ACCOUNTS, dry_run=True, log=lambda *_: None)
    assert client.created == []


def test_resolve_accepts_only_a_handle_equal_to_the_outlet_domain(tmp_path):
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"sites": [{"name": "A", "url": "https://www.a.example"},
                                           {"name": "B", "url": "https://www.b.example"}]}), encoding="utf-8")

    def http(method, url, headers=None, data=None):
        if "actor=a.example" in url:
            return 200, {}, json.dumps({"handle": "a.example", "did": "did:plc:a"}).encode()
        if "actor=b.example" in url:  # a profile that is not the domain's own handle: rejected
            return 200, {}, json.dumps({"handle": "someone-else.bsky.social", "did": "did:plc:x"}).encode()
        return 400, {}, b"{}"

    assert bsky_list.resolve(sites, http) == [{"site": "A", "handle": "a.example", "did": "did:plc:a"}]
