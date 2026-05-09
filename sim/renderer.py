"""Renderer for Nextion DisplayState — produces a Pillow image.

Extracted from scripts/preview_page.py so the simulator and the static
previewer can share a single rendering implementation.
"""
from __future__ import annotations
import os

from PIL import Image, ImageDraw, ImageFont

from sim.font import ZiFont


# Component type IDs (subset; matches Nextion2Text.Component.attributes["type"]["mapping"])
T_PAGE = 121
T_VARIABLE = 52
T_NUMBER = 54
T_XFLOAT = 59
T_TEXT = 116
T_SCROLLING_TEXT = 55
T_PROGRESS_BAR = 106
T_GAUGE = 122
T_BUTTON = 98
T_SLIDER = 1
T_HOTSPOT = 109
T_TIMER = 51
T_CHECKBOX = 56

INVISIBLE_TYPES = {T_VARIABLE, T_HOTSPOT, T_TIMER}

# Liberation Mono ships with most distros; matches Nextion's "liberiso-8859-1"
# theme aesthetically. Falls back to PIL's default if unavailable.
FONT_CANDIDATES = [
    "/usr/share/fonts/liberation-mono-fonts/LiberationMono-Bold.ttf",
    "/usr/share/fonts/liberation-mono/LiberationMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/google-noto-vf/NotoSansMono[wght].ttf",
]


