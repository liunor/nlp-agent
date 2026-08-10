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
    request = WebFetchInput(url=url, extract_mode=extract_mode, max_chars=max_chars)
    try:
        service = build_fetch_service()
        response = await service.fetch(request)
    except WebAccessError as error:
        return json.dumps(error.to_error_dict(), ensure_ascii=False)
    return response.model_dump_json()
