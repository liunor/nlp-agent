"""Tests for academic_search LangChain tool, schema, permissions, and billing isolation."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

import core.model_runtime.factory as factory_module
from core.model_runtime.reporters import InMemoryModelUsageReporter, ModelUsageReporterSlot
from core.model_runtime.usage import UsageAttributionContext, bind_usage_attribution
from core.skill_loader import SkillLoader
from core.tool_config import load_agent_runtime_config
from core.tool_registry import physical_tool_manager
from server.tools.academic.contracts import (
    AcademicAuthor,
    AcademicIdentifiers,
    AcademicPaper,
    AcademicSearchInput,
    AcademicSearchResponse,
    AcademicSourceStatus,
)
from server.tools.api import academic_search_tool as tool_module
from server.tools.api.academic_search_tool import academic_search
from server.tools.tool_manager import register_builtin_tools


def test_academic_search_tool_schema():
    assert academic_search.name == "academic_search"
    assert academic_search.args_schema == AcademicSearchInput
    fields = AcademicSearchInput.model_fields
    assert "query" in fields
    assert "query_en" in fields
    assert "sources" in fields
    assert "max_results" in fields
    assert "year_from" in fields
    assert "year_to" in fields
    assert "sort" in fields


@pytest.mark.asyncio
async def test_academic_search_tool_returns_valid_json(monkeypatch):
    mock_response = AcademicSearchResponse(
        query="Transformer",
        query_used="Transformer",
        papers=[],
        source_status=[
            AcademicSourceStatus(source="arxiv", status="ok")
        ],
        partial=False,
        retrieved_at="2026-09-06T00:00:00Z",
    )

    class FakeService:
        async def search(self, request: AcademicSearchInput) -> AcademicSearchResponse:
            return mock_response

    monkeypatch.setattr(tool_module, "get_academic_search_service", lambda: FakeService())

    output = await academic_search.ainvoke({"query": "Transformer"})
    assert isinstance(output, str)
    parsed = json.loads(output)
    assert parsed["query"] == "Transformer"
    assert parsed["partial"] is False
    assert parsed["source_status"][0]["source"] == "arxiv"


@pytest.mark.asyncio
async def test_academic_search_tool_no_billable_usage(monkeypatch):
    # Ensure offline execution and that begin_billable_tool_usage is NOT called
    import server.quota.reporting as reporting

    mock_service = AsyncMock()
    mock_service.search.return_value = AcademicSearchResponse(
        query="Transformer",
        query_used="Transformer",
        papers=[],
        source_status=[AcademicSourceStatus(source="arxiv", status="ok")],
        partial=False,
        retrieved_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(tool_module, "get_academic_search_service", lambda: mock_service)

    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)
    monkeypatch.setattr(
        factory_module,
        "get_global_model_factory",
        lambda: SimpleNamespace(reporter_slot=slot),
    )

    billable_spy = AsyncMock()
    monkeypatch.setattr(reporting, "begin_billable_tool_usage", billable_spy)

    attribution = UsageAttributionContext(
        request_id="req-academic-1",
        user_id="user-academic-1",
        workspace_id="ws-academic-1",
        conversation_id="conv-academic-1",
        turn_id="turn-academic-1",
        purpose="coordinator",
    )
    with bind_usage_attribution(attribution):
        output = await academic_search.ainvoke({"query": "Transformer"})

    assert billable_spy.call_count == 0
    assert len(reporter.feature_reservations) == 0
    assert len(reporter.events) == 0
    assert mock_service.search.call_count == 1
    assert "Transformer" in output


def test_coordinator_toolset_includes_academic_search_and_workers_do_not():
    from server.tools.task_stop_tool import task_stop_tool
    from server.tools.worker_tool import send_message, spawn_worker

    physical_tool_manager.refresh_config()
    register_builtin_tools(physical_tool_manager.runtime.catalog)

    # Coordinator toolset
    coord_toolset = physical_tool_manager.get_coordinator_toolset(
        [spawn_worker, send_message, task_stop_tool]
    )
    assert "academic_search" in coord_toolset.names

    # General worker without explicit grant cannot access academic_search
    unauthorized = physical_tool_manager.get_worker_toolset(
        allowed_names=(),
        capabilities=(),
        profile="standard-worker",
        inherit_policy=False,
    )
    assert "academic_search" not in unauthorized.names

    # Web researcher and reader also do not receive academic_search
    loader = SkillLoader()
    researcher = loader.resolve_profile("web_researcher")
    researcher_tools = physical_tool_manager.get_worker_toolset(
        allowed_names=researcher.allowed_tools,
        capabilities=researcher.capabilities,
        profile=researcher.name,
        inherit_policy=researcher.inherit_tool_policy,
    )
    assert "academic_search" not in researcher_tools.names