def rgb565_to_rgb888(c: int | None) -> tuple[int, int, int] | None:
    if c is None:
        return None
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return (r * 255 // 31, g * 255 // 63, b * 255 // 31)


def find_font_file() -> str | None:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


_FONT_CACHE: dict[int, ImageFont.FreeTypeFont] = {}


def load_font(point_size: int) -> ImageFont.ImageFont:
    if point_size in _FONT_CACHE:
        return _FONT_CACHE[point_size]
    path = find_font_file()
    if path:
        f = ImageFont.truetype(path, point_size)
    else:
        f = ImageFont.load_default()
    _FONT_CACHE[point_size] = f
    return f


def font_size_for(font_id: int | None, comp_height: int) -> int:
    """Pick a TTF point size that roughly fills a Nextion font slot.

    Nextion font 0 in this project is ~16px tall (label use, comp h=30).
    Nextion font 1 is ~40px tall (digit use, comp h=50). Heuristic:
    use ~70% of component height as point size.
    """
    if comp_height <= 0:
        return 12
    return max(8, int(comp_height * 0.7))


def align_text(draw: ImageDraw.ImageDraw, text: str, font, box, xcen, ycen,
               fill):
    """Draw text inside box (x, y, w, h) with horiz/vert alignment.

    xcen: 0=left 1=center 2=right
    ycen: 0=top  1=center 2=bottom
    """
    x, y, w, h = box
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    if xcen == 1:
        tx = x + (w - tw) // 2 - bbox[0]
    elif xcen == 2:
        tx = x + w - tw - bbox[0]
    else:
        tx = x - bbox[0]
    if ycen == 1:
        ty = y + (h - th) // 2 - bbox[1]
    elif ycen == 2:
        ty = y + h - th - bbox[1]
    else:
        ty = y - bbox[1]
    draw.text((tx, ty), text, font=font, fill=fill)


def _zi_text_width(font: ZiFont, codepoints: list[int]) -> int:
    """Total advance width for a sequence of font codepoints."""
    return sum(font.glyph_width(cp) or font.width or 0 for cp in codepoints)


def draw_zi_text(
    img: Image.Image,
    text: str,
    font: ZiFont,
    box: tuple[int, int, int, int],
    xcen: int,
    ycen: int,
    fill: tuple[int, int, int],
) -> None:
    """Render `text` glyph-by-glyph onto `img` using the ZI bitmap font.

    Each glyph's L-mode mask is colorised with `fill` and pasted at
    integer pixel coordinates. The component's box is used for alignment.
    Text that overflows the box is allowed to spill — Nextion firmware
    behaves the same.
    """
    x, y, w, h = box
    cps = font.encode_text(text)
    tw = _zi_text_width(font, cps)
    th = font.height
    if xcen == 1:
        tx = x + (w - tw) // 2
    elif xcen == 2:
        tx = x + w - tw
    else:
        tx = x
    if ycen == 1:
        ty = y + (h - th) // 2
    elif ycen == 2:
        ty = y + h - th
    else:
        ty = y
    # Solid-color L-mode -> RGBA with the component's pco. We composite
    # each glyph individually so anti-aliased pixels blend with the bg.
    cursor = tx
    for cp in cps:
        gw = font.glyph_width(cp) or font.width or 0
        if gw <= 0:
            continue
        mask = font.glyph_image(cp)
        # Build a coloured patch the same size as the mask, then paste using
        # the mask as alpha. img is RGB so we go through the L mask directly.
        # Pillow's `paste(color, box, mask)` accepts an L-mode mask.
        img.paste(fill, (cursor, ty, cursor + mask.size[0], ty + mask.size[1]),
                  mask)
        cursor += gw


def format_xfloat(val: int, vvs0: int, vvs1: int) -> str:
    """Format an XFloat. vvs0 = padding/leading-zero count, vvs1 = decimals.

    Nextion's XFloat stores an integer val and renders it as
    int_part = val // 10**vvs1, frac_part = val % 10**vvs1, padded.
    With vvs1=0 it's a plain integer. We mirror that.
    """
    if vvs1 and vvs1 > 0:
        scale = 10 ** vvs1
        sign = "-" if val < 0 else ""
        v = abs(val)
        whole = v // scale
        frac = v % scale
        s = f"{sign}{whole}.{frac:0{vvs1}d}"
    else:
        s = str(val)
    if vvs0 and vvs0 > len(s.lstrip("-")):
        pad = vvs0 - len(s.lstrip("-"))
        if s.startswith("-"):
            s = "-" + "0" * pad + s[1:]
        else:
            s = "0" * pad + s
    return s


def _draw_text(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    text: str,
    font_id,
    fonts: dict,
    box: tuple[int, int, int, int],
    xcen: int,
    ycen: int,
    fill,
) -> None:
    """Render `text` using the ZI font for `font_id` if available, else the TTF substitute."""
    zi = fonts.get(font_id) if isinstance(font_id, int) else None
    if zi is not None:
        draw_zi_text(img, text, zi, box, xcen, ycen, fill)
        return
    _, _, _, h = box
    font_pt = font_size_for(font_id, h)
    ttf = load_font(font_pt)
    align_text(draw, text, ttf, box, xcen, ycen, fill)


def render_component(img: Image.Image, draw: ImageDraw.ImageDraw, c, page_bg, fonts: dict | None = None):
    fonts = fonts or {}
    a = c.rawData["att"]
    t = a.get("type")
    if t in INVISIBLE_TYPES:
        return
    # Honor the runtime `vis` attribute. Defaults to 1 (visible) for
    # components that don't have it set.
    if a.get("vis", 1) == 0:
        return
    x = a.get("x", 0)
    y = a.get("y", 0)
    w = a.get("w", 0)
    h = a.get("h", 0)
    if w <= 0 or h <= 0:
        return

    bco = rgb565_to_rgb888(a.get("bco")) or page_bg
    pco = rgb565_to_rgb888(a.get("pco")) or (255, 255, 255)
    sta = a.get("sta", 1)  # 1 = solid color (the only mode we render)

    # Background fill (most components) — but sta=0 on Page = "no background".
    if t == T_PAGE:
        # Page fill already done by the caller. Nothing more to render.
        return
    if sta == 1:
        draw.rectangle([x, y, x + w - 1, y + h - 1], fill=bco)

    if t == T_PROGRESS_BAR:
        val = max(0, min(100, a.get("val", 0)))
        fill_w = (w * val) // 100
        if fill_w > 0:
            draw.rectangle([x, y, x + fill_w - 1, y + h - 1], fill=pco)
        # Outline so the bar is visible even at 0%
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline=(80, 80, 80))
        return

    if t == T_TEXT or t == T_SCROLLING_TEXT:
        txt = a.get("txt", "") or ""
        _draw_text(img, draw, txt, a.get("font"), fonts, (x, y, w, h),
                   a.get("xcen", 1), a.get("ycen", 1), pco)
        return

    if t in (T_NUMBER, T_XFLOAT):
        val = a.get("val", 0) or 0
        if t == T_XFLOAT:
            s = format_xfloat(val, a.get("vvs0", 0) or 0, a.get("vvs1", 0) or 0)
        else:
            s = str(val)
        _draw_text(img, draw, s, a.get("font"), fonts, (x, y, w, h),
                   a.get("xcen", 1), a.get("ycen", 1), pco)
        return

    if t == T_BUTTON:
        txt = a.get("txt", "") or ""
        _draw_text(img, draw, txt, a.get("font"), fonts, (x, y, w, h),
                   a.get("xcen", 1), a.get("ycen", 1), pco)
        return

    if t == T_GAUGE:
        # Render as an outlined circle with a single radial line
        cx, cy = x + w // 2, y + h // 2
        r = min(w, h) // 2 - 2
        if r > 0:
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=pco, width=2)
            val = a.get("val", 0) or 0
            # Nextion gauge: val 0..360, 0 = pointing down (6 o'clock)?
            # We just draw something reasonable for preview purposes.
            import math
            angle = math.radians(val - 90)
            ex = cx + int(r * 0.85 * math.cos(angle))
            ey = cy + int(r * 0.85 * math.sin(angle))
            draw.line([cx, cy, ex, ey], fill=pco, width=3)
        return

    if t == T_SLIDER:
        # Track + handle
        track_y = y + h // 2 - 2
        draw.rectangle([x, track_y, x + w - 1, track_y + 3], fill=pco)
        val = a.get("val", 0) or 0
        maxval = a.get("maxval", 100) or 100
        if maxval == 0:
            maxval = 100
        hx = x + (w * max(0, min(maxval, val))) // maxval
        draw.ellipse([hx - 6, y + h // 2 - 6, hx + 6, y + h // 2 + 6], fill=pco)
        return

    # Fallback: outline + label so unhandled types are visible
    draw.rectangle([x, y, x + w - 1, y + h - 1], outline=(180, 100, 100))
    label = f"<type {t}>"
    align_text(draw, label, load_font(10), (x, y, w, h), 1, 1, (180, 100, 100))


class Renderer:
    """Renders a DisplayState's active page into a Pillow Image."""

    def render(self, state) -> Image.Image:
        page = state.active_page
        w = page.attrs.get("w", 480)
        h = page.attrs.get("h", 320)
        sta = page.attrs.get("sta", 1)
        if sta == 1:
            bg = rgb565_to_rgb888(page.attrs.get("bco")) or (0, 0, 0)
        else:
            bg = (255, 255, 255)
        img = Image.new("RGB", (w, h), bg)
        draw = ImageDraw.Draw(img)
        fonts = getattr(state, "fonts", {}) or {}
        # Render in id order (matches Nextion paint order)
        for c in sorted(page.components, key=lambda c: c.attrs.get("id", 0)):
            # Adapt: render_component expects a Nextion2Text-style component
            # with c.rawData["att"]. Build a tiny shim.
            shim = type("Shim", (), {"rawData": {"att": c.attrs}})()
            render_component(img, draw, shim, bg, fonts)
        # Composite the per-page draw overlay (filled by `fill`/`xstr`/etc.
        # primitives invoked from event scripts). Nextion paints these on
        # top of static components.
        overlay = getattr(page, "overlay", None)
        if overlay is not None:
            if overlay.mode != "RGBA":
                overlay = overlay.convert("RGBA")
            img = img.convert("RGBA")
            img.alpha_composite(overlay)
            img = img.convert("RGB")
        # Apply dim
        dim = max(0, min(100, getattr(state, "dim", 100)))
        if dim < 100:
            factor = max(0.05, dim / 100.0)
            img = Image.eval(img, lambda v: int(v * factor))
        state.dirty = False
        return img
