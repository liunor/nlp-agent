"""Deterministic and explainable routing for image-analysis tasks."""

from __future__ import annotations

from server.tools.vision.contracts import (
    ImageTask,
    RouteDecision,
    VisionSignals,
)


class VisionTaskRouter:
    def __init__(
        self,
        *,
        text_coverage_threshold: float = 0.15,
        aligned_text_threshold: float = 0.65,
    ) -> None:
        if not 0 <= text_coverage_threshold <= 1:
            raise ValueError("text_coverage_threshold must be between 0 and 1")
        if not 0 <= aligned_text_threshold <= 1:
            raise ValueError("aligned_text_threshold must be between 0 and 1")
        self.text_coverage_threshold = text_coverage_threshold
        self.aligned_text_threshold = aligned_text_threshold

    def route(
        self, task: ImageTask, signals: VisionSignals | None = None
    ) -> RouteDecision:
        if task == "ocr":
            return RouteDecision(
                task_executed="ocr", route="ocr", reason="explicit task=ocr"
            )
        if task in {"describe", "question"}:
            return RouteDecision(
                task_executed=task,
                route="vlm",
                reason=f"explicit task={task}",
            )
        if task in {"table", "chart", "formula"}:
            return RouteDecision(
                task_executed=task,
                route="fusion",
                reason=f"explicit task={task}",
            )

        observed = signals or VisionSignals()
        if (
            observed.image_category == "chart"
            or observed.has_axes
            or observed.has_legend
            or observed.has_data_labels
        ):
            return RouteDecision(
                task_executed="chart",
                route="fusion",
                reason="auto detected chart signals",
                signals=observed,
            )
        if observed.image_category == "formula":
            return RouteDecision(
                task_executed="formula",
                route="fusion",
                reason="auto detected formula category",
                signals=observed,
            )
        if (
            observed.has_grid_lines
            or observed.aligned_text_ratio >= self.aligned_text_threshold
        ):
            return RouteDecision(
                task_executed="table",
                route="fusion",
                reason="auto detected table layout signals",
                signals=observed,
            )
        if (
            observed.image_category == "document"
            or observed.text_coverage >= self.text_coverage_threshold
        ):
            return RouteDecision(
                task_executed="ocr",
                route="ocr",
                reason="auto detected document or dense text",
                signals=observed,
            )
        return RouteDecision(
            task_executed="describe",
            route="vlm",
            reason="auto defaulted to semantic description",
            signals=observed,
        )
