from __future__ import annotations

import httpx
import pytest

from core.outbound_network import OutboundNetworkPolicy
from core.model_runtime.network import model_http_client_kwargs


def test_domestic_hosts_bypass_proxy_and_external_hosts_use_it() -> None:
    policy = OutboundNetworkPolicy(proxy_url="http://host.docker.internal:7897")

    assert policy.proxy_for_url("https://api.deepseek.com/v1") is None
    assert policy.proxy_for_url("https://www.example.cn/search") is None
    assert (
        policy.proxy_for_url("https://export.arxiv.org/api/query")
        == "http://host.docker.internal:7897"
    )


def test_direct_domain_matching_is_boundary_safe() -> None:
    policy = OutboundNetworkPolicy(
        proxy_url="http://proxy.example:8080",
        direct_domains=("api.deepseek.com",),
    )

    assert policy.proxy_for_host("api.deepseek.com") is None
    assert policy.proxy_for_host("sub.api.deepseek.com") is None
    assert policy.proxy_for_host("api.deepseek.com.attacker.example") == (
        "http://proxy.example:8080"
    )


def test_environment_direct_domains_extend_safe_defaults(monkeypatch) -> None:
    monkeypatch.setenv("NOVA_DIRECT_DOMAINS", "internal.example")

    policy = OutboundNetworkPolicy.from_environment(
        proxy_url="http://proxy.example:8080"
    )

    assert policy.proxy_for_host("api.deepseek.com") is None
    assert policy.proxy_for_host("service.internal.example") is None
    assert policy.proxy_for_host("export.arxiv.org") == "http://proxy.example:8080"


@pytest.mark.asyncio
async def test_model_clients_disable_environment_proxy_for_domestic_endpoint() -> None:
    clients = model_http_client_kwargs(
        "https://api.deepseek.com/v1",
        httpx.Timeout(10.0),
        proxy_url="http://proxy.example:8080",
    )

    assert clients["http_client"]._transport._pool._proxy is None
    assert clients["http_async_client"]._transport._pool._proxy is None
    clients["http_client"].close()
    await clients["http_async_client"].aclose()
