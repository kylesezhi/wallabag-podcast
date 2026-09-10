"""Per-podcast cover art rendering.

Each podcast's name is composited in white Pixel Operator 8 Bold (with a black
outline) onto the pristine ``static/cover.png`` base and served per podcast.
The base asset is never modified; the text is drawn bottom-center inside the
near-black band at the bottom of the artwork.

Pixel Operator is by Jayvee Enaguas (HarvettFox96), CC0 1.0 public domain; the
font and its license are bundled under ``app/assets/fonts/``.
"""

from __future__ import annotations

import io
import logging
from functools import lru_cache
from importlib.resources import files as resources
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent
_BASE_COVER = _BASE_DIR / "static" / "cover.png"

_CANVAS_WIDTH = 1254
_MAX_TEXT_WIDTH = int(_CANVAS_WIDTH * 0.88)  # ~88% of the canvas width
_BOTTOM_MARGIN = 72
_START_SIZE = 72  # multiple of the 8px design grid
_MIN_SIZE = 16
_SIZE_STEP = 8

_FONT_RESOURCE = "assets/fonts/PixelOperator8-Bold.ttf"

# In-memory per-podcast cover cache {guid: PNG bytes}.
_cache: dict[str, bytes] = {}


@lru_cache(maxsize=1)
def _font_bytes() -> bytes:
    """Read the bundled CC0 Pixel Operator 8 Bold TrueType font once."""
    return resources("app").joinpath(_FONT_RESOURCE).read_bytes()


def _stroke(size: int) -> int:
    """Outline thickness for a font size, scaled off the 8px design grid."""
    return max(2, size // 16)


def _pick_font(name: str) -> tuple[ImageFont.FreeTypeFont, int]:
    """Largest 8px-grid font whose text (with stroke) fits the canvas width.

    Starts at 72px and shrinks in 8px steps until the rendered width is at
    most ~88% of the canvas; floors at 16px so even long names stay on one
    line.
    """
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for size in range(_START_SIZE, _MIN_SIZE - 1, -_SIZE_STEP):
        font = ImageFont.truetype(io.BytesIO(_font_bytes()), size)
        stroke = _stroke(size)
        text_w = probe.textbbox((0, 0), name, font=font, stroke_width=stroke)[2]
        if text_w <= _MAX_TEXT_WIDTH:
            return font, stroke
    font = ImageFont.truetype(io.BytesIO(_font_bytes()), _MIN_SIZE)
    return font, _stroke(_MIN_SIZE)


def render_cover_png(name: str) -> bytes:
    """Render ``name`` onto the base cover and return PNG bytes.

    Raises (e.g. OSError for a missing base asset or font) so callers can fall
    back to the pristine base cover.
    """
    base = Image.open(_BASE_COVER).convert("RGBA")
    font, stroke = _pick_font(name)
    draw = ImageDraw.Draw(base)
    width, height = base.size
    bbox = draw.textbbox((0, 0), name, font=font, stroke_width=stroke)
    text_w = bbox[2] - bbox[0]
    x = (width - text_w) / 2 - bbox[0]
    y = (height - _BOTTOM_MARGIN) - bbox[3]
    draw.text(
        (x, y),
        name,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=stroke,
        stroke_fill=(0, 0, 0, 255),
    )
    buf = io.BytesIO()
    base.save(buf, format="PNG")
    return buf.getvalue()


@lru_cache(maxsize=1)
def base_cover_bytes() -> bytes:
    """The pristine ``static/cover.png`` bytes (render-failure fallback)."""
    return _BASE_COVER.read_bytes()


def get_cached(guid: str) -> bytes | None:
    """Return the cached rendered cover for a podcast, or None."""
    return _cache.get(guid)


def set_cached(guid: str, png: bytes) -> None:
    """Store the rendered cover bytes for a podcast guid."""
    _cache[guid] = png


def invalidate(guid: str) -> None:
    """Drop a podcast's cached cover (e.g. when the podcast is deleted)."""
    _cache.pop(guid, None)