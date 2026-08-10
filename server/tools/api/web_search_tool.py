"""LangChain wrapper exposing web_search through the unified tool runtime."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from server.tools.web.contracts import WebAccessError, WebSearchInput
from server.tools.web.search import build_search_service


@tool("web_search", args_schema=WebSearchInput)
async def web_search(
    query: str,
    max_results: int = 5,
    provider: str | None = None,
    domains: list[str] | None = None,
    freshness: str | None = None,
) -> str:
    """搜索需要最新公开信息或外部资料的问题，返回带来源的结构化结果。"""
    request = WebSearchInput(
        query=query,
        max_results=max_results,
        provider=provider,
        domains=domains or [],
        freshness=freshness,
    )
    try:
        service = build_search_service()
        response = await service.search(request)
    except WebAccessError as error:
        return json.dumps(error.to_error_dict(), ensure_ascii=False)
    return response.model_dump_json()
