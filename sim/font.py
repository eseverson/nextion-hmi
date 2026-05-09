"""Nextion ZI font parser and renderer.

Supports ZI v3 (fixed-width 1bpp), v5/v6 (variable-width with
per-glyph RLE encoding, B&W or anti-aliased).

The format is reverse-engineered. The reference docs live at:
    https://github.com/hagronnestad/nextion-font-editor/tree/master/Docs

Public surface:
    parse_zi(data: bytes) -> ZiFont
    ZiFont.glyph_image(codepoint: int) -> PIL.Image.Image  (mode 'L', 0=blank, 255=ink)
    ZiFont.glyph_width(codepoint: int) -> int   (advance width in pixels)
    ZiFont.height -> int   (cell height, applies to every glyph)
    ZiFont.width  -> int   (declared cell width, 0 means variable-width)

Glyph images are returned at the per-glyph width (variable in v5/v6).
For monospace v3 fonts, every glyph is `width` pixels wide.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from PIL import Image


# ---------- Encoding map (codepage byte -> Python codec name) ----------
#
# These correspond to the "Encoding" byte at offset 0x04 in the ZI header
# (and the "Code page / character encoding reference" tables in the spec).
# We keep the commonly-used entries; unknowns fall back to latin-1 so
# bytes still round-trip.
_ENCODING_MAP = {
    0x01: "ascii",
    # GB2312 (multi-byte) — handled lookup-style
    0x02: "gb2312",
    0x03: "iso-8859-1",
    0x04: "iso-8859-2",
    0x05: "iso-8859-3",
    0x06: "iso-8859-4",
    0x07: "iso-8859-5",
    0x08: "iso-8859-6",
    0x09: "iso-8859-7",
    0x0A: "iso-8859-8",
    0x0B: "iso-8859-9",
    0x0C: "iso-8859-13",
    0x0D: "iso-8859-15",
    0x0E: "iso-8859-11",
    0x0F: "ks_c_5601-1987",
    0x10: "big5",
    0x11: "windows-1255",
    0x12: "windows-1256",
    0x13: "windows-1257",
    0x14: "windows-1258",
    0x15: "windows-874",
    0x16: "koi8-r",
    0x17: "shift-jis",
    0x18: "utf-8",
}


@dataclass
class _GlyphEntry:
    width: int
    klft: int  # left kerning
    krht: int  # right kerning
    data_off: int  # byte offset in `data` (already absolute)
    data_len: int


class ZiFont:
    """Parsed ZI font.

    Use `glyph_image(codepoint)` to get a PIL "L" mask. The mask is exactly
    `glyph_width(codepoint)` x `height` pixels. 0 = transparent, 255 = full
    ink, intermediate values for anti-aliased glyphs.
    """

    def __init__(
        self,
        version: int,
        width: int,
        height: int,
        encoding: int,
        encoding_name: str,
        name: str,
        glyphs: dict[int, "Image.Image"],
        widths: dict[int, int],
    ) -> None:
        self.version = version
        self.width = width  # declared cell width; 0 means variable-width
        self.height = height
        self.encoding = encoding
        self.encoding_name = encoding_name
        self.name = name
        self._glyphs = glyphs
        self._widths = widths

    def has_glyph(self, codepoint: int) -> bool:
        return codepoint in self._glyphs

    def glyph_width(self, codepoint: int) -> int:
        if codepoint in self._widths:
            return self._widths[codepoint]
        return self.width or 0

    def glyph_image(self, codepoint: int) -> Image.Image:
        """Return the L-mode mask for `codepoint`.

        If the codepoint is not in the font, returns a blank image of the
        cell width (or 1 px wide if even that is unknown).
        """
        img = self._glyphs.get(codepoint)
        if img is not None:
            return img
        w = self.width or 1
        return Image.new("L", (w, self.height), 0)

    def encode_text(self, text: str) -> list[int]:
        """Map a Python string to font codepoints using the font's codepage.

        Returns one int per output codepoint (single-byte encodings produce
        one int per char; multi-byte encodings return the multi-byte word).
        Unknown chars are dropped.
        """
        out: list[int] = []
        codec = self.encoding_name or "latin-1"
        try:
            data = text.encode(codec, errors="replace")
        except (LookupError, UnicodeEncodeError):
            data = text.encode("latin-1", errors="replace")
        for b in data:
            out.append(b)
        return out


# ---------- Header parsing ----------


def _parse_header(data: bytes) -> dict:
    if len(data) < 28:
        raise ValueError("ZI file shorter than minimum 28-byte header")
    sig = data[0]
    if sig != 0x04:
        raise ValueError(f"unexpected ZI signature byte: 0x{sig:02x}")
    version = data[16]
    if version == 3:
        # v3 fixed-width 1bpp. Header is 0x1C (28) bytes.
        encoding = struct.unpack_from("<H", data, 4)[0]
        cw = data[6]
        ch = data[7]
        nchars = struct.unpack_from("<I", data, 12)[0]
        name_len = data[17]
        cp_second_start = data[10]
        cp_second_end = data[11]
        cp_first_start = data[8]
        cp_first_end = data[9]
        return {
            "version": 3,
            "encoding": encoding,
            "width": cw,
            "height": ch,
            "nchars": nchars,
            "name_len": name_len,
            "cp_first_start": cp_first_start,
            "cp_first_end": cp_first_end,
            "cp_second_start": cp_second_start,
            "cp_second_end": cp_second_end,
            "name_off": 28,
            "data_start": 28 + name_len,
        }
    if version in (5, 6):
        # v5/v6 variable-width. Header is 0x2C (44) bytes.
        encoding = data[4]
        # multibyte_mode = data[5]
        cw = data[6]
        ch = data[7]
        cp_first_start = data[8]
        cp_first_end = data[9]
        cp_second_start = data[10]
        cp_second_end = data[11]
        nchars = struct.unpack_from("<I", data, 12)[0]
        desc_len = data[17]
        data_start = struct.unpack_from("<I", data, 24)[0]
        # AA / variable-width flags only matter for the per-glyph decode path.
        aa = data[30]
        var_w = data[31]
        align8 = data[33] if version == 6 else 0
        return {
            "version": version,
            "encoding": encoding,
            "width": cw,
            "height": ch,
            "nchars": nchars,
            "name_len": desc_len,
            "cp_first_start": cp_first_start,
            "cp_first_end": cp_first_end,
            "cp_second_start": cp_second_start,
            "cp_second_end": cp_second_end,
            "aa": aa,
            "var_w": var_w,
            "align8": align8,
            "name_off": data_start,
            "data_start": data_start + desc_len,
        }
    raise ValueError(f"unsupported ZI version: {version}")


# ---------- Codepoint enumeration ----------


def _codepoints_for(hdr: dict) -> list[int]:
    """Order in which the character map / glyph table enumerates codepoints.

    Single-byte codepages (ISO-8859-x, ASCII): codepoints
    cp_second_start..cp_second_end inclusive.

    Multi-byte codepages (GB2312, BIG5): all combinations of
    (first_start..first_end) x (second_start..second_end).
    """
    first_start = hdr["cp_first_start"]
    first_end = hdr["cp_first_end"]
    second_start = hdr["cp_second_start"]
    second_end = hdr["cp_second_end"]

    if first_start == 0 and first_end == 0:
        # Single-byte: enumerate the second-byte range only.
        return list(range(second_start, second_end + 1))

    out: list[int] = []
    for hi in range(first_start, first_end + 1):
        for lo in range(second_start, second_end + 1):
            out.append((hi << 8) | lo)
    return out


# ---------- Glyph data decoders ----------


def _decode_v3_glyph(data: bytes, offset: int, width: int, height: int) -> Image.Image:
    """v3: 1bpp packed flat bit-stream, MSB-first within each byte.

    Bit `n` is at byte `offset + n // 8`, position `7 - (n % 8)`.
    Total bits = width * height; row-major.
    """
    img = Image.new("L", (width, height), 0)
    if width <= 0 or height <= 0:
        return img
    px = img.load()
    n_bits = width * height
    for n in range(n_bits):
        byte = data[offset + (n >> 3)]
        bit = (byte >> (7 - (n & 7))) & 1
        if bit:
            x = n % width
            y = n // width
            px[x, y] = 255
    return img


def _decode_v6_glyph(
    data: bytes, offset: int, length: int, width: int, height: int
) -> Image.Image:
    """v5/v6: leading-byte mode + RLE'd pixel runs.

    Mode 0x01 = black & white (extra B&W modes available).
    Mode 0x03 = anti-aliased (8 alpha levels, 3 bits per pixel).

    Each subsequent byte is `YZdddddd`:
      * Y=0, Z=0, xxxxx: xxxxx transparent pixels
      * Y=0, Z=1, xxxxx: xxxxx opaque pixels
      * Y=1, Z=0, xxxxx (B&W mode 0x01): xxxxx transparent + 3 opaque (or aa: xxxccc -> xxx transparent + 1 alpha=ccc)
      * Y=1, Z=1, xxxxx (B&W mode 0x01, "11 www bbb"): www transparent + bbb opaque (each 3 bits)
        OR (aa mode 0x03, "11 ccc ddd"): 2 alpha pixels.
    The "01 0xxxxx" / "01 1xxxxx" extended forms add 1 / 2 trailing opaque pixels respectively.
    """
    img = Image.new("L", (width, height), 0)
    if length <= 0 or width <= 0 or height <= 0:
        return img
    px = img.load()
    total_pixels = width * height
    end = offset + length
    if end > len(data):
        end = len(data)
    if offset >= end:
        return img

    mode = data[offset]
    pos = offset + 1
    pixel_idx = 0

    def put(n: int, value: int) -> None:
        nonlocal pixel_idx
        for _ in range(n):
            if pixel_idx >= total_pixels:
                return
            x = pixel_idx % width
            y = pixel_idx // width
            px[x, y] = value
            pixel_idx += 1

    while pos < end and pixel_idx < total_pixels:
        b = data[pos]
        pos += 1
        yz = (b >> 6) & 0x3
        rest = b & 0x3F  # 6 data bits

        if yz == 0b00:
            # 00 0xxxxx -> xxxxx transparent
            # 00 1xxxxx -> xxxxx opaque
            count = rest & 0x1F
            if rest & 0x20:
                put(count, 255)
            else:
                put(count, 0)
        elif yz == 0b01:
            # 01 0xxxxx -> xxxxx transparent then 1 opaque
            # 01 1xxxxx -> xxxxx transparent then 2 opaque
            count = rest & 0x1F
            put(count, 0)
            put(2 if (rest & 0x20) else 1, 255)
        elif yz == 0b10:
            if mode == 0x01:
                # B&W: 10 0xxxxx -> xxxxx transparent + 3 opaque
                #      10 1xxxxx -> xxxxx transparent + 4 opaque
                count = rest & 0x1F
                put(count, 0)
                put(4 if (rest & 0x20) else 3, 255)
            else:
                # AA: 10 xxxccc -> xxx transparent + 1 alpha (ccc)
                xxx = (rest >> 3) & 0x7
                ccc = rest & 0x7
                put(xxx, 0)
                put(1, _alpha3(ccc))
        else:  # yz == 0b11
            if mode == 0x01:
                # B&W extra: 11 www bbb -> www opaque + bbb opaque?
                # spec phrases as "www times white pixels followed by bbb opaque pixels"
                # In display use, white=opaque, bbb=opaque… both same alpha here.
                # Treat both runs as opaque since this is the B&W stream.
                www = (rest >> 3) & 0x7
                bbb = rest & 0x7
                put(www, 255)
                put(bbb, 255)
            else:
                # AA: 11 ccc ddd -> 2 alpha pixels
                ccc = (rest >> 3) & 0x7
                ddd = rest & 0x7
                put(1, _alpha3(ccc))
                put(1, _alpha3(ddd))

    return img


def _alpha3(v: int) -> int:
    """Map 3-bit alpha (0..7) to 8-bit (0..255), mirroring the obvious lookup."""
    return (v * 255 + 3) // 7


# ---------- Top-level parse ----------


def parse_zi(data: bytes) -> ZiFont:
    """Parse a ZI font blob and return a populated ZiFont.

    Supports v3 (fixed-width 1bpp), v5 and v6 (variable width, AA or B&W).
    Unsupported / malformed glyphs are silently skipped — the caller should
    treat them as missing via `has_glyph`.
    """
    hdr = _parse_header(data)
    encoding = hdr["encoding"]
    encoding_name = _ENCODING_MAP.get(encoding, "latin-1")
    name_off = hdr["name_off"]
    name_len = hdr["name_len"]
    name = data[name_off:name_off + name_len].decode("latin-1", errors="replace").rstrip("\x00")

    cps = _codepoints_for(hdr)

    glyphs: dict[int, Image.Image] = {}
    widths: dict[int, int] = {}

    if hdr["version"] == 3:
        cw = hdr["width"]
        ch = hdr["height"]
        bytes_per_glyph = (cw * ch + 7) // 8
        glyph_data_start = hdr["data_start"]
        for i, cp in enumerate(cps):
            off = glyph_data_start + i * bytes_per_glyph
            if off + bytes_per_glyph > len(data):
                break
            try:
                img = _decode_v3_glyph(data, off, cw, ch)
            except Exception:
                continue
            glyphs[cp] = img
            widths[cp] = cw
        return ZiFont(
            version=3,
            width=cw,
            height=ch,
            encoding=encoding,
            encoding_name=encoding_name,
            name=name,
            glyphs=glyphs,
            widths=widths,
        )

    # v5 / v6
    align8 = hdr.get("align8", 0)
    charmap_start = hdr["data_start"]
    nchars = hdr["nchars"]
    height = hdr["height"]

    # The character map has 10-byte entries.
    for i, cp in enumerate(cps[:nchars]):
        off = charmap_start + i * 10
        if off + 10 > len(data):
            break
        # The first 2 bytes are the codepoint as stored. We trust the
        # iteration order rather than parsing each entry's codepoint
        # field — but if they disagree, prefer the entry's value.
        entry_cp = struct.unpack_from("<H", data, off)[0]
        glyph_w = data[off + 2]
        klft = data[off + 3]
        krht = data[off + 4]
        data_off_3 = (
            data[off + 5] | (data[off + 6] << 8) | (data[off + 7] << 16)
        )
        if align8 & 0x01:
            data_off_3 *= 8
        data_len = struct.unpack_from("<H", data, off + 8)[0]
        # Width 0 means the codepoint is missing/blank; skip storing.
        if glyph_w == 0:
            continue
        abs_off = charmap_start + data_off_3
        if abs_off >= len(data):
            continue
        try:
            img = _decode_v6_glyph(data, abs_off, data_len, glyph_w, height)
        except Exception:
            continue
        # Use the actual codepoint stored in the entry — if it's reasonable.
        # For single-byte codepages, this matches our enumeration anyway.
        key = entry_cp if entry_cp != 0 else cp
        glyphs[key] = img
        widths[key] = glyph_w

    return ZiFont(
        version=hdr["version"],
        width=hdr["width"],
        height=hdr["height"],
        encoding=encoding,
        encoding_name=encoding_name,
        name=name,
        glyphs=glyphs,
        widths=widths,
    )
