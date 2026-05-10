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
T_RADIO = 57
T_QRCODE = 58
T_PICTURE = 112
T_DUAL_STATE_BUTTON = 53
T_CROP_PICTURE = 5
T_WAVEFORM = 0
T_UNKNOWN_113 = 113

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


def align_text(img: Image.Image, draw: ImageDraw.ImageDraw, text: str, font,
               box, xcen, ycen, fill):
    """Draw text inside box (x, y, w, h) with horiz/vert alignment.

    xcen: 0=left 1=center 2=right
    ycen: 0=top  1=center 2=bottom

    Text that overflows the component is clipped on all sides — matches
    Nextion firmware (centered overflow is trimmed equally left/right,
    not left-aligned with right cut-off).
    """
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    if xcen == 1:
        tx = (w - tw) // 2 - bbox[0]
    elif xcen == 2:
        tx = w - tw - bbox[0]
    else:
        tx = -bbox[0]
    if ycen == 1:
        ty = (h - th) // 2 - bbox[1]
    elif ycen == 2:
        ty = h - th - bbox[1]
    else:
        ty = -bbox[1]
    clip = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(clip).text((tx, ty), text, font=font, fill=fill)
    img.paste(clip, (x, y), clip)


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
    if w <= 0 or h <= 0:
        return
    cps = font.encode_text(text)
    tw = _zi_text_width(font, cps)
    th = font.height
    if xcen == 1:
        tx = (w - tw) // 2
    elif xcen == 2:
        tx = w - tw
    else:
        tx = 0
    if ycen == 1:
        ty = (h - th) // 2
    elif ycen == 2:
        ty = h - th
    else:
        ty = 0
    # Compose into a clip the size of the component so glyph overflow is
    # trimmed equally on all sides (Nextion firmware clips text to the
    # widget bounds — centered overflow is cut from both edges, not just
    # the right).
    clip = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rgba_fill = fill if len(fill) == 4 else fill + (255,)
    cursor = tx
    for cp in cps:
        gw = font.glyph_width(cp) or font.width or 0
        if gw <= 0:
            continue
        mask = font.glyph_image(cp)
        clip.paste(rgba_fill,
                   (cursor, ty, cursor + mask.size[0], ty + mask.size[1]),
                   mask)
        cursor += gw
    img.paste(clip, (x, y), clip)


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
    align_text(img, draw, text, ttf, box, xcen, ycen, fill)


