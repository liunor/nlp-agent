"""LangChain tool exposing academic_search through the unified tool runtime."""

from __future__ import annotations

from typing import Literal
from langchain_core.tools import tool

from server.tools.academic.contracts import AcademicSearchInput, AcademicSource
from server.tools.academic.service import get_academic_search_service


@tool("academic_search", args_schema=AcademicSearchInput)
async def academic_search(
    query: str,
    query_en: str | None = None,
    sources: list[AcademicSource] | None = None,
    max_results: int = 5,
    year_from: int | None = None,
    year_to: int | None = None,
    sort: Literal["relevance", "recent"] = "relevance",
) -> str:
    """检索国际学术论文元数据和官方出处，返回可核验的结构化结果。

    source_status=skipped 表示按配置跳过，不得凭记忆补链接。
    没有可核验结果时须说明无法核验学术出处；部分失败仅引用成功来源结果。
    """
    service = get_academic_search_service()
    request = AcademicSearchInput(
        query=query,
        query_en=query_en,
        sources=sources or [],
        max_results=max_results,
        year_from=year_from,
        year_to=year_to,
        sort=sort,
    )
    response = await service.search(request)
    return response.model_dump_json()
