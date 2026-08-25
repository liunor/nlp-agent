import hashlib
import io
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from server.tools.vision.contracts import ImageAsset, ImageReference
from server.tools.vision.router import VisionTaskRouter
from server.tools.vision.signals import (
    OpenCVSignalProvider,
    _Box,
    _aligned_text_ratio,
)


def _create_asset(img_bytes: bytes, width: int, height: int) -> ImageAsset:
    sha256 = hashlib.sha256(img_bytes).hexdigest()
    ref = ImageReference(
        file_name="test.png",
        media_type="image/png",
        size_bytes=len(img_bytes),
        width=width,
        height=height,
        frames=1,
        sha256=sha256,
    )
    return ImageAsset(path=Path("/test.png"), data=img_bytes, reference=ref)


@pytest.fixture
def provider():
    return OpenCVSignalProvider()


@pytest.fixture
def router():
    return VisionTaskRouter()


@pytest.mark.asyncio
async def test_grid_image_detects_table_signals(provider, router):
    # Create a 600x400 white image
    img = np.ones((400, 600, 3), dtype=np.uint8) * 255
    # Draw a 5x5 grid
    # Horizontal lines
    for y in range(0, 401, 80):
        cv2.line(img, (0, y), (600, y), (0, 0, 0), 2)
    # Vertical lines
    for x in range(0, 601, 120):
        cv2.line(img, (x, 0), (x, 400), (0, 0, 0), 2)

    is_success, buffer = cv2.imencode(".png", img)
    assert is_success
    asset = _create_asset(buffer.tobytes(), 600, 400)

    signals = await provider.detect(asset)
    assert signals.has_grid_lines is True

    decision = router.route("auto", signals)
    assert decision.task_executed == "table"


@pytest.mark.asyncio
async def test_disconnected_projected_lines_do_not_form_a_table_grid(provider, router):
    img = np.ones((400, 600, 3), dtype=np.uint8) * 255
    for y in (80, 160, 240):
        cv2.line(img, (0, y), (150, y), (0, 0, 0), 2)
        cv2.line(img, (450, y), (599, y), (0, 0, 0), 2)
    for x in (250, 300, 350):
        cv2.line(img, (x, 0), (x, 60), (0, 0, 0), 2)
        cv2.line(img, (x, 280), (x, 399), (0, 0, 0), 2)

    is_success, buffer = cv2.imencode(".png", img)
    assert is_success
    signals = await provider.detect(_create_asset(buffer.tobytes(), 600, 400))

    assert signals.has_grid_lines is False
    assert router.route("auto", signals).task_executed != "table"


def test_stair_step_components_do_not_chain_into_text_columns():
    starts = [0, 5, 10, 15, 300, 305, 310, 315]
    lines = [
        _Box(x=start, y=index * 12, w=20, h=8) for index, start in enumerate(starts)
    ]

    assert _aligned_text_ratio(lines, width=600) < 0.65


@pytest.mark.asyncio
async def test_borderless_text_columns_still_route_to_table(provider, router):
    img = np.ones((420, 600, 3), dtype=np.uint8) * 255
    for row in range(7):
        y = 45 + row * 52
        for column, x in enumerate((30, 230, 430)):
            cv2.putText(
                img,
                f"C{column}R{row}",
                (x, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 0, 0),
                2,
            )

    is_success, buffer = cv2.imencode(".png", img)
    assert is_success
    signals = await provider.detect(_create_asset(buffer.tobytes(), 600, 420))

    assert signals.aligned_text_ratio >= 0.65
    assert router.route("auto", signals).task_executed == "table"


@pytest.mark.asyncio
async def test_axes_image_detects_chart_signals(provider, router):
    # Create a 600x400 white image
    img = np.ones((400, 600, 3), dtype=np.uint8) * 255

    # Draw L-shape axes
    # Vertical line (y-axis)
    cv2.line(img, (50, 50), (50, 350), (0, 0, 0), 2)
    # Horizontal line (x-axis)
    cv2.line(img, (50, 350), (550, 350), (0, 0, 0), 2)

    # Draw some dots in the plot area
    for i in range(5):
        cv2.circle(img, (100 + i * 80, 300 - i * 40), 5, (255, 0, 0), -1)

    is_success, buffer = cv2.imencode(".png", img)
    assert is_success
    asset = _create_asset(buffer.tobytes(), 600, 400)

    signals = await provider.detect(asset)
    assert signals.has_axes is True

    decision = router.route("auto", signals)
    assert decision.task_executed == "chart"


@pytest.mark.asyncio
async def test_dense_text_image_detects_document(provider, router):
    img = Image.new("RGB", (600, 400), color="white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    for i in range(20):
        draw.text((10, 10 + i * 15), "This is a line of dense text. " * 3, fill="black", font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    asset = _create_asset(buf.getvalue(), 600, 400)

    signals = await provider.detect(asset)
    assert signals.text_coverage >= 0.15

    decision = router.route("auto", signals)
    assert decision.task_executed == "ocr"


@pytest.mark.asyncio
async def test_blank_photo_defaults_to_describe(provider, router):
    img = np.ones((400, 600, 3), dtype=np.uint8) * 128  # Solid gray
    is_success, buffer = cv2.imencode(".png", img)
    assert is_success
    asset = _create_asset(buffer.tobytes(), 600, 400)

    signals = await provider.detect(asset)
    assert signals.text_coverage < 0.15

    decision = router.route("auto", signals)
    assert decision.task_executed == "describe"


@pytest.mark.asyncio
async def test_sparse_fraction_layout_routes_to_formula(provider, router):
    img = np.ones((400, 600, 3), dtype=np.uint8) * 255
    cv2.putText(
        img, "a + b", (190, 155), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2
    )
    cv2.line(img, (175, 200), (390, 200), (0, 0, 0), 2)
    cv2.putText(
        img, "c - d", (190, 260), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2
    )
    is_success, buffer = cv2.imencode(".png", img)
    assert is_success
    asset = _create_asset(buffer.tobytes(), 600, 400)

    signals = await provider.detect(asset)
    decision = router.route("auto", signals)

    assert signals.image_category == "formula"
    assert decision.task_executed == "formula"
    assert decision.route == "fusion"


@pytest.mark.asyncio
async def test_sparse_plain_text_does_not_route_to_formula(provider, router):
    img = np.ones((400, 600, 3), dtype=np.uint8) * 255
    cv2.putText(
        img, "plain text", (160, 210), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2
    )
    is_success, buffer = cv2.imencode(".png", img)
    assert is_success
    asset = _create_asset(buffer.tobytes(), 600, 400)

    signals = await provider.detect(asset)
    decision = router.route("auto", signals)

    assert signals.image_category != "formula"
    assert decision.task_executed != "formula"


@pytest.mark.asyncio
async def test_quality_score_is_between_0_and_1(provider):
    img = np.ones((100, 100, 3), dtype=np.uint8) * 255
    is_success, buffer = cv2.imencode(".png", img)
    assert is_success
    asset = _create_asset(buffer.tobytes(), 100, 100)

    signals = await provider.detect(asset)
    if signals.quality_score is not None:
        assert 0 <= signals.quality_score <= 1


def test_provider_id(provider):
    assert provider.id == "opencv-signals"
