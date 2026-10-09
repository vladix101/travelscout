import logging
import socket
from unittest.mock import patch

import pytest

from app.security import BlockedURLError, RedactingFilter, assert_url_allowed, is_safe_link, redact


def fake_dns(ip: str):
    return lambda host, port, proto=0: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


@pytest.mark.parametrize(
    "url",
    [
        "http://www.booking.com/x",  # not https
        "https://localhost/x",
        "https://127.0.0.1/x",
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata (IP literal)
        "https://[::1]/x",
        "https://evil.example.com/x",
        "https://www.booking.com.evil.com/x",
        "https://user:pass@www.booking.com/x",
        "https://www.booking.com:8443/x",
        "file:///etc/passwd",
    ],
)
def test_blocked_urls(url):
    with pytest.raises(BlockedURLError):
        assert_url_allowed(url, resolve_dns=False)


@pytest.mark.parametrize("ip", ["10.0.0.5", "192.168.1.1", "127.0.0.1", "169.254.169.254", "172.16.0.1"])
def test_allowlisted_host_resolving_to_private_ip_is_blocked(ip):
    with patch("app.security.socket.getaddrinfo", fake_dns(ip)), pytest.raises(BlockedURLError):
        assert_url_allowed("https://www.booking.com/searchresults.html")


def test_allowlisted_public_host_passes():
    with patch("app.security.socket.getaddrinfo", fake_dns("93.184.216.34")):
        assert assert_url_allowed("https://be.wizzair.com/29.20.0/Api/search/timetable")


def test_safe_links():
    assert is_safe_link("https://www.booking.com/hotel/it/x.html")
    assert not is_safe_link("javascript:alert(1)")
    assert not is_safe_link("https://phishing.example/booking")


def test_redaction():
    fake_key = "sk-proj-" + "ABCDEFGHIJ" * 3  # built at runtime so secret scanners don't flag the repo
    text = f"key {fake_key} and Authorization: Bearer abc.def and api_key=supersecret1"
    out = redact(text)
    assert "ABCDEFGHIJ" not in out and "abc.def" not in out and "supersecret1" not in out
    assert out.count("***REDACTED***") == 3


def test_log_filter_redacts(caplog):
    logger = logging.getLogger("redact-test")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO):
        logger.info("calling with %s", "sk-" + "abcdefghij" * 3)
    assert "sk-abcdefghij" not in caplog.text
