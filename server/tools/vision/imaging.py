"""Shared image decoding helpers for vision providers."""

from __future__ import annotations

from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps


def decode_bgr(data: bytes, *, auto_rotate: bool = True) -> np.ndarray:
    """Decode image bytes to a BGR array, honoring EXIF orientation."""

    with Image.open(BytesIO(data)) as pil_image:
        if auto_rotate:
            pil_image = ImageOps.exif_transpose(pil_image)
        rgb = np.asarray(pil_image.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def downscale_to(array: np.ndarray, max_dimension: int) -> tuple[np.ndarray, float]:
    """Downscale so the longest edge fits ``max_dimension``.

    Returns the (possibly unchanged) array and the ratio of array pixels per
    original pixel (``1.0`` when untouched), so callers can map coordinates
    back to the original space.
    """

    height, width = array.shape[:2]
    largest = max(height, width)
    if largest <= max_dimension:
        return array, 1.0
    ratio = max_dimension / largest
    resized = cv2.resize(
        array, None, fx=ratio, fy=ratio, interpolation=cv2.INTER_AREA
    )
    return resized, ratio
