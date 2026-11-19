"""LangChain wrapper exposing web_search through the unified tool runtime."""

from __future__ import annotations

import json

from langchain_core.tools import tool
from pydantic import ValidationError

from server.tools.web.contracts import WebAccessError, WebSearchInput
from server.tools.web.search import build_search_service


@tool("web_search", args_schema=WebSearchInput)
async def web_search(
    query: str,
    ori_question: str | None = None,
    max_results: int = 5,
) -> str:
    """使用 DeepSeek Search API 搜索公开互联网，返回含高密度长摘要（summary）与重排质量分（rerank_score）的结构化结果。"""
    # Keep quota/model-runtime initialization outside the tool registration
    # import path so DB-less processes can import tool definitions cleanly.
    from core.model_runtime.usage import BillableFeatureUsage
    from server.quota.reporting import (
        begin_billable_tool_usage,
        cancel_billable_tool_usage,
        complete_billable_tool_usage,
    )

    try:
        request = WebSearchInput(
            query=query,
            ori_question=ori_question,
            max_results=max_results,
        )
    except ValidationError as error:
        return json.dumps(
            {"error": str(error), "code": "invalid_input"},
            ensure_ascii=False,
        )

    billing_invocation = None

    async def reserve_cache_miss() -> None:
        nonlocal billing_invocation
        billing_invocation = await begin_billable_tool_usage(
            tool_name="web_search",
            feature_usage=BillableFeatureUsage(search_calls=1),
        )

    try:
        service = build_search_service()
        response = await service.search(request, before_request=reserve_cache_miss)
    except WebAccessError as error:
        await cancel_billable_tool_usage(billing_invocation)
        return json.dumps(error.to_error_dict(), ensure_ascii=False)
    except BaseException:
        await cancel_billable_tool_usage(billing_invocation)
        raise

    # DeepSeek Search API does not charge for empty results or cached responses.
    if response.cache_hit or not response.results:
        await cancel_billable_tool_usage(billing_invocation)
    else:
        await complete_billable_tool_usage(billing_invocation)
    return response.model_dump_json()
