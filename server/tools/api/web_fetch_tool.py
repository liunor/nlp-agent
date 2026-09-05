"""LangChain wrapper exposing web_fetch through the unified tool runtime."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from server.tools.web.contracts import WebAccessError, WebFetchInput
from server.tools.web.fetch import build_fetch_service


@tool("web_fetch", args_schema=WebFetchInput)
async def web_fetch(
    url: str, extract_mode: str = "markdown", max_chars: int = 20_000
) -> str:
    """读取指定的公开 http(s) 链接，抽取可引用正文，返回带引用的结构化结果。"""
    # Tool registration is also imported by DB-less sandbox processes. Load
    # accounting dependencies only when the tool is actually executed.
    from core.model_runtime.usage import BillableFeatureUsage
    from server.quota.reporting import (
        begin_billable_tool_usage,
        cancel_billable_tool_usage,
        complete_billable_tool_usage,
    )

    request = WebFetchInput(url=url, extract_mode=extract_mode, max_chars=max_chars)
    billing_invocation = None

    async def reserve_cache_miss() -> None:
        nonlocal billing_invocation
        billing_invocation = await begin_billable_tool_usage(
            tool_name="web_fetch",
            feature_usage=BillableFeatureUsage(link_pages=1),
        )

    try:
        service = build_fetch_service()
        response = await service.fetch(request, before_download=reserve_cache_miss)
    except WebAccessError as error:
        await cancel_billable_tool_usage(billing_invocation)
        return json.dumps(error.to_error_dict(), ensure_ascii=False)
    except BaseException:
        await cancel_billable_tool_usage(billing_invocation)
        raise
    await complete_billable_tool_usage(billing_invocation)
    return response.model_dump_json()
