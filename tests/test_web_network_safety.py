"""SSRF, URL sanitization, and redirect-target boundary tests for web access."""

from __future__ import annotations

import pytest

from core.tool_config import DEFAULT_BLOCKED_CIDRS
from server.tools.web import network_safety
from server.tools.web.contracts import WebAccessError
from server.tools.web.network_safety import (
    is_blocked_address,
    resolve_and_check,
    sanitize_url,
    validate_url,
)


def test_sanitize_url_strips_whitespace_quotes_and_backticks():
    assert sanitize_url('  "https://example.com/a"  ') == "https://example.com/a"
    assert sanitize_url("`https://example.com/a`") == "https://example.com/a"
    assert sanitize_url("'https://example.com'") == "https://example.com"


def test_validate_url_accepts_https_and_normalizes():
    parsed = validate_url("HTTPS://Example.com:443/Path?x=1#frag")
    assert parsed.scheme == "https"
    assert parsed.host == "example.com"
    assert parsed.port == 443
    assert parsed.normalized == "https://example.com:443/Path?x=1"


@pytest.mark.parametrize(
    "raw",
    ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/", "example.com"],
)
def test_validate_url_rejects_non_http_schemes(raw):
    with pytest.raises(WebAccessError) as excinfo:
        validate_url(raw)
    assert excinfo.value.code in {"blocked_scheme", "invalid_url"}


def test_validate_url_rejects_embedded_credentials():
    with pytest.raises(WebAccessError) as excinfo:
        validate_url("https://user:pass@example.com/")
    assert excinfo.value.code == "invalid_url"


def test_validate_url_rejects_missing_host():
    with pytest.raises(WebAccessError) as excinfo:
        validate_url("https://")
    assert excinfo.value.code == "invalid_url"


def test_validate_url_rejects_non_default_port():
    with pytest.raises(WebAccessError) as excinfo:
        validate_url("https://example.com:8080/")
    assert excinfo.value.code == "blocked_port"


def test_validate_url_rejects_overlong_url():
    with pytest.raises(WebAccessError) as excinfo:
        validate_url("https://example.com/" + "a" * 2100)
    assert excinfo.value.code == "invalid_url"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "fc00::1",
        "fe80::1",
    ],
)
def test_blocked_addresses(address):
    import ipaddress

    assert is_blocked_address(ipaddress.ip_address(address), DEFAULT_BLOCKED_CIDRS)


def test_public_address_is_not_blocked():
    import ipaddress

    assert not is_blocked_address(
        ipaddress.ip_address("93.184.216.34"), DEFAULT_BLOCKED_CIDRS
    )
    assert not is_blocked_address(
        ipaddress.ip_address("2606:2800:220:1::248"), DEFAULT_BLOCKED_CIDRS
    )


def _fake_resolver(*addresses: str):
    async def resolver(host, port):
        return list(addresses)

    return resolver


async def test_resolve_and_check_allows_public_address(monkeypatch):
    monkeypatch.setattr(network_safety, "resolve_addresses", _fake_resolver("93.184.216.34"))
    parsed = validate_url("https://example.com/")
    resolved = await resolve_and_check(parsed)
    assert resolved == ["93.184.216.34"]


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.0.10",
        "169.254.169.254",
        "::ffff:10.0.0.1",
        "fd12:3456::1",
        "::1",
    ],
)
async def test_resolve_and_check_blocks_private_and_special_addresses(
    monkeypatch, address
):
    monkeypatch.setattr(network_safety, "resolve_addresses", _fake_resolver(address))
    parsed = validate_url("https://example.com/")
    with pytest.raises(WebAccessError) as excinfo:
        await resolve_and_check(parsed)
    assert excinfo.value.code == "blocked_address"


async def test_resolve_and_check_blocks_when_any_address_is_private(monkeypatch):
    monkeypatch.setattr(
        network_safety,
        "resolve_addresses",
        _fake_resolver("93.184.216.34", "10.0.0.1"),
    )
    parsed = validate_url("https://example.com/")
    with pytest.raises(WebAccessError) as excinfo:
        await resolve_and_check(parsed)
    assert excinfo.value.code == "blocked_address"


async def test_trusted_host_bypasses_cidr_blocks(monkeypatch):
    monkeypatch.setattr(network_safety, "resolve_addresses", _fake_resolver("10.0.0.5"))
    parsed = validate_url("http://searxng.internal:8080/", allowed_ports=frozenset())
    resolved = await resolve_and_check(
        parsed, trusted_hosts=frozenset({"searxng.internal"})
    )
    assert resolved == ["10.0.0.5"]


async def test_dns_failure_maps_to_dns_error(monkeypatch):
    async def resolver(host, port):
        raise WebAccessError("dns_resolution_failed", "boom")

    monkeypatch.setattr(network_safety, "resolve_addresses", resolver)
    parsed = validate_url("https://nonexistent.example/")
    with pytest.raises(WebAccessError) as excinfo:
        await resolve_and_check(parsed)
    assert excinfo.value.code == "dns_resolution_failed"
