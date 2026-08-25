"""Provider-neutral protocols for OCR, visual models, and route signals."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from server.tools.vision.contracts import (
    ImageAsset,
    ImageLanguage,
    OCRResult,
    VisionError,
    VisionErrorCode,
    VisionModelResult,
    VisionSignals,
)


@runtime_checkable
class OCRProvider(Protocol):
    id: str

    async def extract(
        self, image: ImageAsset, *, language: ImageLanguage
    ) -> OCRResult: ...


@runtime_checkable
class VLMProvider(Protocol):
    id: str

    async def analyze(
        self,
        image: ImageAsset,
        *,
        task: str,
        question: str | None,
        language: ImageLanguage,
        ocr_context: OCRResult | None,
    ) -> VisionModelResult: ...


@runtime_checkable
class VisionSignalProvider(Protocol):
    id: str

    async def detect(self, image: ImageAsset) -> VisionSignals: ...


class UnavailableOCRProvider:
    id = "unavailable-ocr"

    async def extract(
        self, image: ImageAsset, *, language: ImageLanguage
    ) -> OCRResult:
        del image, language
        raise VisionError(
            VisionErrorCode.PROVIDER_UNAVAILABLE,
            "OCR provider 未配置或当前不可用",
        )


class UnavailableVLMProvider:
    id = "unavailable-vlm"

    async def analyze(
        self,
        image: ImageAsset,
        *,
        task: str,
        question: str | None,
        language: ImageLanguage,
        ocr_context: OCRResult | None,
    ) -> VisionModelResult:
        del image, task, question, language, ocr_context
        raise VisionError(
            VisionErrorCode.PROVIDER_UNAVAILABLE,
            "视觉模型 provider 未配置或当前不可用",
        )


class NullVisionSignalProvider:
    """Phase-one fallback: return explicit unknown signals without inference."""

    id = "no-signal-provider"

    async def detect(self, image: ImageAsset) -> VisionSignals:
        del image
        return VisionSignals()
