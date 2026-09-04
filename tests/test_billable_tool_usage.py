"""Integration tests for non-model feature usage entering the existing Reporter."""

from types import SimpleNamespace

import pytest

import core.model_runtime.factory as factory_module
from core.model_runtime.reporters import (
    InMemoryModelUsageReporter,
    ModelUsageReporterSlot,
)
from core.model_runtime.usage import UsageAttributionContext, bind_usage_attribution
from core.tool_config import VisionVLMConfig
from core.tool_runtime import ToolExecutionResult
from server.quota.reporting import report_billable_tool_execution
import server.tools.vision.config as vision_config_module


def _attribution() -> UsageAttributionContext:
    return UsageAttributionContext(
        request_id="request-feature-1",
        user_id="user-feature-1",
        workspace_id="workspace-feature-1",
        conversation_id="conversation-feature-1",
        turn_id="turn-feature-1",
        reservation_id="reservation-feature-1",
        worker_id="worker-feature-1",
        purpose="worker",
    )


def _configure_reporter(monkeypatch) -> InMemoryModelUsageReporter:
    reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(reporter)
    monkeypatch.setattr(
        factory_module,
        "get_global_model_factory",
        lambda: SimpleNamespace(reporter_slot=slot),
    )
    return reporter


@pytest.mark.asyncio
async def test_web_fetch_reports_one_page_but_cache_hit_reports_nothing(monkeypatch):
    reporter = _configure_reporter(monkeypatch)
    fresh = ToolExecutionResult(
        tool_name="web_fetch",
        ok=True,
        output={"final_url": "https://example.com/page", "cache_hit": False},
        duration_ms=12,
    )
    cached = fresh.model_copy(
        update={"output": {"final_url": "https://example.com/page", "cache_hit": True}}
    )

    with bind_usage_attribution(_attribution()):
        await report_billable_tool_execution(tool_call_id="call-fresh", execution=fresh)
        await report_billable_tool_execution(tool_call_id="call-cached", execution=cached)

    assert len(reporter.events) == 1
    invocation, usage, outcome = reporter.events[0]
    assert invocation.identity.pricing_key == "feature/link-read"
    assert invocation.feature_usage.link_pages == 1
    assert usage.total_tokens == 0
    assert usage.source == "provider"
    assert outcome.status == "succeeded"


@pytest.mark.asyncio
async def test_ocr_only_route_reports_tiered_image_unit_without_vlm_duplicate(
    monkeypatch,
):
    reporter = _configure_reporter(monkeypatch)
    monkeypatch.setattr(
        vision_config_module,
        "get_vision_config",
        lambda: SimpleNamespace(
            vlm=VisionVLMConfig(
                standard_image_max_pixels=1_000_000,
                high_image_max_pixels=4_000_000,
            )
        ),
    )
    ocr = ToolExecutionResult(
        tool_name="image_analyze",
        ok=True,
        output={
            "route": "ocr",
            "task_executed": "ocr",
            "input": {"width": 2_000, "height": 1_000},
        },
    )
    vlm = ocr.model_copy(update={"output": {"route": "vlm"}})

    with bind_usage_attribution(_attribution()):
        await report_billable_tool_execution(tool_call_id="call-ocr", execution=ocr)
        await report_billable_tool_execution(tool_call_id="call-vlm", execution=vlm)

    assert len(reporter.events) == 1
    invocation, usage, _outcome = reporter.events[0]
    assert invocation.attribution.purpose == "vision"
    assert invocation.identity.pricing_key == "feature/image-understanding"
    assert invocation.feature_usage.image_units == 2
    assert usage.total_tokens == 0
