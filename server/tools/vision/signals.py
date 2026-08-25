"""OpenCV-based vision signals for explainable ``auto`` routing.

All signals are derived from deterministic image processing — no learned
models — so every routing decision can be explained by the emitted
``VisionSignals`` payload:

- ``has_grid_lines``: at least ``min_grid_lines`` long horizontal *and*
  vertical lines with a real lattice of intersections, i.e. a bordered table.
- ``has_axes``: one dominant vertical line in the left region plus one
  dominant horizontal line in the bottom region, without a full grid.
  Bordered tables are excluded by the grid check; charts with heavy internal
  grids may legitimately route as tables instead.
- ``text_coverage``: union of dilated text-like connected components over the
  image area.  Dense documents score high; photos with a street sign stay low.
- ``aligned_text_ratio``: fraction of text lines whose left edges fall into
  at least two separated column clusters (borderless tables, column layouts);
  ordinary paragraphs form a single cluster and score zero.
- ``has_data_labels``: many small text components inside the axes region.
- ``image_category=formula``: sparse text with a short internal fraction bar,
  excluding grids and axes.  This is intentionally conservative.
- ``has_legend`` is not inferred from CV and stays ``False``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import cv2
import numpy as np

from server.tools.vision.contracts import ImageAsset, VisionSignals
from server.tools.vision.imaging import decode_bgr, downscale_to


@dataclass(frozen=True, slots=True)
class _Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def center_x(self) -> float:
        return self.x + self.w / 2

    @property
    def center_y(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> int:
        return self.w * self.h


def _long_lines(
    mask: np.ndarray, *, horizontal: bool, min_span: int, max_thickness: int,
) -> list[_Box]:
    """Detect long straight lines via projection of the opened mask.

    Projection (rather than contour bounding boxes) keeps lines detectable
    even where filled shapes — chart bars, table cells — touch them.
    """

    counts = (
        mask.sum(axis=1) if horizontal else mask.sum(axis=0)
    ) // 255
    indices = np.flatnonzero(counts >= min_span)
    lines: list[_Box] = []
    if indices.size == 0:
        return lines
    group_start = prev = int(indices[0])
    for raw in indices[1:]:
        index = int(raw)
        if index - prev <= 2:  # tolerate anti-aliasing gaps
            prev = index
            continue
        lines.append((group_start, prev))
        group_start = prev = index
    lines.append((group_start, prev))

    boxes: list[_Box] = []
    for start, end in lines:
        thickness = end - start + 1
        if thickness > max_thickness:
            continue
        if horizontal:
            band = mask[start : end + 1, :]
            columns = np.flatnonzero(band.any(axis=0))
            if columns.size == 0:
                continue
            x0, x1 = int(columns[0]), int(columns[-1])
            boxes.append(_Box(x0, start, x1 - x0 + 1, thickness))
        else:
            band = mask[:, start : end + 1]
            rows = np.flatnonzero(band.any(axis=1))
            if rows.size == 0:
                continue
            y0, y1 = int(rows[0]), int(rows[-1])
            boxes.append(_Box(start, y0, thickness, y1 - y0 + 1))
    return boxes


def _text_components(binary: np.ndarray, width: int, height: int) -> list[_Box]:
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    boxes: list[_Box] = []
    max_h = max(6, int(height * 0.15))
    max_w = max(6, int(width * 0.5))
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        if not (3 <= h <= max_h and 2 <= w <= max_w):
            continue
        if area < 6 or w / h > 20:
            continue
        boxes.append(_Box(x, y, w, h))
    return boxes


def _line_boxes(boxes: list[_Box], width: int, height: int) -> list[_Box]:
    """Merge character-level components into text-line boxes."""

    if not boxes:
        return []
    mask = np.zeros((height, width), dtype=np.uint8)
    for box in boxes:
        mask[box.y : box.y + box.h, box.x : box.x + box.w] = 255
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(4, width // 50), 1)
    )
    merged = cv2.dilate(mask, kernel)
    count, _, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    lines: list[_Box] = []
    for index in range(1, count):
        x, y, w, h, _area = (int(v) for v in stats[index])
        if w >= 8 and h >= 4:
            lines.append(_Box(x, y, w, h))
    return lines


def _aligned_text_ratio(lines: list[_Box], width: int) -> float:
    if len(lines) < 4:
        return 0.0
    tolerance = max(3, int(width * 0.01))
    clusters: list[list[int]] = []
    for start in sorted(line.x for line in lines):
        if not clusters:
            clusters.append([start])
            continue
        closest_center = sum(clusters[-1]) / len(clusters[-1])
        if start - closest_center <= tolerance:
            clusters[-1].append(start)
        else:
            clusters.append([start])
    multi = [cluster for cluster in clusters if len(cluster) >= 3]
    if len(multi) < 2:
        return 0.0
    centers = [sum(cluster) / len(cluster) for cluster in multi]
    if max(centers) - min(centers) < width * 0.15:
        return 0.0
    aligned = sum(len(cluster) for cluster in multi)
    return aligned / len(lines)


def _has_grid_intersections(
    horizontals: list[_Box],
    verticals: list[_Box],
    h_mask: np.ndarray,
    v_mask: np.ndarray,
    *,
    min_grid_lines: int,
) -> bool:
    """Reject projected texture lines unless they form an actual lattice."""

    if len(horizontals) < min_grid_lines or len(verticals) < min_grid_lines:
        return False
    height, width = h_mask.shape[:2]
    tolerance = 2
    intersecting_horizontals: set[int] = set()
    intersecting_verticals: set[int] = set()
    intersections = 0
    for horizontal_index, horizontal in enumerate(horizontals):
        y = int(round(horizontal.center_y))
        for vertical_index, vertical in enumerate(verticals):
            x = int(round(vertical.center_x))
            if not (
                horizontal.x - tolerance <= x <= horizontal.x + horizontal.w + tolerance
                and vertical.y - tolerance <= y <= vertical.y + vertical.h + tolerance
            ):
                continue
            x0, x1 = max(0, x - tolerance), min(width, x + tolerance + 1)
            y0, y1 = max(0, y - tolerance), min(height, y + tolerance + 1)
            if not h_mask[y0:y1, x0:x1].any() or not v_mask[y0:y1, x0:x1].any():
                continue
            intersections += 1
            intersecting_horizontals.add(horizontal_index)
            intersecting_verticals.add(vertical_index)
    return (
        len(intersecting_horizontals) >= min_grid_lines
        and len(intersecting_verticals) >= min_grid_lines
        and intersections >= min_grid_lines**2
    )


class OpenCVSignalProvider:
    """Deterministic CV signal extractor behind ``VisionSignalProvider``."""

    id = "opencv-signals"

    def __init__(
        self,
        *,
        work_dimension: int = 1024,
        min_grid_lines: int = 3,
        min_line_span_ratio: float = 0.4,
        min_data_labels: int = 8,
    ) -> None:
        if work_dimension < 64:
            raise ValueError("work_dimension must be at least 64")
        if min_grid_lines < 2:
            raise ValueError("min_grid_lines must be at least 2")
        if not 0 < min_line_span_ratio <= 1:
            raise ValueError("min_line_span_ratio must be within (0, 1]")
        self.work_dimension = work_dimension
        self.min_grid_lines = min_grid_lines
        self.min_line_span_ratio = min_line_span_ratio
        self.min_data_labels = min_data_labels

    async def detect(self, image: ImageAsset) -> VisionSignals:
        return await asyncio.to_thread(self._detect_sync, image)

    def _detect_sync(self, image: ImageAsset) -> VisionSignals:
        array = decode_bgr(image.data, auto_rotate=True)
        array, _ = downscale_to(array, self.work_dimension)
        height, width = array.shape[:2]
        gray = cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)
        # Otsu preserves solid strokes (text, bars) while adaptive threshold
        # keeps thin lines under uneven lighting; union of both is used.
        otsu = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
        )[1]
        adaptive = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, blockSize=25, C=10,
        )
        binary = cv2.bitwise_or(otsu, adaptive)

        min_span_h = int(width * self.min_line_span_ratio)
        min_span_v = int(height * self.min_line_span_ratio)
        max_thickness = max(2, min(width, height) // 100)
        h_mask = cv2.morphologyEx(
            binary, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(8, width // 40), 1)),
        )
        v_mask = cv2.morphologyEx(
            binary, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(8, height // 40))),
        )
        horizontals = _long_lines(
            h_mask, horizontal=True,
            min_span=min_span_h, max_thickness=max_thickness,
        )
        verticals = _long_lines(
            v_mask, horizontal=False,
            min_span=min_span_v, max_thickness=max_thickness,
        )
        formula_horizontals = _long_lines(
            h_mask,
            horizontal=True,
            min_span=max(8, int(width * 0.08)),
            max_thickness=max_thickness,
        )

        has_grid = _has_grid_intersections(
            horizontals,
            verticals,
            h_mask,
            v_mask,
            min_grid_lines=self.min_grid_lines,
        )
        left_axis = any(line.center_x < width * 0.35 for line in verticals)
        bottom_axis = any(line.center_y > height * 0.65 for line in horizontals)
        has_axes = (
            left_axis
            and bottom_axis
            and not has_grid
            and (len(verticals) < self.min_grid_lines
                 or len(horizontals) < self.min_grid_lines)
        )

        text_mask = binary.copy()
        text_mask[h_mask > 0] = 0
        text_mask[v_mask > 0] = 0
        components = _text_components(text_mask, width, height)

        coverage = 0.0
        if components:
            region = np.zeros((height, width), dtype=np.uint8)
            for box in components:
                region[box.y : box.y + box.h, box.x : box.x + box.w] = 255
            merge = cv2.getStructuringElement(
                cv2.MORPH_RECT, (max(3, width // 40), max(3, height // 80))
            )
            region = cv2.dilate(region, merge)
            coverage = float(cv2.countNonZero(region)) / float(width * height)

        lines = _line_boxes(components, width, height)
        aligned_ratio = _aligned_text_ratio(lines, width)
        formula_bars = [
            line
            for line in formula_horizontals
            if width * 0.08 <= line.w <= width * 0.65
            and height * 0.1 <= line.center_y <= height * 0.9
        ]
        formula_like = (
            not has_grid
            and not has_axes
            and 0 < coverage < 0.15
            and len(components) >= 3
            and len(lines) <= 4
            and bool(formula_bars)
        )

        data_labels = 0
        if has_axes:
            axis_x = min(
                (line.center_x for line in verticals if line.center_x < width * 0.35),
                default=0,
            )
            axis_y = max(
                (line.center_y for line in horizontals if line.center_y > height * 0.65),
                default=height,
            )
            data_labels = sum(
                1
                for box in components
                if box.center_x > axis_x and box.center_y < axis_y
            )

        laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        quality = min(1.0, laplacian_var / 800.0)

        if has_axes:
            category = "chart"
        elif formula_like:
            category = "formula"
        elif coverage >= 0.15:
            category = "document"
        elif coverage <= 0.02:
            category = "photo"
        else:
            category = "unknown"

        return VisionSignals(
            text_coverage=round(coverage, 4),
            aligned_text_ratio=round(aligned_ratio, 4),
            has_grid_lines=has_grid,
            has_axes=has_axes,
            has_legend=False,
            has_data_labels=data_labels >= self.min_data_labels,
            image_category=category,
            quality_score=round(quality, 4),
        )
