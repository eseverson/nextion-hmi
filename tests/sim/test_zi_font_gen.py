"""From-scratch DEVICE-VALID ``.zi`` font generation — me/miata-dash#39.

The refined #39 goal is a ``.zi`` the DISPLAY renders correctly, generated
from a TTF + pixel height + charset on Linux.  Byte-matching the Nextion
Editor's rasterizer is explicitly NOT the goal, so these tests assert:

  1. STRUCTURE/METRICS match the editor's reference font byte-for-byte
     where reproducible — header (every field bar the payload-size, which
     legitimately tracks the differing glyph bytes), codepage ranges, and
     the char-map codepoints.  The per-glyph ADVANCE WIDTH is NOT a
     reproducible function of the TTF (documented + measured here).
  2. SELF-CONSISTENCY — ``describe_zi_font(build_zi_font(...))`` round-trips,
     glyph streams are valid 3-bit AA the renderer's decoder reads back
     exactly, and offsets/lengths are internally consistent.
  3. The generated font, spliced into a TFT and recompiled, loads through
     the renderer's TFT loader (device-valid SHAPE).  Final on-device pixel
     correctness is a HARDWARE check (the flashable artifact is built by
     ``scripts/tools/build_font_proof.py``); it cannot be proven headlessly,
     by design.

Oracle fixture: the in-repo ``source/nextion.hmi.tft`` (2 fonts:
``liberiso-8859-1`` @ 24px, ``liber-48iso-8859-1`` @ 48px, both v6 AA).
Requires freetype-py + the Liberation Serif TTF; skips cleanly otherwise.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.lib import tft_format, tft_attrs  # noqa: E402
from scripts.lib.resources_generator import (  # noqa: E402
    build_zi_font, describe_zi_font, replace_fonts_in_resources_source,
    FontSwap, FONT_GEN_FEASIBLE, FONT_METRIC_REPRODUCIBLE,
    FONT_RASTER_FEASIBLE, _alpha8_to_level, _encode_aa_glyph,
    _charset_to_codepoints,
)

SRC_TFT = REPO_ROOT / "source" / "nextion.hmi.tft"
SRC_HMI = REPO_ROOT / "source" / "nextion.hmi.HMI"
TTF = Path("/usr/share/fonts/liberation-serif-fonts/"
           "LiberationSerif-Regular.ttf")


def _need_ttf():
    pytest.importorskip("freetype")
    if not TTF.exists():
        pytest.skip("Liberation Serif TTF not installed")
    if not SRC_TFT.exists():
        pytest.skip("source TFT fixture missing")


def _appinf1(raw: bytes) -> dict:
    return tft_attrs.parse_appinf1_corrected(tft_format.parse(raw).appinf1)


def _extract_standalone_zi(raw: bytes, font_idx: int) -> bytes:
    """Same splice the renderer's extract_zi_fonts uses — reconstruct a
    standalone .zi for font ``font_idx`` from the TFT."""
    a = _appinf1(raw)
    zim = a["zimoxinxiadd"]
    qty = a["zimoqyt"]
    strd = a["strdataaddr"]
    starts = [struct.unpack_from("<I", raw, zim + n * 44 + 24)[0]
              for n in range(qty)]
    starts.append(strd - zim)
    hdr_off = zim + font_idx * 44
    name_off = zim + starts[font_idx]
    next_off = zim + starts[font_idx + 1]
    ne = name_off
    while ne < next_off and ne - name_off < 64 and 32 <= raw[ne] < 127:
        ne += 1
    nb = bytes(raw[name_off:ne]).rstrip(b" ")
    gd = raw[name_off + len(nb):next_off]
    header = bytearray(raw[hdr_off:hdr_off + 44])
    struct.pack_into("<I", header, 24, 44)
    header[17] = len(nb)
    return bytes(header) + nb + gd


@pytest.fixture(scope="module")
def src_tft() -> bytes:
    if not SRC_TFT.exists():
        pytest.skip("source TFT fixture missing")
    return SRC_TFT.read_bytes()


@pytest.fixture(scope="module")
def gen_font24() -> bytes:
    _need_ttf()
    # Generate with the editor's own name so name-len + name bytes line up
    # for the byte-exact header structural comparison.
    return build_zi_font(str(TTF), 24, "latin-1", name="liberiso-8859-1")


# ════════════════════════════════════════════════════════════════════════
# Capability flags.
# ════════════════════════════════════════════════════════════════════════

def test_font_gen_feasible_flag():
    """Device-valid font generation IS feasible (distinct from the
    editor-byte-match flag, which stays False)."""
    assert FONT_GEN_FEASIBLE is True
    assert FONT_RASTER_FEASIBLE is False   # byte-matching the editor: still no


def test_font_metric_reproducible_flag_is_false():
    """The editor's per-glyph advance width is NOT reproducible from the
    TTF — flagged so callers know layout diverges from the editor."""
    assert FONT_METRIC_REPRODUCIBLE is False


# ════════════════════════════════════════════════════════════════════════
# (1) Structure / metrics match the editor's reference font.
# ════════════════════════════════════════════════════════════════════════

def test_header_structure_byte_exact_vs_reference(src_tft, gen_font24):
    """The generated 44-byte header equals the editor's reference header
    EXCEPT the payload-size field (+0x14, u32), which legitimately tracks
    the differing glyph-pixel bytes.  Every structural field — signature,
    magic tail, encoding, mb_mode, cell width, height, codepage ranges,
    glyph_count, version, name-length, data_start, the AA/var-width flags
    and tail constants — is byte-identical."""
    ref_hdr = _extract_standalone_zi(src_tft, 0)[:44]
    gen_hdr = gen_font24[:44]
    diffs = [i for i in range(44) if ref_hdr[i] != gen_hdr[i]]
    # Only the u32 payload-size field (bytes 20..23) may differ.
    assert set(diffs) <= {20, 21, 22, 23}, (
        f"unexpected header diffs at {diffs} — structure must match")
    # And those are exactly the payload-size field difference.
    ref_payload = struct.unpack_from("<I", ref_hdr, 20)[0]
    gen_payload = struct.unpack_from("<I", gen_hdr, 20)[0]
    assert gen_payload == len(gen_font24) - 44
    assert ref_payload == len(_extract_standalone_zi(src_tft, 0)) - 44
    # Everything outside the payload-size field is byte-identical.
    assert ref_hdr[:20] == gen_hdr[:20]
    assert ref_hdr[24:] == gen_hdr[24:]


def test_codepage_and_name_match_reference(src_tft, gen_font24):
    ref = describe_zi_font(_extract_standalone_zi(src_tft, 0))
    gen = describe_zi_font(gen_font24)
    assert gen.version == ref.version == 6
    assert gen.height == ref.height == 24
    assert gen.width == ref.width == 0           # variable-width
    assert gen.encoding == ref.encoding
    assert gen.name == ref.name == "liberiso-8859-1"
    assert (gen.cp_first_start, gen.cp_first_end) == \
           (ref.cp_first_start, ref.cp_first_end) == (0, 0)
    assert (gen.cp_second_start, gen.cp_second_end) == \
           (ref.cp_second_start, ref.cp_second_end) == (32, 255)
    assert gen.glyph_count == ref.glyph_count == 224


def test_charmap_codepoints_match_reference(src_tft, gen_font24):
    """Every char-map entry's codepoint (cp_low/cp_high) matches the
    reference for all 224 glyphs — the device's char lookup is identical."""
    ref = _extract_standalone_zi(src_tft, 0)
    from sim.font import _parse_header
    hr = _parse_header(ref)
    hg = _parse_header(gen_font24)
    cmr, cmg = hr["data_start"], hg["data_start"]
    for i in range(224):
        er = ref[cmr + i * 10:cmr + i * 10 + 2]
        eg = gen_font24[cmg + i * 10:cmg + i * 10 + 2]
        assert er == eg, f"glyph {i}: codepoint {eg.hex()} != ref {er.hex()}"


