"""Self-hosted image CAPTCHA generation and verification.

Generates distorted text images to prevent automated registration / SMS abuse.
Each captcha is identified by a UUID and expires after a short TTL.
"""

from __future__ import annotations

import base64
import io
import random
import secrets
import string
import threading
import time
import uuid

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
#  In-memory captcha store
# ---------------------------------------------------------------------------

_CAPTCHA_TTL_S = 120  # 2 minutes
_store: dict[str, tuple[str, float]] = {}  # captcha_id -> (code, expires_at)
_lock = threading.Lock()


def _cleanup_expired() -> None:
    now = time.monotonic()
    expired = [cid for cid, (_, exp) in _store.items() if exp <= now]
    for cid in expired:
        _store.pop(cid, None)


# ---------------------------------------------------------------------------
#  Image generation
# ---------------------------------------------------------------------------

_CHARS = string.ascii_uppercase + string.digits
# Remove ambiguous characters
_CHARS = "".join(c for c in _CHARS if c not in {"O", "0", "I", "1", "L"})

_IMG_WIDTH = 160
_IMG_HEIGHT = 60
_FONT_SIZE = 36


def _random_color(low: int = 30, high: int = 150) -> tuple[int, int, int]:
    return (random.randint(low, high), random.randint(low, high), random.randint(low, high))


def generate_captcha_image() -> tuple[str, str]:
    """Generate a CAPTCHA image and return ``(captcha_id, base64_png)``.

    The correct code is stored in memory and will be checked during
    verification.
    """
    _cleanup_expired()

    code = "".join(random.choices(_CHARS, k=4))
    captcha_id = uuid.uuid4().hex

    with _lock:
        _store[captcha_id] = (code, time.monotonic() + _CAPTCHA_TTL_S)

    # -- Render image -------------------------------------------------------
    img = Image.new("RGB", (_IMG_WIDTH, _IMG_HEIGHT), color=(245, 245, 245))
    draw = ImageDraw.Draw(img)

    # Try to use a TrueType font for better readability; fall back to default.
    try:
        font = ImageFont.truetype("arial.ttf", _FONT_SIZE)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", _FONT_SIZE)
        except (OSError, IOError):
            font = ImageFont.load_default()

    # Draw each character with random rotation and position
    x_offset = 15
    for ch in code:
        y_offset = random.randint(5, 15)
        color = _random_color(20, 120)
        # Draw with slight rotation effect via individual text placement
        draw.text((x_offset, y_offset), ch, fill=color, font=font)
        x_offset += random.randint(28, 38)

    # Add noise lines
    for _ in range(6):
        xy = [
            (random.randint(0, _IMG_WIDTH), random.randint(0, _IMG_HEIGHT)),
            (random.randint(0, _IMG_WIDTH), random.randint(0, _IMG_HEIGHT)),
        ]
        draw.line(xy, fill=_random_color(100, 200), width=random.randint(1, 2))

    # Add noise dots
    for _ in range(80):
        x = random.randint(0, _IMG_WIDTH - 1)
        y = random.randint(0, _IMG_HEIGHT - 1)
        draw.point((x, y), fill=_random_color(80, 180))

    # Encode to base64 PNG
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return captcha_id, f"data:image/png;base64,{b64}"


def verify_captcha(captcha_id: str, code: str) -> bool:
    """Check *code* against the stored answer for *captcha_id*.

    The captcha is consumed (deleted) regardless of outcome to prevent replay.
    Returns ``True`` when the code matches (case-insensitive).
    """
    with _lock:
        entry = _store.pop(captcha_id, None)

    if entry is None:
        return False
    stored_code, expires_at = entry
    if time.monotonic() > expires_at:
        return False
    return secrets.compare_digest(stored_code.upper(), code.strip().upper())
