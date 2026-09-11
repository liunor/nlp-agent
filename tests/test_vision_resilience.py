import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from server.tools.vision import ocr as ocr_module, service as service_module
from server.tools.vision.contracts import (
    ImageAnalyzeInput, ImageAsset, ImageReference, OCRResult,
    VisionError, VisionErrorCode, VisionModelResult, VisionSignals,
)
from server.tools.vision.service import ImageAnalyzeService


@pytest.fixture
def service():
    asset = ImageAsset(
        path=Path("test.png"), data=b"test",
        reference=ImageReference(file_name="test.png", media_type="image/png",
                                 size_bytes=4, width=100, height=100, sha256="0" * 64),
    )
    return ImageAnalyzeService(
        resolver=SimpleNamespace(resolve=lambda _: asset),
        ocr_provider=SimpleNamespace(extract=AsyncMock(return_value=OCRResult(text="金额 100", confidence=0.9))),
        vlm_provider=SimpleNamespace(analyze=AsyncMock(return_value=VisionModelResult(summary="金额是 100"))),
        signal_provider=SimpleNamespace(detect=AsyncMock(return_value=VisionSignals(image_category="document"))),
    )


@pytest.mark.parametrize("code", [
    VisionErrorCode.PROVIDER_QUOTA_EXHAUSTED, VisionErrorCode.PROVIDER_AUTH_FAILED,
    VisionErrorCode.PROVIDER_UNAVAILABLE, VisionErrorCode.INVALID_PROVIDER_RESPONSE,
])
async def test_fusion_retains_ocr_without_claiming_question_answered(service, code):
    service.vlm_provider.analyze.side_effect = VisionError(code, "语义服务不可用")
    result = await service.analyze(ImageAnalyzeInput(image="test.png", question="金额多少？"))
    assert result.degraded
    assert (result.task_executed, result.route) == ("ocr", "ocr")
    assert result.ocr.text == "金额 100"
    assert result.confidence.semantic is None
    assert code.value in result.warnings[0]
    assert "未完成" in result.warnings[0]


async def test_fusion_continues_without_failed_ocr(service):
    service.ocr_provider.extract.side_effect = VisionError(VisionErrorCode.PROVIDER_UNAVAILABLE, "OCR offline")
    result = await service.analyze(ImageAnalyzeInput(image="test.png", task="table"))
    assert result.degraded and result.route == "vlm"
    assert result.ocr is None
    assert service.vlm_provider.analyze.call_args.kwargs["ocr_context"] is None


async def test_both_providers_failing_remains_an_error(service):
    service.ocr_provider.extract.side_effect = VisionError(VisionErrorCode.PROVIDER_UNAVAILABLE, "OCR offline")
    service.vlm_provider.analyze.side_effect = VisionError(VisionErrorCode.PROVIDER_AUTH_FAILED, "No access")
    with pytest.raises(VisionError, match="No access"):
        await service.analyze(ImageAnalyzeInput(image="test.png", task="table"))


async def test_cancellation_and_input_errors_do_not_become_success(service):
    service.vlm_provider.analyze.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await service.analyze(ImageAnalyzeInput(image="test.png", task="table"))
    service.vlm_provider.analyze.side_effect = VisionError(VisionErrorCode.FILE_TOO_LARGE, "Too large")
    with pytest.raises(VisionError, match="Too large"):
        await service.analyze(ImageAnalyzeInput(image="test.png", task="table"))


async def test_vlm_deadline_delivers_ocr_before_outer_tool_timeout(service, monkeypatch):
    monkeypatch.setattr(service_module, "_VLM_TIMEOUT_S", 0.01)
    async def hang(*args, **kwargs):
        await asyncio.Event().wait()
    service.vlm_provider.analyze.side_effect = hang
    result = await service.analyze(ImageAnalyzeInput(image="test.png", task="table"))
    assert result.degraded and result.ocr.text == "金额 100"
    assert "provider_timeout" in result.warnings[0]


def test_default_ocr_engine_initializes_once_across_instances(monkeypatch):
    factory = Mock(return_value=lambda image: ([], None))
    monkeypatch.setattr(ocr_module, "_shared_engine", None)
    monkeypatch.setattr(ocr_module, "_create_engine", factory)
    providers = [ocr_module.RapidOCRProvider() for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        engines = list(pool.map(lambda p: p._engine_instance(), providers))
    assert factory.call_count == 1
    assert all(engine is engines[0] for engine in engines)