def render_component(img: Image.Image, draw: ImageDraw.ImageDraw, c, page_bg,
                     fonts: dict | None = None, pictures: dict | None = None,
                     time_ms: int = 0):
    fonts = fonts or {}
    pictures = pictures or {}
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

    # Type-specific defaults for components where the TFT-only loader
    # can't yet pull the bco/pco/val attrs from per-component bytecode.
    # These match the editor's freshly-added defaults so projects that
    # haven't customised them render correctly.
    if t == T_WAVEFORM and a.get("bco") is None:
        a = dict(a); a["bco"] = 0           # black
    elif t == T_GAUGE and a.get("bco") is None:
        a = dict(a); a["bco"] = 65535       # white
        a.setdefault("pco", 1024)           # green
    elif t in (T_CHECKBOX, T_RADIO) and a.get("bco") is None:
        a = dict(a); a["bco"] = 65535       # white
        a.setdefault("pco", 0)              # black
        a.setdefault("val", 1)              # checked
    elif t == T_SCROLLING_TEXT and a.get("bco") is None:
        a = dict(a); a["bco"] = 65535       # white
        a.setdefault("pco", 0)              # black
    elif t == T_QRCODE and a.get("bco") is None:
        # Editor default for QR: bco=white, pco=black. The editor
        # always fills the component with bco regardless of sta, so
        # we don't override sta here.
        a = dict(a)
        a.setdefault("bco", 65535)
        a.setdefault("pco", 0)
    elif t == T_DUAL_STATE_BUTTON and a.get("bco") is None:
        a = dict(a); a["bco"] = 50712       # editor's default button gray
        a.setdefault("pco", 0)
    elif t == T_CROP_PICTURE and a.get("bco") is None:
        a = dict(a); a["bco"] = 50712       # similar gray placeholder

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

    if t == T_TEXT:
        txt = a.get("txt", "") or ""
        _draw_text(img, draw, txt, a.get("font"), fonts, (x, y, w, h),
                   a.get("xcen", 1), a.get("ycen", 1), pco)
        return

    if t == T_SCROLLING_TEXT:
        # Scrolling Text: per Nextion, dir=0 scrolls text rightward
        # (enters from left, exits right) and dir=1 scrolls leftward
        # (the classic marquee — enters from right, exits left).
        txt = a.get("txt", "") or ""
        font_id = a.get("font")
        from PIL import Image as _Image
        clip = _Image.new("RGB", (max(w, 1), max(h, 1)), bco)
        clip_draw = ImageDraw.Draw(clip)
        try:
            ttf = load_font(font_size_for(font_id, fonts))
            tbbox = clip_draw.textbbox((0, 0), txt, font=ttf)
            text_w = tbbox[2] - tbbox[0]
        except Exception:
            text_w = len(txt) * 8
        cycle = w + text_w
        if cycle <= 0:
            cycle = max(w, 1)
        speed_px_per_sec = 60
        progress = int((time_ms / 1000) * speed_px_per_sec) % cycle
        direction = a.get("dir", 0) or 0
        if direction == 1:
            text_x = w - progress           # right-to-left (marquee)
        else:
            text_x = -text_w + progress     # left-to-right (default dir=0)
        _draw_text(clip, clip_draw, txt, font_id, fonts,
                   (text_x, 0, text_w, h),
                   a.get("xcen", 0), a.get("ycen", 1), pco)
        img.paste(clip, (x, y))
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
        # Render as an outlined circle with a single radial line.
        cx, cy = x + w // 2, y + h // 2
        r = min(w, h) // 2 - 2
        if r > 0:
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=pco, width=2)
            val = a.get("val", 0) or 0
            # Nextion gauge convention: val 0..360 in degrees, with
            # val=0 pointing LEFT (180° in standard math coords),
            # increasing clockwise. So screen angle = 180° + val.
            import math
            angle = math.radians(180 + val)
            ex = cx + int(r * 0.85 * math.cos(angle))
            ey = cy + int(r * 0.85 * math.sin(angle))
            draw.line([cx, cy, ex, ey], fill=pco, width=3)
        return

    if t == T_PICTURE:
        # Composite the source bitmap at (x, y). Picture's `pic` attr
        # references the picture id; the image is sized at its native
        # dimensions (which match the component's w/h by convention).
        pic_id = a.get("pic")
        pic_img = pictures.get(pic_id) if isinstance(pic_id, int) else None
        if pic_img is not None:
            # Match the component box: paste at x,y, cropping/resizing
            # if dimensions differ.
            if pic_img.size != (w, h):
                pic_img = pic_img.resize((w, h))
            img.paste(pic_img, (x, y))
        else:
            # No picture data available — outline placeholder.
            draw.rectangle([x, y, x + w - 1, y + h - 1],
                           outline=(160, 160, 160))
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

    if t == T_DUAL_STATE_BUTTON:
        # Like Button but with two states (val=0 normal, val=1 pressed)
        # We render the current state's text centered.
        txt = a.get("txt", "") or ""
        _draw_text(img, draw, txt, a.get("font"), fonts, (x, y, w, h),
                   a.get("xcen", 1), a.get("ycen", 1), pco)
        return

    if t == T_CHECKBOX:
        # Box outline + inner fill if val=1
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline=pco, width=2)
        if a.get("val", 0):
            m = max(3, min(w, h) // 4)
            draw.rectangle([x + m, y + m, x + w - 1 - m, y + h - 1 - m], fill=pco)
        return

    if t == T_RADIO:
        # Circle outline + inner dot if val=1
        draw.ellipse([x, y, x + w - 1, y + h - 1], outline=pco, width=2)
        if a.get("val", 0):
            m = max(3, min(w, h) // 4)
            draw.ellipse([x + m, y + m, x + w - 1 - m, y + h - 1 - m], fill=pco)
        return

    if t == T_QRCODE:
        # Always fill the box with bco — the Nextion editor shows the QR
        # on a solid background regardless of sta. Then place the matrix
        # centered with a small interior padding (the editor leaves a
        # visible margin around the dark modules).
        draw.rectangle([x, y, x + w - 1, y + h - 1], fill=bco)
        txt = a.get("txt", "") or ""
        if txt:
            try:
                import segno
                qr = segno.make(txt, error="m")
                matrix = qr.matrix
                mw = len(matrix[0])
                mh = len(matrix)
                # Small quiet zone around the matrix. Roughly 3% of the
                # smaller side per edge, clamped to 1 px minimum so tiny
                # QR boxes still get a separator.
                pad = max(1, min(w, h) // 32)
                inner_w = max(1, w - 2 * pad)
                inner_h = max(1, h - 2 * pad)
                scale = max(1, min(inner_w // mw, inner_h // mh))
                qr_w = mw * scale
                qr_h = mh * scale
                ox = x + (w - qr_w) // 2
                oy = y + (h - qr_h) // 2
                for ry, row in enumerate(matrix):
                    for cx_, cell in enumerate(row):
                        if cell:
                            draw.rectangle(
                                [ox + cx_ * scale, oy + ry * scale,
                                 ox + (cx_ + 1) * scale - 1,
                                 oy + (ry + 1) * scale - 1],
                                fill=pco)
                return
            except ImportError:
                pass
            except Exception:
                pass
        # Fallback: placeholder finder pattern.
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline=pco)
        fs = max(3, min(w, h) // 6)
        for fx, fy in [(x + 4, y + 4),
                       (x + w - 4 - fs, y + 4),
                       (x + 4, y + h - 4 - fs)]:
            draw.rectangle([fx, fy, fx + fs - 1, fy + fs - 1], outline=pco, width=2)
            draw.rectangle([fx + fs // 3, fy + fs // 3,
                            fx + fs - 1 - fs // 3, fy + fs - 1 - fs // 3], fill=pco)
        return

    if t == T_WAVEFORM:
        # Waveform/Graph (type 0). Without a sample history we just
        # show the bg fill + axes border. The sim doesn't simulate
        # `add` commands populating samples yet.
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline=pco)
        return

    if t == T_CROP_PICTURE:
        # Crop Picture (type 5): draws a sub-region of a picture. We
        # don't have crop coords decoded yet — render the bg fill so
        # the component is visible.
        return

    if t in (T_UNKNOWN_113,):
        # Type 113 — undocumented in our sources. Show a hint outline
        # so it's visible without spamming `<type N>` placeholder.
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline=pco)
        return

    # Fallback: outline + label so unhandled types are visible
    draw.rectangle([x, y, x + w - 1, y + h - 1], outline=(180, 100, 100))
    label = f"<type {t}>"
    align_text(img, draw, label, load_font(10), (x, y, w, h), 1, 1, (180, 100, 100))


class Renderer:
    """Renders a DisplayState's active page into a Pillow Image."""

    def render(
        self,
        state,
        show_outlines: bool = False,
        show_ids: bool = False,
    ) -> Image.Image:
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
        pictures = getattr(state, "pictures", {}) or {}
        time_ms = getattr(state, "time_ms", 0)
        # Render in id order (matches Nextion paint order)
        for c in sorted(page.components, key=lambda c: c.attrs.get("id", 0)):
            # Adapt: render_component expects a Nextion2Text-style component
            # with c.rawData["att"]. Build a tiny shim.
            shim = type("Shim", (), {"rawData": {"att": c.attrs}})()
            render_component(img, draw, shim, bg, fonts, pictures, time_ms)
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
        # Debug overlays — drawn at full brightness on top of everything.
        if show_outlines or show_ids:
            ov = ImageDraw.Draw(img)
            label_font = load_font(10)
            for c in sorted(page.components, key=lambda c: c.attrs.get("id", 0)):
                a = c.attrs
                cw = a.get("w", 0); ch = a.get("h", 0)
                if cw <= 0 or ch <= 0:
                    continue
                cx = a.get("x", 0); cy = a.get("y", 0)
                if show_outlines:
                    ov.rectangle(
                        [cx, cy, cx + cw - 1, cy + ch - 1],
                        outline=(255, 0, 255),
                    )
                if show_ids:
                    ov.text(
                        (cx + 1, cy + 1),
                        str(a.get("id", "?")),
                        font=label_font,
                        fill=(255, 0, 255),
                    )
        # Apply orientation as a post-rotation. The HMI stores logical
        # coordinates (as authored at 0°); the device runtime rotates the
        # framebuffer for 180°/90°/270°. We mirror that here so a project
        # configured with H1+0x14=0x03 (180°) renders flipped.
        orientation = getattr(state, "orientation", 0) or 0
        if orientation == 180:
            img = img.transpose(Image.ROTATE_180)
        elif orientation == 90:
            img = img.transpose(Image.ROTATE_90)
        elif orientation == 270:
            img = img.transpose(Image.ROTATE_270)
        state.dirty = False
        return img
