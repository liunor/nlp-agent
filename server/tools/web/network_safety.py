"""URL sanitization, DNS resolution, and SSRF defenses for outbound web tools.

v1 strategy: resolve every hostname to its A/AAAA addresses and reject blocked
private/special ranges immediately before each request and before following each
redirect hop. A full DNS-pinning transport is intentionally deferred; the residual
risk is the small TOCTOU window between validation and connect.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from core.tool_config import DEFAULT_BLOCKED_CIDRS
from server.tools.web.contracts import WebAccessError

DEFAULT_ALLOWED_PORTS: frozenset[int] = frozenset({80, 443})

_STRIP_CHARS = " \t\r\n`\"'"


@dataclass(frozen=True)
class ParsedUrl:
    raw: str
    scheme: str
    host: str
    port: int | None
    normalized: str


def sanitize_url(raw: str) -> str:
    cleaned = raw.strip()
    while cleaned and (cleaned[0] in _STRIP_CHARS or cleaned[-1] in _STRIP_CHARS):
        cleaned = cleaned.strip(_STRIP_CHARS)
    return cleaned.strip()


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def validate_url(
    raw: str,
    *,
    allowed_ports: frozenset[int] = DEFAULT_ALLOWED_PORTS,
) -> ParsedUrl:
    cleaned = sanitize_url(raw)
    if not cleaned:
        raise WebAccessError("invalid_url", "URL 为空")
    if len(cleaned) > 2048:
        raise WebAccessError("invalid_url", "URL 过长（超过 2048 字符）")
    try:
        parts = urlsplit(cleaned)
    except ValueError as error:
        raise WebAccessError("invalid_url", f"URL 无法解析: {error}") from error
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise WebAccessError(
            "blocked_scheme", f"仅允许 http/https，拒绝 scheme={scheme or '缺失'!r}"
        )
    host = parts.hostname
    if not host:
        raise WebAccessError("invalid_url", "URL 缺少主机名")
    if parts.username or parts.password:
        raise WebAccessError("invalid_url", "URL 不得内嵌用户名或密码")
    port = parts.port
    effective_port = port if port is not None else _default_port(scheme)
    if allowed_ports and effective_port not in allowed_ports:
        allowed = ", ".join(str(p) for p in sorted(allowed_ports))
        raise WebAccessError(
            "blocked_port", f"端口 {effective_port} 不在允许列表（{allowed}）"
        )
    netloc = host if port is None else f"{host}:{port}"
    normalized = f"{scheme}://{netloc}"
    if parts.path:
        normalized += parts.path
    if parts.query:
        normalized += "?" + parts.query
    return ParsedUrl(
        raw=cleaned, scheme=scheme, host=host.lower(), port=port, normalized=normalized
    )


def _to_ipaddress(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def is_blocked_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    blocked_cidrs: tuple[str, ...] | list[str],
) -> bool:
    for cidr in blocked_cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if (
            isinstance(address, ipaddress.IPv4Address)
            == isinstance(network, ipaddress.IPv4Network)
            and address in network
        ):
            return True
    return False


async def resolve_addresses(host: str, port: int) -> list[str]:
    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo, host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as error:
        raise WebAccessError(
            "dns_resolution_failed", f"无法解析主机 {host!r}: {error}"
        ) from error
    addresses: list[str] = []
    for record in records:
        addresses.append(record[4][0])
    if not addresses:
        raise WebAccessError("dns_resolution_failed", f"主机 {host!r} 无解析结果")
    return addresses


async def resolve_and_check(
    parsed: ParsedUrl,
    *,
    blocked_cidrs: tuple[str, ...] | list[str] = DEFAULT_BLOCKED_CIDRS,
    trusted_hosts: frozenset[str] | set[str] = frozenset(),
) -> list[str]:
    port = parsed.port if parsed.port is not None else _default_port(parsed.scheme)
    resolved = await resolve_addresses(parsed.host, port)
    if parsed.host.lower() in {item.lower() for item in trusted_hosts}:
        return resolved
    for raw in resolved:
        try:
            address = _to_ipaddress(raw.split("%", 1)[0])
        except ValueError:
            raise WebAccessError(
                "blocked_address", f"无法解析地址 {raw!r}，按拒绝处理"
            )
        if address.is_loopback or address.is_link_local or address.is_multicast:
            raise WebAccessError(
                "blocked_address", f"主机 {parsed.host!r} 解析到受限地址"
            )
        if is_blocked_address(address, blocked_cidrs):
            raise WebAccessError(
                "blocked_address", f"主机 {parsed.host!r} 解析到内网/保留地址"
            )
    return resolved
