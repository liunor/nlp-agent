"""RapidOCR adapter mapping, retry, and wiring tests."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from core.session_context import SessionContext
from core.tool_config import VisionOCRConfig, VisionToolsConfig
from server.tools.vision import service as service_module
from server.tools.vision.contracts import (
    ImageAsset,
    ImageReference,
    VisionError,
    VisionErrorCode,
)
from server.tools.vision.ocr import RapidOCRProvider
from server.tools.vision.providers import UnavailableOCRProvider


class FakeEngine:
    """Queued-output stand-in for the RapidOCR engine."""

    def __init__(self, *outputs: list) -> None:
        self.outputs = list(outputs)
        self.call_shapes: list[tuple[int, ...]] = []

    def __call__(self, array) -> tuple[list, list]:
        self.call_shapes.append(tuple(array.shape))
        entries = self.outputs.pop(0) if self.outputs else []
        return entries, [0.01]


def _asset(width: int = 400, height: int = 200, text: str | None = None) -> ImageAsset:
    image = Image.new("RGB", (width, height), "white")
    if text:
        ImageDraw.Draw(image).text((20, 40), text, fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    reference = ImageReference(
        file_name="sample.png",
        media_type="image/png",
        size_bytes=len(data),
        width=width,
        height=height,
        sha256=hashlib.sha256(data).hexdigest(),
    )
    return ImageAsset(path=Path("sample.png"), data=data, reference=reference)


def _entry(points, text, score):
    return [points, text, score]


async def test_maps_engine_entries_to_blocks() -> None:
    engine = FakeEngine([
        _entry([[10, 5], [70, 5], [70, 25], [10, 25]], "第一行", 0.9),
        _entry([[10, 35], [70, 35], [70, 55], [10, 55]], "second", 1.4),
    ])
    provider = RapidOCRProvider(engine_factory=lambda: engine)

    result = await provider.extract(_asset(), language="zh")

    assert result.text == "第一行\nsecond"
    assert result.language == "zh"
    assert [block.block_id for block in result.blocks] == ["block-1", "block-2"]
    first = result.blocks[0]
    assert (first.bbox.x, first.bbox.y) == (10, 5)
    assert (first.bbox.width, first.bbox.height) == (60, 20)
    assert first.confidence == pytest.approx(0.9)
    # Scores are clamped into [0, 1] before reaching the contract.
    assert result.blocks[1].confidence == 1.0
    assert result.confidence == pytest.approx((0.9 + 1.0) / 2)
    assert engine.call_shapes == [(200, 400, 3)]


async def test_empty_engine_result_yields_empty_ocr_result() -> None:
    provider = RapidOCRProvider(
        retry_low_confidence_once=False,
        engine_factory=lambda: FakeEngine([]),
    )

    result = await provider.extract(_asset(), language="auto")

    assert result.text == ""
    assert result.blocks == []
    assert result.confidence is None


async def test_downscaled_inference_maps_boxes_back_to_original_space() -> None:
    engine = FakeEngine([
        _entry([[10, 5], [30, 5], [30, 15], [10, 15]], "text", 0.95),
    ])
    provider = RapidOCRProvider(
        max_dimension=100,
        retry_low_confidence_once=False,
        engine_factory=lambda: engine,
    )

    result = await provider.extract(_asset(width=400, height=200), language="auto")

    # 400x200 downscales to 100x50 (ratio 0.25); boxes must be original-space.
    assert engine.call_shapes == [(50, 100, 3)]
    bbox = result.blocks[0].bbox
    assert (bbox.x, bbox.y) == (40, 20)
    assert (bbox.width, bbox.height) == (80, 40)


async def test_low_confidence_retries_once_with_upscale_and_keeps_better() -> None:
    engine = FakeEngine(
        [_entry([[10, 5], [70, 5], [70, 25], [10, 25]], "blurry", 0.3)],
        [_entry([[40, 80], [160, 80], [160, 120], [40, 120]], "sharp", 0.9)],
    )
    provider = RapidOCRProvider(
        confidence_threshold=0.75,
        engine_factory=lambda: engine,
    )

    result = await provider.extract(_asset(width=400, height=200), language="auto")

    assert engine.call_shapes == [(200, 400, 3), (400, 800, 3)]
    assert result.text == "sharp"
    assert result.confidence == pytest.approx(0.9)
    # Retry coordinates are in 2x space and must be halved back.
    bbox = result.blocks[0].bbox
    assert (bbox.x, bbox.y) == (20, 40)
    assert (bbox.width, bbox.height) == (60, 20)


async def test_low_confidence_retry_keeps_original_when_not_better() -> None:
    engine = FakeEngine(
        [_entry([[10, 5], [70, 5], [70, 25], [10, 25]], "original", 0.6)],
        [_entry([[20, 10], [140, 10], [140, 50], [20, 50]], "worse", 0.4)],
    )
    provider = RapidOCRProvider(
        confidence_threshold=0.75,
        engine_factory=lambda: engine,
    )

    result = await provider.extract(_asset(), language="auto")

    assert len(engine.call_shapes) == 2
    assert result.text == "original"
    assert result.confidence == pytest.approx(0.6)
    assert (result.blocks[0].bbox.x, result.blocks[0].bbox.y) == (10, 5)


async def test_confident_result_skips_retry() -> None:
    engine = FakeEngine([
        _entry([[10, 5], [70, 5], [70, 25], [10, 25]], "ok", 0.99),
    ])
    provider = RapidOCRProvider(engine_factory=lambda: engine)

    result = await provider.extract(_asset(), language="auto")

    assert result.confidence == pytest.approx(0.99)
    assert len(engine.call_shapes) == 1


async def test_retry_skipped_when_upscale_would_exceed_max_dimension() -> None:
    engine = FakeEngine([
        _entry([[10, 5], [70, 5], [70, 25], [10, 25]], "low", 0.3)],
    )
    provider = RapidOCRProvider(
        max_dimension=100,
        confidence_threshold=0.75,
        engine_factory=lambda: engine,
    )

    result = await provider.extract(_asset(width=400, height=200), language="auto")

    # Downscaled to 100x50; a 2x retry would reach 200px > max_dimension.
    assert len(engine.call_shapes) == 1
    assert result.confidence == pytest.approx(0.3)


async def test_retry_disabled_by_flag() -> None:
    engine = FakeEngine([
        _entry([[10, 5], [70, 5], [70, 25], [10, 25]], "low", 0.3)],
    )
    provider = RapidOCRProvider(
        retry_low_confidence_once=False,
        engine_factory=lambda: engine,
    )

    result = await provider.extract(_asset(), language="auto")

    assert len(engine.call_shapes) == 1
    assert result.confidence == pytest.approx(0.3)


async def test_engine_failure_surfaces_stable_provider_unavailable() -> None:
    def broken_factory():
        raise ImportError("No module named 'rapidocr_onnxruntime'")

    provider = RapidOCRProvider(engine_factory=broken_factory)

    with pytest.raises(VisionError) as excinfo:
        await provider.extract(_asset(), language="auto")

    assert excinfo.value.code == VisionErrorCode.PROVIDER_UNAVAILABLE


async def test_malformed_engine_output_surfaces_provider_unavailable() -> None:
    class ExplodingEngine:
        def __call__(self, array):
            raise RuntimeError("onnxruntime crashed")

    provider = RapidOCRProvider(engine_factory=lambda: ExplodingEngine())

    with pytest.raises(VisionError) as excinfo:
        await provider.extract(_asset(), language="auto")

    assert excinfo.value.code == VisionErrorCode.PROVIDER_UNAVAILABLE


async def test_real_engine_recognizes_rendered_text() -> None:
    pytest.importorskip("rapidocr_onnxruntime")
    provider = RapidOCRProvider()

    result = await provider.extract(
        _asset(width=400, height=120, text="Hello NLP 123"), language="auto"
    )

    assert "hello" in result.text.lower()
    assert result.blocks and result.confidence is not None
    assert result.confidence > 0.5


def test_default_service_wires_rapidocr_provider() -> None:
    context = SessionContext(
        session_id="session-1",
        user_id="learner-1",
        workspace_id="class-1",
    )

    service = service_module.build_image_analyze_service(context=context)

    assert isinstance(service.ocr_provider, RapidOCRProvider)
    assert service.ocr_provider._engine is None  # engine stays lazy


def test_service_wires_unavailable_ocr_when_provider_is_none(monkeypatch) -> None:
    import server.tools.vision.config as vision_config_module

    config = VisionToolsConfig(ocr=VisionOCRConfig(provider="none"))
    # build_image_analyze_service imports get_vision_config lazily at call time.
    monkeypatch.setattr(vision_config_module, "get_vision_config", lambda: config)

    context = SessionContext(
        session_id="session-1",
        user_id="learner-1",
        workspace_id="class-1",
    )
    service = service_module.build_image_analyze_service(context=context)

    assert isinstance(service.ocr_provider, UnavailableOCRProvider)


def test_ocr_provider_config_defaults_to_rapidocr() -> None:
    assert VisionOCRConfig().provider == "rapidocr"
    assert VisionOCRConfig(provider=" none ").provider == "none"


def test_ocr_provider_config_rejects_unknown_provider() -> None:
    with pytest.raises(ValidationError):
        VisionOCRConfig(provider="paddleocr")
