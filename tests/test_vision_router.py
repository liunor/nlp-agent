"""Explicit and explainable auto-routing tests."""

from __future__ import annotations

import pytest

from server.tools.vision.contracts import VisionSignals
from server.tools.vision.router import VisionTaskRouter


@pytest.mark.parametrize(
    ("task", "executed", "route"),
    [
        ("ocr", "ocr", "ocr"),
        ("describe", "describe", "vlm"),
        ("question", "question", "vlm"),
        ("table", "table", "fusion"),
        ("chart", "chart", "fusion"),
        ("formula", "formula", "fusion"),
    ],
)
def test_explicit_tasks_have_deterministic_routes(task, executed, route) -> None:
    decision = VisionTaskRouter().route(task)
    assert decision.task_executed == executed
    assert decision.route == route
    assert decision.reason == f"explicit task={task}"


@pytest.mark.parametrize(
    "signals",
    [
        VisionSignals(image_category="chart"),
        VisionSignals(has_axes=True),
        VisionSignals(has_legend=True),
        VisionSignals(has_data_labels=True),
    ],
)
def test_auto_routes_chart_signals_to_fusion(signals: VisionSignals) -> None:
    decision = VisionTaskRouter().route("auto", signals)
    assert (decision.task_executed, decision.route) == ("chart", "fusion")
    assert decision.signals == signals


def test_auto_routes_formula_category_to_fusion() -> None:
    decision = VisionTaskRouter().route(
        "auto", VisionSignals(image_category="formula")
    )
    assert (decision.task_executed, decision.route) == ("formula", "fusion")


@pytest.mark.parametrize(
    "signals",
    [
        VisionSignals(has_grid_lines=True),
        VisionSignals(aligned_text_ratio=0.65),
    ],
)
def test_auto_routes_table_layout_to_fusion(signals: VisionSignals) -> None:
    decision = VisionTaskRouter().route("auto", signals)
    assert (decision.task_executed, decision.route) == ("table", "fusion")


@pytest.mark.parametrize(
    "signals",
    [
        VisionSignals(image_category="document"),
        VisionSignals(text_coverage=0.15),
    ],
)
def test_auto_routes_dense_text_to_ocr(signals: VisionSignals) -> None:
    decision = VisionTaskRouter().route("auto", signals)
    assert (decision.task_executed, decision.route) == ("ocr", "ocr")


@pytest.mark.parametrize(
    "category", ["photo", "ui", "unknown"]
)
def test_auto_defaults_semantic_images_to_description(category: str) -> None:
    decision = VisionTaskRouter().route(
        "auto", VisionSignals(image_category=category)
    )
    assert (decision.task_executed, decision.route) == ("describe", "vlm")


def test_chart_signals_take_precedence_over_dense_text() -> None:
    decision = VisionTaskRouter().route(
        "auto", VisionSignals(has_axes=True, text_coverage=0.8)
    )
    assert (decision.task_executed, decision.route) == ("chart", "fusion")
