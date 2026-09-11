"""HTTP clients for model adapters with explicit proxy routing."""

from __future__ import annotations

from typing import Any

import httpx

from core.outbound_network import OutboundNetworkPolicy


def model_http_client_kwargs(
    base_url: str,
    timeout: httpx.Timeout,
    *,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """Build sync and async clients that do not inherit ambient proxy state."""

    policy = OutboundNetworkPolicy.from_environment(proxy_url=proxy_url)
    proxy = policy.proxy_for_url(base_url)
    return {
        "http_client": httpx.Client(
            proxy=proxy,
            timeout=timeout,
            trust_env=False,
        ),
        "http_async_client": httpx.AsyncClient(
            proxy=proxy,
            timeout=timeout,
            trust_env=False,
        ),
    }
