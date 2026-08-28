"""Self-hosted image CAPTCHA generation.

Generates distorted text images to prevent automated registration / SMS abuse.
Each captcha is identified by a UUID; the answer is stored in the shared
``nlp_auth_codes`` table (see :mod:`server.auth.code_store`) so that
verification works across multiple server instances.
"""

from __future__ import annotations

import base64
import io
import random
import string
import uuid

from PIL import Image, ImageDraw, ImageFont

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


def generate_captcha_image() -> tuple[str, str, str]:
    """Generate a CAPTCHA and return ``(captcha_id, base64_png, code)``.

    The caller is responsible for persisting *code* through
    :func:`server.auth.code_store.put_code`; verification happens via
    :func:`server.auth.code_store.consume_code`.  Nothing is kept in
    process memory.
    """
    code = "".join(random.choices(_CHARS, k=4))
    captcha_id = uuid.uuid4().hex

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

    return captcha_id, f"data:image/png;base64,{b64}", code
