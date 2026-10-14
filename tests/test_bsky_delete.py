import pytest

from traceguard import bsky_delete
from traceguard.publish import PublishError


class Fake:
    def __init__(self, text="New: Browser check. Press a button"):
        self.did, self.calls, self.text = "did:plc:me", [], text

    def _call(self, nsid, body=None, *, method="POST", query=None, **kwargs):
        self.calls.append((nsid, body, query))
        if nsid == "com.atproto.repo.getRecord":
            return {"value": {"text": self.text}}
        return {}


KEY = "3mxtyhbh4ui2y"


def test_deletes_only_the_named_post_of_its_own_account_when_the_text_matches():
    client = Fake()
    assert bsky_delete.delete_post(client, KEY, "New: Browser check", log=lambda *_: None)
    nsid, body, _ = client.calls[-1]
    assert nsid == "com.atproto.repo.deleteRecord"
    assert body == {"repo": "did:plc:me", "collection": "app.bsky.feed.post", "rkey": KEY}


def test_refuses_when_the_text_does_not_match_and_deletes_nothing():
    client = Fake(text="Something else entirely")
    with pytest.raises(PublishError):
        bsky_delete.delete_post(client, KEY, "New: Browser check", log=lambda *_: None)
    assert all(call[0] != "com.atproto.repo.deleteRecord" for call in client.calls)


@pytest.mark.parametrize("key", ["", "short", "3MXTYHBH4UI2Y", "../../x", "3mxtyhbh4ui2y/extra"])
def test_refuses_a_malformed_key(key):
    with pytest.raises(PublishError):
        bsky_delete.delete_post(Fake(), key, "New", log=lambda *_: None)


def test_refuses_an_empty_expectation():
    with pytest.raises(PublishError):
        bsky_delete.delete_post(Fake(), KEY, "  ", log=lambda *_: None)


def test_main_needs_the_credentials():
    assert bsky_delete.main([KEY, "--expect", "New"], env={}) == 2
