"""Tray icon images, generated at runtime with Pillow -- no external image
assets are shipped, and nothing here names an attorney, a firm, or a client.

Two variants of one generic document glyph: a neutral gray version for
"sharing off" and a green-accented version for "sharing on". Pillow's raster
drawing needs no display/X server, so this module is safe to import and call
on a headless CI runner -- unlike pystray or tkinter, it never touches a
windowing system.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

ICON_SIZE = 64

_TRANSPARENT = (0, 0, 0, 0)
_DOC_FILL = (247, 247, 247, 255)
_DOC_OUTLINE = (90, 90, 90, 255)
_LINE_COLOR = (140, 140, 140, 255)
_GRAY_ACCENT = (150, 150, 150, 255)
_GREEN_ACCENT = (34, 139, 69, 255)


def _document_glyph(accent: tuple[int, int, int, int]) -> Image.Image:
    """A minimal document-with-folded-corner shape plus a small accent dot in
    the corner -- the only thing that differs between the on/off icons is
    that accent color, drawn with primitive shapes (no font, no external
    file)."""
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), _TRANSPARENT)
    draw = ImageDraw.Draw(image)

    margin = 10
    fold = 16
    left, top = margin, margin
    right, bottom = ICON_SIZE - margin, ICON_SIZE - margin

    body = [
        (left, top),
        (right - fold, top),
        (right, top + fold),
        (right, bottom),
        (left, bottom),
    ]
    draw.polygon(body, fill=_DOC_FILL, outline=_DOC_OUTLINE)
    draw.polygon(
        [(right - fold, top), (right, top + fold), (right - fold, top + fold)],
        fill=_DOC_OUTLINE,
    )

    text_left = left + 8
    text_right = right - 8
    for offset in (24, 32, 40, 48):
        draw.line([(text_left, offset), (text_right, offset)], fill=_LINE_COLOR, width=2)

    accent_radius = 9
    accent_cx, accent_cy = ICON_SIZE - accent_radius - 2, ICON_SIZE - accent_radius - 2
    draw.ellipse(
        [
            (accent_cx - accent_radius, accent_cy - accent_radius),
            (accent_cx + accent_radius, accent_cy + accent_radius),
        ],
        fill=accent,
        outline=(255, 255, 255, 255),
        width=2,
    )

    return image


def sharing_off_icon() -> Image.Image:
    """Gray-accented icon: sharing is off (the default, safe state)."""
    return _document_glyph(_GRAY_ACCENT)


def sharing_on_icon() -> Image.Image:
    """Green-accented icon: sharing is on -- something is being served
    beyond localhost, so this should stand out at a glance."""
    return _document_glyph(_GREEN_ACCENT)
