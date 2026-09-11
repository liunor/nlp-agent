"""Deterministic outbound routing for services running inside Compose.

The deployment exposes an HTTP proxy for destinations that need it, but the
proxy must not become an implicit dependency for domestic providers.  Keeping
the decision here also prevents different HTTP clients from interpreting the
process environment differently.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


DEFAULT_DIRECT_DOMAINS: tuple[str, ...] = (
    ".cn",
    ".中国",
    "api.deepseek.com",
    "dashscope.aliyuncs.com",
    "api.moonshot.cn",
    "open.bigmodel.cn",
)


def _normalize_host(value: str) -> str:
    host = value.strip().rstrip(".").lower()
    if not host:
        return ""
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


def _normalize_domain(value: str) -> str:
    domain = value.strip().lower()
    if domain.startswith("*."):
        domain = domain[1:]
    if domain.startswith("."):
        return "." + _normalize_host(domain[1:])
    return _normalize_host(domain)


def _domains_from_environment() -> tuple[str, ...]:
    raw = os.environ.get("NOVA_DIRECT_DOMAINS", "")
    if not raw.strip():
        return DEFAULT_DIRECT_DOMAINS
    configured = tuple(
        domain
        for domain in (_normalize_domain(item) for item in raw.replace(";", ",").split(","))
        if domain
    )
    return tuple(dict.fromkeys((*DEFAULT_DIRECT_DOMAINS, *configured)))


def _host_matches_domain(host: str, domain: str) -> bool:
    if domain.startswith("."):
        return host.endswith(domain)
    return host == domain or host.endswith("." + domain)


@dataclass(frozen=True)
class OutboundNetworkPolicy:
    """Select direct or proxied transport using a normalized hostname."""

    proxy_url: str = ""
    direct_domains: tuple[str, ...] = DEFAULT_DIRECT_DOMAINS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "proxy_url",
            self.proxy_url.strip(),
        )
        object.__setattr__(
            self,
            "direct_domains",
            tuple(
                domain
                for domain in (_normalize_domain(item) for item in self.direct_domains)
                if domain
            )
            or DEFAULT_DIRECT_DOMAINS,
        )

    @classmethod
    def from_environment(cls, *, proxy_url: str | None = None) -> "OutboundNetworkPolicy":
        if proxy_url is None:
            proxy = (
                os.environ.get("NOVA_OUTBOUND_PROXY_URL", "").strip()
                or os.environ.get("HTTPS_PROXY", "").strip()
            )
        else:
            proxy = proxy_url.strip()
        return cls(proxy_url=proxy, direct_domains=_domains_from_environment())

    def is_direct_host(self, host: str) -> bool:
        normalized = _normalize_host(host)
        return bool(normalized) and any(
            _host_matches_domain(normalized, domain)
            for domain in self.direct_domains
        )

    def proxy_for_host(self, host: str) -> str | None:
        if not self.proxy_url or self.is_direct_host(host):
            return None
        return self.proxy_url

    def proxy_for_url(self, url: str) -> str | None:
        return self.proxy_for_host(urlsplit(url).hostname or "")
