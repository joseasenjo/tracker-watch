import json

import pytest

from traceguard import mastodon_delete
from traceguard.publish import Mastodon, PublishError


class FakeHttp:
    def __init__(self, owner="1", status_owner="1", content="<p>Cookies are fading; fingerprinting is not.</p>"):
        self.calls, self.owner, self.status_owner, self.content = [], owner, status_owner, content

    def __call__(self, method, url, headers=None, data=None):
        self.calls.append((method, url))
        if method == "GET":
            return 200, {}, json.dumps({"account": {"id": self.status_owner}, "content": self.content}).encode()
        return 200, {}, b"{}"


def client(http):
    return Mastodon(http, "https://mastodon.example", "tok")


def deletes(http):
    return [c for c in http.calls if c[0] == "DELETE"]


def test_deletes_only_its_own_status_when_the_text_matches():
    http = FakeHttp()
    assert mastodon_delete.delete_status(client(http), "117463023118125488", "Cookies are fading", log=lambda *_: None)
    assert deletes(http) == [("DELETE", "https://mastodon.example/api/v1/statuses/117463023118125488")]


def test_refuses_a_different_text_and_deletes_nothing():
    http = FakeHttp()
    with pytest.raises(PublishError):
        mastodon_delete.delete_status(client(http), "117463023118125488", "Something else", log=lambda *_: None)
    assert not deletes(http)


@pytest.mark.parametrize("status_id", ["", "abc", "12/../3", "1 2"])
def test_refuses_a_malformed_id(status_id):
    with pytest.raises(PublishError):
        mastodon_delete.delete_status(client(FakeHttp()), status_id, "x", log=lambda *_: None)


def test_html_is_read_as_text():
    assert mastodon_delete.plain("<p>a &amp; b<br />c</p>") == "a & b\nc"
