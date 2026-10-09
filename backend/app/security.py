"""Outbound request guard and secret redaction."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import urlsplit

# Every server-side fetch must target one of these hosts (exact match or subdomain).
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "www.wizzair.com",
        "be.wizzair.com",
        "www.booking.com",
        "nominatim.openstreetmap.org",
        "overpass-api.de",
        "api.frankfurter.dev",
        "kurs.resenje.org",
        "api.openai.com",
    }
)

# Hosts we only ever *link to* (never fetched server-side).
LINK_HOSTS: frozenset[str] = frozenset(
    {
        "wizzair.com",
        "www.wizzair.com",
        "www.booking.com",
        "booking.com",
        "www.openstreetmap.org",
        "www.google.com",
        "en.wikipedia.org",
        "www.wikidata.org",
    }
)


class BlockedURLError(ValueError):
    pass


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None and not _ip_is_public(ip.ipv4_mapped))
    )


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def assert_url_allowed(url: str, *, resolve_dns: bool = True) -> str:
    """Raise BlockedURLError unless `url` is https, on the allowlist and resolves to public IPs."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise BlockedURLError("only https URLs are allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or parts.username or parts.password:
        raise BlockedURLError("invalid host")
    if parts.port not in (None, 443):
        raise BlockedURLError("non-standard port")
    if _is_ip_literal(host):
        raise BlockedURLError("IP-literal hosts are not allowed")
    if host not in ALLOWED_HOSTS:
        raise BlockedURLError(f"host not on allowlist: {host}")
    if resolve_dns:
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise BlockedURLError(f"DNS resolution failed for {host}") from exc
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not _ip_is_public(ip):
                raise BlockedURLError(f"{host} resolves to a non-public address")
    return url


def is_safe_link(url: str | None) -> bool:
    """For links we display (not fetch): https + known provider host."""
    if not url:
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host in LINK_HOSTS or host.endswith(".booking.com"))


_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"lsv2_[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s'\"]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret)[\"']?\s*[:=]\s*[\"']?)[^\s'\",}]+"),
]


def redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) if m.groups() else "") + "***REDACTED***", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        record.msg = redact(msg)
        record.args = ()
        return True


def install_log_redaction() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(RedactingFilter())
    root.addFilter(RedactingFilter())