def test_advance_width_diverges_and_is_documented(src_tft, gen_font24):
    """MEASURED proof of FONT_METRIC_REPRODUCIBLE=False: the generated
    advance widths do NOT byte-match the editor's for most glyphs (the
    editor's width logic is not a reproducible function of the TTF), yet
    every generated width is sensible (>0 for inked glyphs, contains the
    glyph's own ink).  This is the one layout-affecting divergence — stated
    precisely, not papered over."""
    from sim.font import _parse_header, _codepoints_for
    ref = _extract_standalone_zi(src_tft, 0)
    hr = _parse_header(ref)
    hg = _parse_header(gen_font24)
    cmr, cmg = hr["data_start"], hg["data_start"]
    cps = _codepoints_for(hr)
    match = total = 0
    for i, cp in enumerate(cps):
        rw = ref[cmr + i * 10 + 2]
        gw = gen_font24[cmg + i * 10 + 2]
        total += 1
        if rw == gw:
            match += 1
        # Inked ASCII glyphs must have a positive width.
        if 33 <= cp < 127:
            assert gw > 0, f"glyph {cp!r} has zero width"
    frac = match / total
    # Some widths coincide, but most diverge — that's the documented gap.
    assert frac < 0.95, (
        f"advance widths matched {frac:.2f} — unexpectedly reproducible; "
        "re-evaluate FONT_METRIC_REPRODUCIBLE")


