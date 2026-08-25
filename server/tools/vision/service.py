"""Image-analysis orchestration: resolve, route, invoke providers, assemble."""

from __future__ import annotations

import asyncio
from statistics import fmean
from typing import Any

from pydantic import ValidationError

from core.session_context import SessionContext
from server.tools.vision.contracts import (
    UNTRUSTED_IMAGE_BANNER,
    ConfidenceReport,
    ImageAnalyzeInput,
    ImageAnalyzeResponse,
    ImageCitation,
    OCRResult,
    VisionError,
    VisionErrorCode,
    VisionModelResult,
    VisionSignals,
)
from server.tools.vision.input_resolver import ImageInputResolver, session_uploads_root
from server.tools.vision.providers import (
    NullVisionSignalProvider,
    OCRProvider,
    UnavailableOCRProvider,
    UnavailableVLMProvider,
    VLMProvider,
    VisionSignalProvider,
)
from server.tools.vision.router import VisionTaskRouter
from server.tools.vision.safety import ImageSafetyLimits


_TRUNCATION_MARKER = "\n\n[输出已截断]"


def _bounded(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    available = max(0, limit - len(_TRUNCATION_MARKER))
    return f"{text[:available]}{_TRUNCATION_MARKER}", True


def _with_safety_banner(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith(UNTRUSTED_IMAGE_BANNER):
        return cleaned
    return f"{UNTRUSTED_IMAGE_BANNER}\n\n{cleaned}"


def _ocr_confidence(result: OCRResult | None) -> float | None:
    if result is None:
        return None
    if result.confidence is not None:
        return result.confidence
    values = [block.confidence for block in result.blocks]
    values.extend(cell.confidence for cell in result.table_cells)
    return fmean(values) if values else None


def _confidence_level(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value >= 0.85:
        return "high"
    if value >= 0.60:
        return "medium"
    return "low"


class ImageAnalyzeService:
    def __init__(
        self,
        *,
        resolver: ImageInputResolver,
        router: VisionTaskRouter | None = None,
        ocr_provider: OCRProvider | None = None,
        vlm_provider: VLMProvider | None = None,
        signal_provider: VisionSignalProvider | None = None,
        result_max_chars: int = 20_000,
    ) -> None:
        self.resolver = resolver
        self.router = router or VisionTaskRouter()
        self.ocr_provider = ocr_provider or UnavailableOCRProvider()
        self.vlm_provider = vlm_provider or UnavailableVLMProvider()
        self.signal_provider = signal_provider or NullVisionSignalProvider()
        self.result_max_chars = max(500, result_max_chars)

    async def analyze(self, request: ImageAnalyzeInput) -> ImageAnalyzeResponse:
        asset = await asyncio.to_thread(self.resolver.resolve, request.image)
        signals = await self._detect_signals(asset) if request.task == "auto" else None
        decision = self.router.route(request.task, signals)

        ocr: OCRResult | None = None
        model_result: VisionModelResult | None = None
        if decision.route in {"ocr", "fusion"}:
            ocr = await self._extract_ocr(asset, request.language)
        if decision.route in {"vlm", "fusion"}:
            model_result = await self._analyze_vlm(
                asset,
                task=decision.task_executed,
                question=request.question,
                language=request.language,
                ocr_context=ocr,
            )

        raw_summary, raw_markdown = self._content_for(ocr, model_result)
        max_chars = min(request.max_chars, self.result_max_chars)
        summary, summary_truncated = _bounded(
            _with_safety_banner(raw_summary), max_chars
        )
        markdown: str | None = None
        markdown_truncated = False
        if request.output_format == "markdown":
            markdown, markdown_truncated = _bounded(
                _with_safety_banner(raw_markdown), max_chars
            )

        warnings = list(model_result.warnings if model_result else [])
        truncated = summary_truncated or markdown_truncated
        if truncated:
            warnings.append(f"文本输出超过 {max_chars} 字符，已截断")

        citations = [
            citation.model_copy(
                update={"file_name": asset.reference.file_name}
            )
            for citation in (model_result.citations if model_result else [])
        ]
        if not citations:
            citations.append(ImageCitation(file_name=asset.reference.file_name))

        ocr_confidence = _ocr_confidence(ocr)
        semantic_confidence = model_result.confidence if model_result else None
        overall = ocr_confidence if ocr_confidence is not None else semantic_confidence
        reasons: list[str] = []
        if ocr_confidence is not None:
            reasons.append("OCR 置信度独立保留")
        if semantic_confidence is not None:
            reasons.append("语义置信度来自视觉模型 provider")

        return ImageAnalyzeResponse(
            input=asset.reference,
            task_requested=request.task,
            task_executed=decision.task_executed,
            route=decision.route,
            summary=summary,
            markdown=markdown,
            ocr=ocr,
            table=model_result.table if model_result else None,
            chart=model_result.chart if model_result else None,
            formulae=list(model_result.formulae if model_result else []),
            citations=citations,
            confidence=ConfidenceReport(
                overall=overall,
                ocr=ocr_confidence,
                semantic=semantic_confidence,
                level=_confidence_level(overall),
                reasons=reasons,
            ),
            warnings=warnings,
            truncated=truncated,
            untrusted=True,
        )

    async def _detect_signals(self, asset: Any) -> VisionSignals:
        value = await self.signal_provider.detect(asset)
        try:
            return VisionSignals.model_validate(value)
        except ValidationError as error:
            raise VisionError(
                VisionErrorCode.INVALID_PROVIDER_RESPONSE,
                "视觉信号 provider 返回了无效结构",
            ) from error

    async def _extract_ocr(self, asset: Any, language: Any) -> OCRResult:
        value = await self.ocr_provider.extract(asset, language=language)
        try:
            return OCRResult.model_validate(value)
        except ValidationError as error:
            raise VisionError(
                VisionErrorCode.INVALID_PROVIDER_RESPONSE,
                "OCR provider 返回了无效结构",
            ) from error

    async def _analyze_vlm(
        self,
        asset: Any,
        *,
        task: str,
        question: str | None,
        language: Any,
        ocr_context: OCRResult | None,
    ) -> VisionModelResult:
        value = await self.vlm_provider.analyze(
            asset,
            task=task,
            question=question,
            language=language,
            ocr_context=ocr_context,
        )
        try:
            return VisionModelResult.model_validate(value)
        except ValidationError as error:
            raise VisionError(
                VisionErrorCode.INVALID_PROVIDER_RESPONSE,
                "视觉模型 provider 返回了无效结构",
            ) from error

    @staticmethod
    def _content_for(
        ocr: OCRResult | None,
        model_result: VisionModelResult | None,
    ) -> tuple[str, str]:
        if model_result is not None:
            return (
                model_result.summary or "图片分析完成，但没有可显示的摘要。",
                model_result.markdown or model_result.summary,
            )
        if ocr is not None:
            text = ocr.text.strip()
            return (text or "未识别到文字。", text or "未识别到文字。")
        return ("图片分析未产生结果。", "图片分析未产生结果。")


def build_image_analyze_service(*, context: SessionContext) -> ImageAnalyzeService:
    from server.tools.vision.config import get_vision_config
    from server.tools.vision.ocr import RapidOCRProvider
    from server.tools.vision.signals import OpenCVSignalProvider
    from server.tools.vision.vlm import ModelRuntimeVLMProvider

    config = get_vision_config()
    if not config.enabled:
        raise VisionError(VisionErrorCode.DISABLED, "图片理解工具已在配置中禁用")
    limits = ImageSafetyLimits(
        max_file_bytes=config.max_file_bytes,
        max_pixels=config.max_pixels,
        allowed_media_types=frozenset(config.allowed_media_types),
    )
    ocr_provider: OCRProvider | None = None
    if config.ocr.provider == "rapidocr":
        ocr_provider = RapidOCRProvider(
            confidence_threshold=config.ocr.confidence_threshold,
            auto_rotate=config.preprocessing.auto_rotate,
            max_dimension=config.preprocessing.max_dimension,
            retry_low_confidence_once=config.preprocessing.retry_low_confidence_once,
        )
    uploads_root = session_uploads_root(context)
    return ImageAnalyzeService(
        resolver=ImageInputResolver(uploads_root=uploads_root, limits=limits),
        ocr_provider=ocr_provider,
        vlm_provider=ModelRuntimeVLMProvider(
            model_route=config.vlm.model_route,
            max_image_bytes=config.vlm.max_image_bytes,
            send_ocr_context=config.vlm.send_ocr_context,
        ),
        signal_provider=OpenCVSignalProvider(),
        result_max_chars=config.result.max_chars,
    )
