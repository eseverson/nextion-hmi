"""Nextion drawing primitives backed by a per-page Pillow overlay.

Each primitive (cls, fill, line, cir, cirs, cle, xstr) renders into the
active page's `overlay` image — an RGBA layer the renderer composites on
top of component drawing. The overlay is created lazily on first draw and
cleared on page switch (see DisplayState.set_active).

Color arguments are 16-bit RGB565 ints as stored in Nextion attributes.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from sim.state import DisplayState, RGB565
from sim.renderer import load_font, font_size_for, align_text


def _ensure_overlay(state: DisplayState) -> Image.Image:
    """Allocate the active page's overlay if it doesn't exist yet, return it."""
    page = state.active_page
    if page.overlay is None:
        w = page.attrs.get("w", 480)
        h = page.attrs.get("h", 320)
        page.overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    return page.overlay


def _rgba(color: int) -> tuple[int, int, int, int]:
    r, g, b = RGB565(color).to_rgb888()
    return (r, g, b, 255)


def cls(state: DisplayState, color: int) -> None:
    """Fill the active page with `color` (RGB565)."""
    overlay = _ensure_overlay(state)
    page = state.active_page
    w = page.attrs.get("w", overlay.width)
    h = page.attrs.get("h", overlay.height)
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([0, 0, w - 1, h - 1], fill=_rgba(color))
    state.dirty = True


def fill(state: DisplayState, x: int, y: int, w: int, h: int, color: int) -> None:
    """Fill rectangle (x,y,w,h) with color (RGB565)."""
    overlay = _ensure_overlay(state)
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill=_rgba(color))
    state.dirty = True


def line(state: DisplayState, x1: int, y1: int, x2: int, y2: int, color: int) -> None:
    """Draw a 1px line from (x1,y1) to (x2,y2) in color (RGB565)."""
    overlay = _ensure_overlay(state)
    draw = ImageDraw.Draw(overlay)
    draw.line([x1, y1, x2, y2], fill=_rgba(color), width=1)
    state.dirty = True


def cir(state: DisplayState, cx: int, cy: int, r: int, color: int) -> None:
    """Draw an empty circle outline at (cx,cy) radius r in color (RGB565)."""
    overlay = _ensure_overlay(state)
    draw = ImageDraw.Draw(overlay)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=_rgba(color), width=1)
    state.dirty = True


def cirs(state: DisplayState, cx: int, cy: int, r: int, color: int) -> None:
    """Draw a filled circle at (cx,cy) radius r in color (RGB565)."""
    overlay = _ensure_overlay(state)
    draw = ImageDraw.Draw(overlay)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_rgba(color))
    state.dirty = True


def cle(state: DisplayState, x: int, y: int, w: int, h: int) -> None:
    """Erase rectangle on the overlay (transparent)."""
    overlay = _ensure_overlay(state)
    draw = ImageDraw.Draw(overlay)
    # Fill with transparent — RGBA (0,0,0,0) clears the region.
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill=(0, 0, 0, 0))
    state.dirty = True


def xstr(state: DisplayState, x: int, y: int, w: int, h: int,
         font_id: int, pco: int, bco: int, xcen: int, ycen: int,
         sta: int, text: str) -> None:
    """Draw text in a rect with the same alignment/style rules render_component uses
    for Text components. Uses the project's ZI font for `font_id` if available,
    otherwise falls back to Liberation Mono Bold (or Pillow default) sized to
    ~70% of `h`. xcen: 0=left,1=center,2=right. ycen: 0=top,1=center,2=bottom.
    sta: 0=crop, 1=solid bg, 2=image (treat 2 like 0 for now)."""
    overlay = _ensure_overlay(state)
    draw = ImageDraw.Draw(overlay)
    if sta == 1:
        draw.rectangle([x, y, x + w - 1, y + h - 1], fill=_rgba(bco))
    zi = (getattr(state, "fonts", {}) or {}).get(font_id)
    if zi is not None:
        # draw_zi_text expects an RGB image and a 3-tuple fill, but we have
        # an RGBA overlay. Use the RGBA fill directly — Pillow's paste with
        # an L-mode mask works fine on RGBA targets.
        from sim.renderer import draw_zi_text
        draw_zi_text(overlay, text, zi, (x, y, w, h), xcen, ycen, _rgba(pco))
    else:
        font_pt = font_size_for(font_id, h)
        font = load_font(font_pt)
        align_text(overlay, draw, text, font, (x, y, w, h), xcen, ycen, _rgba(pco))
    state.dirty = True
