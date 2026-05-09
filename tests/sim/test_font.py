"""Unit tests for the ZI font parser.

The HMI in source/ ships two ZI v6 fonts:
    0.zi -> "liberiso-8859-1"   (small label font, ~24px tall)
    1.zi -> "liber-48iso-8859-1" (large digit font, ~48px tall)

We extract them via the loader and verify the parser returns sensible
glyphs for ASCII codepoints.
"""
from __future__ import annotations

from PIL import Image

from sim.font import ZiFont, parse_zi
from sim.loader import load_hmi


def _extract_zi_blob(hmi_path, prefix: str) -> bytes:
    """Pull a single .zi font blob out of the HMI by directory-entry name."""
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "tools" / "Nextion2Text"))
    from sim.loader import _ensure_ansi_codec
    _ensure_ansi_codec()
    from Nextion2Text import HMI

    hmi = HMI(str(hmi_path))
    for entry in hmi.header.content:
        if entry.name == prefix:
            return hmi.raw[entry.start:entry.start + entry.size]
    raise AssertionError(f"{prefix} not in HMI")


def test_parse_font0_basic_header(hmi_path):
    blob = _extract_zi_blob(hmi_path, "0.zi")
    font = parse_zi(blob)
    assert isinstance(font, ZiFont)
    assert font.height > 0
    assert font.encoding_name == "iso-8859-1"
    assert "liberiso" in font.name


def test_parse_font1_basic_header(hmi_path):
    blob = _extract_zi_blob(hmi_path, "1.zi")
    font = parse_zi(blob)
    assert font.height > font.height // 2  # sanity
    assert font.height >= 32  # large digit font is much taller than the label font


def test_glyph_image_for_capital_A_is_nonempty(hmi_path):
    """Both fonts should have a real 'A' glyph with at least some ink pixels."""
    for prefix in ("0.zi", "1.zi"):
        blob = _extract_zi_blob(hmi_path, prefix)
        font = parse_zi(blob)
        img = font.glyph_image(ord("A"))
        assert isinstance(img, Image.Image)
        assert img.mode == "L"
        # The image should be exactly (glyph_width, font.height).
        assert img.size[0] == font.glyph_width(ord("A"))
        assert img.size[1] == font.height
        # Some pixel should be lit.
        assert max(img.getdata()) > 0, f"A in {prefix} has no ink"


def test_glyph_image_for_space_is_blank(hmi_path):
    """Space should round-trip to a blank mask (no ink)."""
    blob = _extract_zi_blob(hmi_path, "0.zi")
    font = parse_zi(blob)
    img = font.glyph_image(ord(" "))
    assert max(img.getdata()) == 0


def test_glyph_widths_vary_across_letters(hmi_path):
    """Variable-width fonts should report different widths for 'i' vs 'M'."""
    blob = _extract_zi_blob(hmi_path, "0.zi")
    font = parse_zi(blob)
    wi = font.glyph_width(ord("i"))
    wM = font.glyph_width(ord("M"))
    assert wM > wi > 0


def test_loader_attaches_fonts_to_state(hmi_path):
    state = load_hmi(hmi_path)
    assert 0 in state.fonts
    assert 1 in state.fonts
    f0 = state.fonts[0]
    f1 = state.fonts[1]
    assert f0.height < f1.height
    assert f0.glyph_width(ord("A")) > 0


def test_missing_glyph_returns_blank(hmi_path):
    """Codepoints outside the font's range should give a blank mask."""
    blob = _extract_zi_blob(hmi_path, "0.zi")
    font = parse_zi(blob)
    # ISO-8859-1 covers up to 0xFF; ask for 0x100.
    img = font.glyph_image(0x0100)
    assert isinstance(img, Image.Image)
    assert img.mode == "L"
    assert max(img.getdata()) == 0


def test_v3_decoder_unit():
    """Synthetic v3 sanity test: the documented '!' example from the spec.

    8x16 monochrome '!': bytes 00*4, 60*6, 00, 60, 00*4, decoded MSB-first.
    """
    from sim.font import _decode_v3_glyph
    data = bytes([
        0x00, 0x00, 0x00, 0x00,
        0x60, 0x60, 0x60, 0x60, 0x60, 0x60,
        0x00,
        0x60,
        0x00, 0x00, 0x00, 0x00,
    ])
    img = _decode_v3_glyph(data, 0, 8, 16)
    assert img.size == (8, 16)
    px = img.load()
    # Row 4: 0x60 = 0110 0000 -> ink at columns 1, 2
    assert px[1, 4] == 255
    assert px[2, 4] == 255
    assert px[0, 4] == 0
    assert px[3, 4] == 0
    # Row 0: blank
    assert px[1, 0] == 0
