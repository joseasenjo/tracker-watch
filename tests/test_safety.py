import pytest

from traceguard.safety import HostGuard, UnsafeURL, check_target, classify_host, is_public_ip


def resolver_to(*addresses):
    return lambda host: list(addresses)


def failing_resolver(host):
    raise OSError("no such host")


PUBLIC = "93.184.216.34"


@pytest.mark.parametrize("url", [
    "ftp://example.com/file",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "example.com",
    "http://user:secret@example.com/",
    "http://example.com:22/",
    "http://example.com:99999/",
    "http:///nohost",
])
def test_rejects_unsafe_shapes(url):
    with pytest.raises(UnsafeURL):
        check_target(url, resolver_to(PUBLIC))


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://localhost/",
    "http://[::1]/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/",
    "http://[::ffff:127.0.0.1]/",
    "http://0.0.0.0/",
])
def test_rejects_internal_ip_literals(url):
    resolver = resolver_to("127.0.0.1") if "localhost" in url else resolver_to(PUBLIC)
    with pytest.raises(UnsafeURL):
        check_target(url, resolver)


def test_rejects_name_resolving_to_private_address():
    with pytest.raises(UnsafeURL):
        check_target("https://intranet.example.com/", resolver_to("10.1.2.3"))


def test_rejects_name_with_mixed_public_and_private_answers():
    with pytest.raises(UnsafeURL):
        check_target("https://example.com/", resolver_to(PUBLIC, "192.168.0.10"))


def test_rejects_unresolvable_host():
    with pytest.raises(UnsafeURL):
        check_target("https://does-not-exist.example/", failing_resolver)


def test_accepts_public_https_and_http_urls():
    assert check_target("https://example.com/a?b=1", resolver_to(PUBLIC)) == "https://example.com/a?b=1"
    assert check_target("  http://example.com:8080/  ", resolver_to(PUBLIC)) == "http://example.com:8080/"


def test_is_public_ip():
    assert is_public_ip(PUBLIC)
    assert not is_public_ip("not-an-ip")
    assert not is_public_ip("224.0.0.1")


def test_classify_host_verdicts():
    assert classify_host("example.com", resolver_to(PUBLIC)) == "public"
    assert classify_host("example.com", resolver_to("10.0.0.1")) == "internal"
    assert classify_host("example.com", failing_resolver) == "unresolved"
    assert classify_host("127.0.0.1") == "internal"


def test_host_guard_caches_verdicts():
    calls = []

    def resolver(host):
        calls.append(host)
        return [PUBLIC]

    guard = HostGuard(resolver)
    assert guard.verdict("Example.com") == "public"
    assert guard.verdict("example.com") == "public"
    assert calls == ["example.com"]
