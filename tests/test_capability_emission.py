"""Unit tests for tool/runtime capability event emission (Search, OCR, WebFetch)."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from core.model_runtime.contracts import (
    CircuitBreakerPolicy,
    ModelCapabilities,
    ModelDefinition,
    ModelPresetConfig,
    RetryPolicy,
    ThinkingConfig,
    TimeoutPolicy,
)
from core.model_runtime.reporters import InMemoryModelUsageReporter, ModelUsageReporterSlot
from core.model_runtime.runtime import ModelCandidate, ResilientChatModel
from core.model_runtime.usage import (
    CanonicalTokenUsage,
    UsageAttributionContext,
    bind_usage_attribution,
)
from core.tool_config import WebToolsConfig
from core.usage_metering.reporters import (
    InMemoryCapabilityUsageReporter,
    get_global_capability_reporter_slot,
)
from server.tools.vision.contracts import ImageAsset, ImageLanguage, ImageReference
from server.tools.vision.ocr import RapidOCRProvider
from server.tools.web.fetch import WebFetchInput, WebFetchService

UTC = timezone.utc


@pytest.fixture(autouse=True)
def clean_capability_reporter_slot():
    slot = get_global_capability_reporter_slot()
    old_reporter = slot.reporter
    old_req = slot.required
    reporter = InMemoryCapabilityUsageReporter()
    slot.configure(reporter, required=False)
    try:
        yield reporter
    finally:
        slot.configure(old_reporter, required=old_req)


@pytest.mark.asyncio
async def test_native_search_capability_event_emission(clean_capability_reporter_slot):
    cap_reporter = clean_capability_reporter_slot
    model_reporter = InMemoryModelUsageReporter()
    slot = ModelUsageReporterSlot(model_reporter)

    model_def = ModelDefinition(
        provider="qwen",
        model_id="qwen-plus",
        pricing_key="qwen/qwen-plus",
        context_window_tokens=32000,
        max_output_tokens=2048,
        capabilities=ModelCapabilities(thinking=False),
    )
    preset = ModelPresetConfig(
        model="qwen-plus",
        thinking=ThinkingConfig(enabled=False, effort="none"),
        timeouts=TimeoutPolicy(connect_s=1, first_token_s=1, stream_idle_s=1, total_s=2),
        retry=RetryPolicy(max_attempts=1, base_delay_s=0, max_delay_s=0, jitter="none"),
        circuit_breaker=CircuitBreakerPolicy(failure_threshold=5, cooldown_s=1),
    )

    candidate = ModelCandidate(
        preset_name="qwen-plus",
        provider_name="qwen",
        model_name="qwen-plus",
        definition=model_def,
        preset=preset,
        model=MagicMock(),
    )

    client = ResilientChatModel(
        [candidate],
        reporter_slot=slot,
    )

    usage = CanonicalTokenUsage(
        input_tokens=500,
        output_tokens=200,
        total_tokens=700,
        provider_usage_details={"plugins.search.count": 2},
        source="provider",
    )
    attr = UsageAttributionContext(
        request_id="req-search-1",
        user_id="user-1",
        turn_id="turn-1",
        reservation_id="res-1",
        purpose="worker",
    )

    with bind_usage_attribution(attr):
        inv, _ = client._prepare_invocation(candidate, attempt=1, fallback_index=0)
        await client._report_attempt(
            invocation=inv,
            usage=usage,
            status="succeeded",
        )

    # Verify model reporter received attempt
    assert len(model_reporter.events) == 1

    # Verify capability reporter received search event
    assert len(cap_reporter.events) == 1
    event = cap_reporter.events[0]
    assert event.capability_type == "search"
    assert event.operation_id == f"{inv.operation_id}:native-search"
    assert event.parent_operation_id == inv.operation_id
    assert event.reservation_id == "res-1"
    assert event.pricing_key == "qwen/cn-beijing/web-search/turbo"
    assert len(event.items) == 1
    assert event.items[0].meter == "search.requests"
    assert event.items[0].quantity == 2


@pytest.mark.asyncio
async def test_rapid_ocr_capability_event_emission(clean_capability_reporter_slot):
    from io import BytesIO
    import hashlib
    from PIL import Image

    cap_reporter = clean_capability_reporter_slot

    provider = RapidOCRProvider(engine_factory=lambda: MagicMock(return_value=[]))
    img = Image.new("RGB", (100, 100), "white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    png_bytes = buffer.getvalue()
    image = ImageAsset(
        path=Path("test.png"),
        data=png_bytes,
        reference=ImageReference(
            file_name="test.png",
            media_type="image/png",
            size_bytes=len(png_bytes),
            width=100,
            height=100,
            sha256=hashlib.sha256(png_bytes).hexdigest(),
        ),
    )

    attr = UsageAttributionContext(
        request_id="req-ocr-1",
        user_id="user-ocr",
        turn_id="turn-ocr",
        reservation_id="res-ocr",
        purpose="vision",
    )
    with bind_usage_attribution(attr):
        await provider.extract(image, language="zh")

    assert len(cap_reporter.events) == 1
    event = cap_reporter.events[0]
    assert event.capability_type == "ocr"
    assert event.provider == "internal"
    assert event.pricing_key == "internal/rapidocr/v1"
    assert event.reservation_id == "res-ocr"
    assert len(event.items) == 1
    assert event.items[0].meter == "ocr.pages"
    assert event.items[0].quantity == 1
    assert "data" not in event.raw_usage  # Never leaks image binary


@pytest.mark.asyncio
async def test_web_fetch_capability_event_emission(clean_capability_reporter_slot):
    cap_reporter = clean_capability_reporter_slot

    cfg = WebToolsConfig()
    service = WebFetchService(config=cfg)

    # Mock _download to avoid real network call
    async def fake_download(entry, as_markdown=True):
        return {
            "title": "Test Page",
            "final_url": "https://example.com/page",
            "status_code": 200,
            "content_type": "text/html",
            "extractor": "html",
            "text": "hello world",
            "redirect_count": 0,
            "response_bytes": 1024,
            "warnings": [],
        }

    service._download = fake_download

    attr = UsageAttributionContext(
        request_id="req-fetch-1",
        user_id="user-fetch",
        turn_id="turn-fetch",
        reservation_id="res-fetch",
        purpose="worker",
    )
    with bind_usage_attribution(attr):
        await service.fetch(WebFetchInput(url="https://example.com/page"))

    assert len(cap_reporter.events) == 1
    event = cap_reporter.events[0]
    assert event.capability_type == "web_fetch"
    assert event.provider == "internal"
    assert event.pricing_key == "internal/web-fetch/v1"
    assert event.reservation_id == "res-fetch"
    meters = {item.meter: item.quantity for item in event.items}
    assert meters["web_fetch.requests"] == 1
    assert meters["web_fetch.bytes"] == 1024
    assert "hello world" not in str(event.raw_usage)  # Never stores web content
