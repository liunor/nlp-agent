import os

from PIL import ImageDraw, ImageFont

from server.auth import captcha
from server.auth.captcha import _FONT_SIZE, _IMG_HEIGHT, _IMG_WIDTH


def test_captcha_uses_a_readable_canvas_and_font_for_the_registration_form():
    assert (_IMG_WIDTH, _IMG_HEIGHT, _FONT_SIZE) == (200, 80, 48)


def test_captcha_fallback_keeps_glyphs_readable_without_system_fonts(monkeypatch):
    original_truetype = ImageFont.truetype
    original_text = ImageDraw.ImageDraw.text
    rendered_heights: list[int] = []

    def reject_system_fonts(font, size, *args, **kwargs):
        if isinstance(font, (str, bytes, os.PathLike)):
            raise OSError("system font unavailable")
        return original_truetype(font, size, *args, **kwargs)

    def record_text(self, xy, text, *args, **kwargs):
        font = kwargs.get("font")
        if font is not None:
            left, top, right, bottom = font.getbbox(text)
            rendered_heights.append(bottom - top)
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageFont, "truetype", reject_system_fonts)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)

    captcha.generate_captcha_image()

    assert len(rendered_heights) == 4
    assert min(rendered_heights) >= 30