# ════════════════════════════════════════════════════════════════════════
# (2) Self-consistency.
# ════════════════════════════════════════════════════════════════════════

def test_describe_round_trips(gen_font24):
    """describe_zi_font on the generated bytes returns a coherent
    descriptor with the right counts/modes."""
    d = describe_zi_font(gen_font24)
    assert d.glyph_count == 224
    assert len(d.metrics) == 224
    # Shaped glyphs are AA (mode 0x03); blanks (space) have no stream.
    assert d.aa_glyphs > 100
    by_cp = {m.codepoint: m for m in d.metrics}
    assert by_cp[ord("A")].mode == 0x03
    assert by_cp[ord(" ")].mode is None        # space: empty stream


def test_internal_offsets_consistent(gen_font24):
    """Header payload-size == file − 44, data_start == 44, and each glyph's
    (data_offset, data_length) stays inside the payload and the streams are
    contiguous starting right after the char map."""
    from sim.font import _parse_header
    h = _parse_header(gen_font24)
    assert h["data_start"] == 44 + h["name_len"]
    payload = struct.unpack_from("<I", gen_font24, 20)[0]
    assert payload == len(gen_font24) - 44
    cm = h["data_start"]
    nchars = h["nchars"]
    glyph_region = cm + nchars * 10
    cursor = nchars * 10            # first glyph offset (rel to cm)
    for i in range(nchars):
        e = gen_font24[cm + i * 10:cm + i * 10 + 10]
        doff = e[5] | (e[6] << 8) | (e[7] << 16)
        dlen = struct.unpack_from("<H", e, 8)[0]
        if dlen:
            assert doff == cursor, f"glyph {i}: non-contiguous offset"
            assert cm + doff + dlen <= len(gen_font24)
            cursor += dlen
    assert cm + cursor == len(gen_font24)
    assert glyph_region <= len(gen_font24)


def test_glyph_streams_are_valid_3bit_aa(gen_font24):
    """Each non-empty glyph stream starts with mode 0x03 and decodes (via
    the renderer's own decoder) to a height×width image — i.e. it is valid
    3-bit AA of the right length, even though the bytes differ from the
    editor's."""
    from sim.font import _parse_header, _codepoints_for, _decode_v6_glyph
    import numpy as np
    h = _parse_header(gen_font24)
    cm = h["data_start"]
    H = h["height"]
    cps = _codepoints_for(h)
    checked = 0
    for i, cp in enumerate(cps):
        e = gen_font24[cm + i * 10:cm + i * 10 + 10]
        gw = e[2]
        doff = e[5] | (e[6] << 8) | (e[7] << 16)
        dlen = struct.unpack_from("<H", e, 8)[0]
        if dlen == 0 or gw == 0:
            continue
        assert gen_font24[cm + doff] == 0x03, f"glyph {cp!r} not AA mode"
        img = _decode_v6_glyph(gen_font24, cm + doff, dlen, gw, H)
        arr = np.array(img)
        assert arr.shape == (H, gw)
        checked += 1
    assert checked > 100


