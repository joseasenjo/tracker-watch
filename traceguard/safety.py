"""URL validation that keeps TraceGuard from being used to reach private networks (SSRF).

Known limit: the browser resolves names again when it connects, so a DNS-rebinding
attacker could still slip through between the check and the request. Run scans on a
disposable machine (GitHub Actions runner) with no access to internal services.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Iterable
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")
ALLOWED_TARGET_PORTS = (80, 443, 8080, 8443)

Resolver = Callable[[str], Iterable[str]]


class UnsafeURL(ValueError):
    """Raised when a URL must not be fetched."""


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def classify_host(host: str, resolver: Resolver = system_resolver) -> str:
    """Return 'public', 'internal' or 'unresolved' for a host name or IP literal."""
    host = host.strip("[]")
    try:
        ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        pass
    else:
        return "public" if is_public_ip(host) else "internal"
    try:
        addresses = list(resolver(host))
    except OSError:
        return "unresolved"
    if not addresses:
        return "unresolved"
    return "public" if all(is_public_ip(a) for a in addresses) else "internal"


def check_target(url: str, resolver: Resolver = system_resolver) -> str:
    """Validate a user-supplied URL and return it normalised, or raise UnsafeURL."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURL(f"scheme '{scheme or '(none)'}' is not allowed; use http or https")
    host = parts.hostname
    if not host:
        raise UnsafeURL("URL has no host")
    if parts.username or parts.password:
        raise UnsafeURL("URLs with embedded credentials are not allowed")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise UnsafeURL("invalid port") from None
    if port not in ALLOWED_TARGET_PORTS:
        raise UnsafeURL(f"port {port} is not allowed")
    verdict = classify_host(host, resolver)
    if verdict == "internal":
        raise UnsafeURL("host resolves to a private, loopback or otherwise non-public address")
    if verdict == "unresolved":
        raise UnsafeURL("host cannot be resolved")
    return parts.geturl()


class HostGuard:
    """Caches host verdicts so the per-request browser check stays cheap."""

    def __init__(self, resolver: Resolver = system_resolver):
        self._resolver = resolver
        self._cache: dict[str, str] = {}

    def verdict(self, host: str) -> str:
        host = host.lower()
        if host not in self._cache:
            self._cache[host] = classify_host(host, self._resolver)
        return self._cache[host]
