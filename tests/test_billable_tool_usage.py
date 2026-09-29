"""Integration tests for non-model feature usage entering the existing Reporter."""

import json
from types import SimpleNamespace

import pytest

import core.model_runtime.factory as factory_module
from core.model_runtime.reporters import (
    InMemoryModelUsageReporter,
    ModelUsageReporterSlot,
)
from core.model_runtime.usage import UsageAttributionContext, bind_usage_attribution
from core.tool_config import VisionVLMConfig
from server.tools.web.contracts import WebAccessError


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
async def test_real_web_fetch_wrapper_reserves_before_download_and_settles(monkeypatch):
    import server.tools.api.web_fetch_tool as wrapper

    reporter = _configure_reporter(monkeypatch)

    class Service:
        async def fetch(self, _request, *, before_download):
            assert reporter.feature_reservations == []
            await before_download()
            assert reporter.feature_reservations[0].feature_usage.link_pages == 1
            return SimpleNamespace(
                model_dump_json=lambda: json.dumps(
                    {
                        "final_url": "https://example.com/page",
                        "cache_hit": False,
                    }
                )
            )

    monkeypatch.setattr(wrapper, "build_fetch_service", Service)
    with bind_usage_attribution(_attribution()):
        output = await wrapper.web_fetch.ainvoke(
            {"url": "https://example.com/page"}
        )

    assert json.loads(output)["cache_hit"] is False
    assert len(reporter.feature_reservations) == 1
    assert len(reporter.events) == 1
    assert reporter.events[0][0].operation_id == reporter.feature_reservations[0].operation_id


@pytest.mark.asyncio
async def test_real_web_fetch_wrapper_releases_hold_when_download_fails(monkeypatch):
    import server.tools.api.web_fetch_tool as wrapper

    reporter = _configure_reporter(monkeypatch)

    class Service:
        async def fetch(self, _request, *, before_download):
            await before_download()
            raise WebAccessError("http_error", "failed")

    monkeypatch.setattr(wrapper, "build_fetch_service", Service)
    with bind_usage_attribution(_attribution()):
        output = await wrapper.web_fetch.ainvoke(
            {"url": "https://example.com/fail"}
        )

    assert json.loads(output)["code"] == "http_error"
    assert len(reporter.feature_reservations) == 1
    assert len(reporter.released_feature_reservations) == 1
    assert reporter.events == []


@pytest.mark.asyncio
async def test_real_web_fetch_wrapper_does_not_download_after_quota_rejection(
    monkeypatch,
):
    import server.tools.api.web_fetch_tool as wrapper

    class RejectingReporter(InMemoryModelUsageReporter):
        async def reserve_feature_usage(self, _invocation):
            raise RuntimeError("quota exhausted")

    reporter = RejectingReporter()
    slot = ModelUsageReporterSlot(reporter, required=True)
    monkeypatch.setattr(
        factory_module,
        "get_global_model_factory",
        lambda: SimpleNamespace(reporter_slot=slot),
    )
    downloaded = False

    class Service:
        async def fetch(self, _request, *, before_download):
            nonlocal downloaded
            await before_download()
            downloaded = True
            raise AssertionError("download must not start")

    monkeypatch.setattr(wrapper, "build_fetch_service", Service)
    with bind_usage_attribution(_attribution()):
        with pytest.raises(RuntimeError, match="quota exhausted"):
            await wrapper.web_fetch.ainvoke(
                {"url": "https://example.com/denied"}
            )

    assert downloaded is False


@pytest.mark.asyncio
async def test_real_image_wrapper_uses_resolved_dimensions_for_ocr_hold(monkeypatch):
    import server.tools.api.image_analyze_tool as wrapper

    reporter = _configure_reporter(monkeypatch)
    config = VisionVLMConfig(
        standard_image_max_pixels=1_000_000,
        high_image_max_pixels=4_000_000,
    )
    monkeypatch.setattr(
        wrapper,
        "get_vision_config",
        lambda: SimpleNamespace(vlm=config),
    )

    class Service:
        async def analyze(self, _request, *, before_provider):
            asset = SimpleNamespace(
                reference=SimpleNamespace(width=2_000, height=1_000)
            )
            await before_provider(asset, "ocr", "ocr")
            return SimpleNamespace(
                model_dump_json=lambda **_kwargs: json.dumps(
                    {"route": "ocr", "task_executed": "ocr"}
                )
            )

    monkeypatch.setattr(wrapper, "build_image_analyze_service", lambda **_kwargs: Service())
    with bind_usage_attribution(_attribution()):
        output = await wrapper.image_analyze.ainvoke(
            {"image": ".data/uploads/example.png", "task": "ocr"},
            config={
                "configurable": {
                    "thread_id": "conversation-feature-1",
                    "user_id": "user-feature-1",
                    "workspace_id": "workspace-feature-1",
                    "worker_id": "worker-feature-1",
                    "channel": "worker",
                }
            },
        )

    assert json.loads(output)["route"] == "ocr"
    assert reporter.feature_reservations[0].feature_usage.image_units == 2
    assert reporter.events[0][0].feature_usage.image_units == 2