def test_encode_decode_round_trip_exact():
    """The AA encoder is the exact inverse of the renderer's AA decoder:
    a quantized level grid re-encodes and re-decodes to identical levels."""
    from sim.font import _decode_v6_glyph
    import numpy as np
    # A grid exercising every level 0..7 in runs and singles.
    H, W = 6, 8
    levels = [
        0, 0, 7, 7, 7, 3, 3, 0,
        1, 2, 3, 4, 5, 6, 7, 0,
        0, 7, 0, 7, 0, 7, 0, 7,
        4, 4, 4, 4, 4, 4, 4, 4,
        0, 1, 0, 2, 0, 3, 0, 0,
        7, 7, 7, 7, 7, 7, 7, 7,
    ]
    stream = _encode_aa_glyph(levels)
    assert stream[0] == 0x03
    img = _decode_v6_glyph(stream, 0, len(stream), W, H)
    out = [_alpha8_to_level(v) for v in np.array(img).flatten()]
    assert out == levels


def test_alpha_inverse_is_exact():
    """_alpha8_to_level inverts sim.font._alpha3 for all 8 levels."""
    from sim.font import _alpha3
    for lvl in range(8):
        assert _alpha8_to_level(_alpha3(lvl)) == lvl


def test_charset_resolution():
    assert _charset_to_codepoints("latin-1")[:4] == (0, 0, 32, 255)
    fs, fe, ss, se, cps = _charset_to_codepoints((48, 57))   # digits
    assert (fs, fe, ss, se) == (0, 0, 48, 57)
    assert cps == list(range(48, 58))
    # A literal string spans min..max of its chars.
    _, _, ss2, se2, _ = _charset_to_codepoints("AZ")
    assert (ss2, se2) == (ord("A"), ord("Z"))
    with pytest.raises(ValueError):
        _charset_to_codepoints("€")                          # >255


def test_custom_charset_and_height():
    """A digits-only 32px font generates with exactly the requested
    coverage and height."""
    _need_ttf()
    zi = build_zi_font(str(TTF), 32, (48, 57))
    d = describe_zi_font(zi)
    assert d.height == 32
    assert d.glyph_count == 10
    assert (d.cp_second_start, d.cp_second_end) == (48, 57)
    assert {m.codepoint for m in d.metrics} == set(range(48, 58))


# ════════════════════════════════════════════════════════════════════════
# (3) Device-valid SHAPE: splice into a TFT, recompile, load via renderer.
# ════════════════════════════════════════════════════════════════════════

def test_generated_font_round_trips_through_tft(src_tft):
    """Splice a generated font into the source TFT's font region, recompile
    a sealed TFT, and confirm the renderer's extractor reads the generated
    font back with all 224 glyphs — the device-valid-shape proof.  (Final
    on-device pixel correctness is a HARDWARE check; see
    scripts/tools/build_font_proof.py.)"""
    _need_ttf()
    import tempfile
    import os
    from scripts.lib.compile_hmi import compile_hmi
    from scripts.lib.tft_format import extract_zi_fonts, parse

    patched = replace_fonts_in_resources_source(
        src_tft, [FontSwap(index=0, ttf_path=str(TTF))])
    # Region size (and thus strdataaddr) is preserved.
    assert len(patched) == len(src_tft)

    tmp = tempfile.NamedTemporaryFile(suffix=".tft", delete=False)
    try:
        tmp.write(patched)
        tmp.close()
        out = compile_hmi(SRC_HMI, tmp.name, generate_resources=False)
    finally:
        os.unlink(tmp.name)

    # The sealed TFT parses (CRCs valid by construction of compile_hmi).
    parse(out)
    fonts = extract_zi_fonts(out)
    assert set(fonts) == {0, 1}
    f0, f1 = fonts[0], fonts[1]
    assert f0.height == 24 and f0.name == "liberiso-8859-1"
    assert len(f0._glyphs) == 224
    assert f0.has_glyph(ord("A")) and f0.glyph_width(ord("A")) > 0
    # Font 1 is untouched.
    assert f1.height == 48 and f1.name == "liber-48iso-8859-1"


def test_region_overflow_raises(src_tft):
    """A charset/height that would overflow the fixed font-region budget
    raises rather than silently truncating — protecting the device."""
    _need_ttf()
    # A 128px full-latin font's two-font region overflows the source TFT's
    # 95827-byte budget (the source's fonts are 24/48px).
    with pytest.raises(ValueError, match="exceeds fixed budget"):
        replace_fonts_in_resources_source(
            src_tft,
            [FontSwap(index=0, ttf_path=str(TTF), pixel_height=128),
             FontSwap(index=1, ttf_path=str(TTF), pixel_height=128)])
