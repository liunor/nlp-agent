"""RapidOCR adapter: local ONNX OCR behind the vision provider protocol.

The bundled PP-OCRv4 ONNX models cover Chinese and English mixed text, so the
``language`` parameter is recorded on results for traceability but does not
switch model weights.  Inference is CPU-bound and runs off the event loop via
``asyncio.to_thread``.

``retry_low_confidence_once`` is implemented as a single 2x upscale retry:
small text is the dominant cause of low-confidence OCR, and line-level angle
classification is already enabled in the engine, making rotation retries
redundant.  Bounding boxes are always mapped back to the original (EXIF-rotated)
image coordinate space regardless of internal downscale/upscale.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from statistics import fmean
from typing import Any

import cv2
import numpy as np

from server.tools.vision.contracts import (
    BoundingBox,
    ImageAsset,
    ImageLanguage,
    OCRBlock,
    OCRResult,
    VisionError,
    VisionErrorCode,
)
from server.tools.vision.imaging import decode_bgr, downscale_to

_UPSCALE_FACTOR = 2


def _default_engine_factory() -> Any:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as error:
        raise VisionError(
            VisionErrorCode.PROVIDER_UNAVAILABLE,
            "OCR 依赖 rapidocr-onnxruntime 未安装",
        ) from error
    try:
        return RapidOCR()
    except Exception as error:
        raise VisionError(
            VisionErrorCode.PROVIDER_UNAVAILABLE,
            "OCR 引擎初始化失败",
        ) from error


class RapidOCRProvider:
    """Lazy RapidOCR engine behind the ``OCRProvider`` protocol."""

    id = "rapidocr"

    def __init__(
        self,
        *,
        confidence_threshold: float = 0.75,
        auto_rotate: bool = True,
        max_dimension: int = 4096,
        retry_low_confidence_once: bool = True,
        engine_factory: Callable[[], Any] | None = None,
    ) -> None:
        if not 0 <= confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if max_dimension <= 0:
            raise ValueError("max_dimension must be positive")
        self.confidence_threshold = confidence_threshold
        self.auto_rotate = auto_rotate
        self.max_dimension = max_dimension
        self.retry_low_confidence_once = retry_low_confidence_once
        self._engine_factory = engine_factory or _default_engine_factory
        self._engine: Any = None
        self._engine_lock = threading.Lock()

    async def extract(self, image: ImageAsset, *, language: ImageLanguage) -> OCRResult:
        try:
            return await asyncio.to_thread(self._extract_sync, image, language)
        except VisionError:
            raise
        except Exception as error:
            raise VisionError(
                VisionErrorCode.PROVIDER_UNAVAILABLE,
                "OCR 引擎执行失败",
            ) from error

    def _engine_instance(self) -> Any:
        with self._engine_lock:
            if self._engine is None:
                self._engine = self._engine_factory()
            return self._engine

    def _extract_sync(self, image: ImageAsset, language: ImageLanguage) -> OCRResult:
        engine = self._engine_instance()
        array, ratio = self._decode(image)
        entries = self._run(engine, array)
        if self._should_retry(entries, array):
            upscaled = cv2.resize(
                array, None,
                fx=_UPSCALE_FACTOR, fy=_UPSCALE_FACTOR,
                interpolation=cv2.INTER_CUBIC,
            )
            retry_entries = self._run(engine, upscaled)
            if self._mean_confidence(retry_entries) > self._mean_confidence(entries):
                entries, ratio = retry_entries, ratio * _UPSCALE_FACTOR
        return self._to_result(entries, ratio, language)

    def _decode(self, image: ImageAsset) -> tuple[np.ndarray, float]:
        array = decode_bgr(image.data, auto_rotate=self.auto_rotate)
        return downscale_to(array, self.max_dimension)

    def _should_retry(self, entries: list, array: np.ndarray) -> bool:
        if not self.retry_low_confidence_once:
            return False
        height, width = array.shape[:2]
        if max(height, width) * _UPSCALE_FACTOR > self.max_dimension:
            return False
        return self._mean_confidence(entries) < self.confidence_threshold

    @staticmethod
    def _run(engine: Any, array: np.ndarray) -> list:
        output = engine(array)
        entries = output[0] if isinstance(output, tuple) else output
        return list(entries) if entries else []

    @staticmethod
    def _mean_confidence(entries: list) -> float:
        if not entries:
            return 0.0
        return fmean(float(entry[2]) for entry in entries)

    @staticmethod
    def _to_result(
        entries: list, ratio: float, language: ImageLanguage
    ) -> OCRResult:
        blocks: list[OCRBlock] = []
        for index, (points, text, score) in enumerate(entries):
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            block = OCRBlock(
                block_id=f"block-{index + 1}",
                text=str(text),
                bbox=BoundingBox(
                    x=max(0, round(min(xs) / ratio)),
                    y=max(0, round(min(ys) / ratio)),
                    width=max(1, round((max(xs) - min(xs)) / ratio)),
                    height=max(1, round((max(ys) - min(ys)) / ratio)),
                ),
                confidence=max(0.0, min(1.0, float(score))),
                language=language,
            )
            blocks.append(block)
        return OCRResult(
            text="\n".join(block.text for block in blocks if block.text),
            blocks=blocks,
            language=language,
            confidence=fmean(b.confidence for b in blocks) if blocks else None,
        )
