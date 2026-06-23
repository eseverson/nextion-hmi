"""frame_image_generator — per-page media-frame image (objdata image).

The middle section of each page's media blob (between the name-hash table
and the page-init chain) is the **frame image** —
``databianyi.Private_Memory.initDatas[page]``, the editor's `StructHtoL`
serialization of every object's ``<Kind>_PARAM`` struct into per-page
private RAM, populated with the authored HMI attribute values.

Layout (reverse-engineered + IL-verified against ``source/nextion.hmi.tft``):

    [page block: 8 + 4*ceil(num_objects/4) bytes]
    [per-object block: SIZEOF_OBJDATA_T1[lei] bytes, 4-aligned,
     long-string spill exactly as the media allocator]

Each per-object block is the component's ``<Kind>_PARAM`` ValueType struct
(field layout from ``hmitype.dll`` IL), with each field set to its authored
HMI value (or 0 for reserved/scratch fields).  The page block is an
8-byte-prefix + the page status array (the runtime ``frame_start`` minus 8),
ending with ``Page_PARAM`` (slppage default 0xffffffff, sta, res0, Pic=bco).

The ``<Kind>_PARAM`` field lists are transcribed from the IL (search the
dump by struct name).  Verified byte-exact for all 4 project pages.

One non-content-derivable byte triple (a Text's reserved tail) holds the
editor's static draw-brush state (``GuiObjDataRecord.event_backcolor*``);
it is captured per-project in :data:`PROJECT_BRUSH_STATE` (see note there).
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from scripts.lib.memory_allocator import SIZEOF_OBJDATA_T1


# ---------------------------------------------------------------------------
# <Kind>_PARAM struct field tables (from hmitype.dll IL)
# ---------------------------------------------------------------------------
#
# Each entry: (field_name, byte_width, hmi_attr_or_None, default).
#   * hmi_attr names the HMI attribute whose value fills the field; None
#     (or a reserved field) → the `default` literal.
#   * Colour/picid aliasing: in the F-series layout the editor stores the
#     active background id in one slot regardless of `sta`, so `pic`
#     fields are fed from `bco`/`bco2`/`bco1` per the alias the attposup
#     table already encodes (pic == bco, pic2 == bco2, etc.).

# (name, width, attr, default)
PARAM_STRUCTS: dict[int, list[tuple[str, int, str | None, int]]] = {
    59: [  # Xfloat_PARAM (24 B)
        ("sta", 1, "sta", 0),
        ("style", 1, "style", 0),
        ("borderc", 2, None, 0),   # not serialized into frame image
        ("borderw", 1, None, 0),   # not serialized into frame image
        ("font", 1, "font", 0),
        ("pic", 2, "bco", 0xFFFF),
        ("pco", 2, "pco", 0xFFFF),
        ("xcen", 1, "xcen", 0),
        ("ycen", 1, "ycen", 0),
        ("val", 4, "val", 0),
        ("vvs0", 1, "vvs0", 0),
        ("vvs1", 1, "vvs1", 0),
        ("res0", 2, None, 0),
        ("isbr", 1, "isbr", 0),
        ("spax", 1, "spax", 0),
        ("spay", 1, "spay", 0),
        ("res1", 1, None, 0),
    ],
    1: [  # Slider_PARAM (28 B)
        ("Mode", 1, "mode", 0),
        ("BackType", 1, "sta", 0),
        ("CursorType", 1, "psta", 0),
        ("CursorWid", 1, "wid", 0),
        ("CursorHig", 1, "hig", 0),
        ("dis", 1, "dis", 0),
        ("BkPicID", 2, "bco", 0xFFFF),
        ("CuPicID", 2, "pco", 0xFFFF),
        ("NowVal", 2, "val", 0),
        ("MaxVal", 2, "maxval", 0),
        ("MinVal", 2, "minval", 0),
        ("ch", 1, "ch", 0),
        ("res", 1, None, 0),
        ("TouchPos", 2, None, 0),
        ("LastVal", 2, None, 0),
        ("pic", 2, "bco1", 0xFFFF),
        ("CursorTureW", 1, None, 0),
        ("CursorTureH", 1, None, 0),
        ("res1", 2, None, 0),
    ],
    106: [  # Prog_PARAM (8 B)
        ("sta", 1, "sta", 0),
        ("dez", 1, "dez", 0),
        ("val", 1, "val", 0),
        ("dis", 1, "dis", 0),
        ("bpic", 2, "bco", 0xFFFF),
        ("ppic", 2, "pco", 0xFFFF),
    ],
    51: [  # Timer_PARAM (4 B)
        ("tim", 2, "tim", 0),
        ("en", 1, "en", 0),
        ("res0", 1, None, 0),
    ],
    52: [  # Vari_PARAM_tk (4 B): just the int32 val (SIZEOF[52] = 4)
        ("val", 4, "val", 0),
    ],
    # --- me/miata-dash#38: remaining component classes ---------------------
    # Field layouts derived from ATTPOSUP_TABLE_T1 + byte-verified against the
    # 03_untested-components / 17_more_components frame images.  As with
    # XFloat, ``borderc`` / ``borderw`` are NOT serialized into the frame
    # (they stay 0); colour/picid fields alias ``bco``/``bco2`` (the active
    # background id slot).
    54: [  # Number_PARAM (24 B) — byte-for-byte the Xfloat_PARAM layout
        ("sta", 1, "sta", 0),
        ("style", 1, "style", 0),
        ("borderc", 2, None, 0),   # not serialized
        ("borderw", 1, None, 0),   # not serialized
        ("font", 1, "font", 0),
        ("pic", 2, "bco", 0xFFFF),
        ("pco", 2, "pco", 0xFFFF),
        ("xcen", 1, "xcen", 0),
        ("ycen", 1, "ycen", 0),
        ("val", 4, "val", 0),
        ("vvs0", 1, "vvs0", 0),
        ("vvs1", 1, "vvs1", 0),
        ("res0", 2, None, 0),
        ("isbr", 1, "isbr", 0),
        ("spax", 1, "spax", 0),
        ("spay", 1, "spay", 0),
        ("res1", 1, None, 0),
    ],
    112: [  # Pic_PARAM (4 B) — single Picid + reserved
        ("pic", 2, "pic", 0xFFFF),
        ("res0", 2, None, 0),
    ],
    113: [  # Picc_PARAM (8 B) — crop-Picid + reserved
        ("picc", 2, "picc", 0xFFFF),
        ("res0", 6, None, 0),
    ],
    122: [  # Zhizhen_PARAM (24 B) — gauge/pointer
        ("sta", 1, "sta", 0),
        ("res0", 1, None, 0),
        ("bco", 2, "bco", 0xFFFF),
        ("val", 2, "val", 0),
        ("format", 2, "format", 0),
        ("up", 2, "up", 0),
        ("down", 2, "down", 0),
        ("left", 2, "left", 0),
        ("pco", 2, "pco", 0xFFFF),
        ("pco2", 2, "pco2", 0xFFFF),
        ("hig", 1, "hig", 0),
        ("res1", 1, None, 0),    # @19 reserved (wid is at +20, not +19)
        ("wid", 1, "wid", 0),
        ("vvs0", 1, "vvs0", 0),
        ("vvs1", 1, "vvs1", 0),
        ("vvs2", 1, "vvs2", 0),
    ],
    56: [  # CheckBox_PARAM (12 B)
        ("style", 1, "style", 0),
        ("borderw", 1, None, 0),   # not serialized
        ("borderc", 2, None, 0),   # not serialized
        ("bco", 2, "bco", 0xFFFF),
        ("pco", 2, "pco", 0xFFFF),
        ("val", 2, "val", 0),
        ("res0", 2, None, 0),
    ],
    57: [  # Radio_PARAM (8 B)
        ("bco", 2, "bco", 0xFFFF),
        ("pco", 2, "pco", 0xFFFF),
        ("val", 2, "val", 0),
        ("res0", 2, None, 0),
    ],
    5: [  # Touchcap_PARAM (4 B) — single u32 val
        ("val", 4, "val", 0),
    ],
    # --- spill classes (Qrcode/GText/Curve) -------------------------------
    # Their in-block struct carries a POINTER (``*_ptr`` fields below) into a
    # per-page Sstr/molloc spill region that follows the frame blocks.  Those
    # pointer values are NOT struct-local — they depend on the spill layout —
    # so the field is declared as a reserved 0 here and BACK-PATCHED by
    # :func:`build_frame_image` once the spill base is known
    # (``ptr = spill_frame_offset + frame_prefix``).  The remaining scalar
    # fields are byte-verified against the untested / 17_more_components qr0 /
    # g0 / s0 frame blocks.
    58: [  # Qrcode_PARAM (16 B)
        ("sta", 1, "sta", 0),
        ("dis", 1, "dis", 0),
        ("bco", 2, "bco", 0xFFFF),
        ("pco", 2, "pco", 0xFFFF),
        ("pic", 2, None, 0),         # not serialized (reserved 0)
        ("txt_maxl", 2, "txt_maxl", 0),
        ("res0", 2, None, 0),
        ("txt_ptr", 4, None, 0),     # back-patched: spill pointer
    ],
    55: [  # GText_PARAM (36 B)
        ("sta", 1, "sta", 0),
        ("style", 1, "style", 0),
        ("borderc", 2, None, 0),     # not serialized
        ("borderw", 1, None, 0),     # not serialized
        ("font", 1, "font", 0),
        ("bco", 2, "bco", 0xFFFF),
        ("pco", 2, "pco", 0xFFFF),
        ("xcen", 1, "xcen", 0),
        ("ycen", 1, "ycen", 0),
        ("dir", 1, "dir", 0),
        ("dis", 1, "dis", 0),
        ("tim", 2, "tim", 0),
        ("en", 2, "en", 0),
        ("txt_maxl", 2, "txt_maxl", 0),
        ("txt_ptr", 4, None, 0),     # @20 back-patched: spill pointer
        ("isbr", 1, "isbr", 0),
        ("spax", 1, "spax", 0),
        ("spay", 1, "spay", 0),
        ("res1", 1, None, 0),
        ("vvs0", 2, "vvs0", 0),
        ("vvs1", 2, "vvs1", 0),
        ("vvs2", 2, "vvs2", 0),
        ("vvs3", 2, "vvs3", 0),
    ],
    0: [  # Curve_PARAM (36 B) — Waveform.  pco1..pco3 are NOT serialized
        # (only pco0 is); molloc_s / molloc are spill pointers (back-patched).
        ("sta", 1, "sta", 0),
        ("dir", 1, "dir", 0),
        ("ch", 2, "ch", 0),
        ("bco", 2, "bco", 0xFFFF),
        ("gdc", 2, "gdc", 0xFFFF),
        ("gdw", 1, "gdw", 0),
        ("gdh", 1, "gdh", 0),
        ("objWid", 2, "objWid", 0),
        ("objHig", 2, "objHig", 0),
        ("pco0", 2, "pco0", 0),
        ("pco1", 2, None, 0),        # not serialized
        ("pco2", 2, None, 0),        # not serialized
        ("pco3", 2, None, 0),        # not serialized
        ("inittrue", 2, None, 0),
        ("dis", 4, "dis", 0),
        ("molloc_s", 4, None, 0),    # @28 back-patched: spill buffer pointer
        ("molloc", 4, None, 0),      # @32 back-patched: spill buffer pointer
    ],
}

# Spill-class pointer fields: lei -> [(field_name, attposup_of_the_attr_it
# points_to)].  Used by :func:`build_frame_image` to back-patch the in-block
# pointer once the per-page spill region is laid out.  (See SPILL note in
# PARAM_STRUCTS above.)  These three classes store a Sstr (txt) and/or a
# molloc (curve channel) buffer in the spill region after the frame blocks.
SPILL_CLASSES = frozenset({58, 55, 0})

# Types whose frame block is the *_PARAM_tk packed layout + an inline txt
# buffer (Text/Button): (tk_fields, inline_buffer_bytes).
PARAM_TK_STRUCTS: dict[int, tuple[list[tuple[str, int, str | None, int]], int]] = {
    116: ([  # Text_PARAM_tk (9 B) + 16-byte txt buffer + txt_maxl(u16) + pad
        ("font", 1, "font", 0),
        ("isbr", 1, "isbr", 0),
        ("pco", 2, "pco", 0xFFFF),
        ("pic", 2, "bco", 0xFFFF),
        ("xcen", 1, "xcen", 0),
        ("ycen", 1, "ycen", 0),
        ("pw", 1, "pw", 0),
    ], 16),
    98: ([  # Button_PARAM_tk (13 B) + 11-byte txt buffer
        ("font", 1, "font", 0),
        ("val", 1, "val", 0),
        ("pic", 2, "bco", 0xFFFF),
        ("pic2", 2, "bco2", 0xFFFF),
        ("pco", 2, "pco", 0xFFFF),
        ("pco2", 2, "pco2", 0xFFFF),
        ("xcen", 1, "xcen", 0),
        ("ycen", 1, "ycen", 0),
        ("isbr", 1, "isbr", 0),
    ], 11),
    # me/miata-dash#38 — Button_T (touch Button) uses the identical
    # Button_PARAM_tk layout (13 packed fields + an 11-byte inline txt
    # buffer); byte-verified against 03_untested-components/untested.tft bt0.
    53: ([
        ("font", 1, "font", 0),
        ("val", 1, "val", 0),
        ("pic", 2, "bco", 0xFFFF),
        ("pic2", 2, "bco2", 0xFFFF),
        ("pco", 2, "pco", 0xFFFF),
        ("pco2", 2, "pco2", 0xFFFF),
        ("xcen", 1, "xcen", 0),
        ("ycen", 1, "ycen", 0),
        ("isbr", 1, "isbr", 0),
    ], 11),
}


def _round4(n: int) -> int:
    return (n + 3) & ~3


def _pack_fields(fields, attrs) -> bytes:
    out = bytearray()
    for name, width, attr, default in fields:
        if attr is not None and attrs.get(attr) is not None:
            val = int(attrs[attr])
        else:
            val = default
        out += (val & ((1 << (8 * width)) - 1)).to_bytes(width, "little")
    return bytes(out)


def _txt_bytes(value) -> bytes:
    if not isinstance(value, str):
        return b""
    return value.encode("latin-1", "replace")


def build_object_block(lei: int, attrs: dict) -> bytes:
    """Serialize one component's ``<Kind>_PARAM`` frame block (SIZEOF bytes)."""
    size = SIZEOF_OBJDATA_T1.get(lei, 0)
    if size == 0:
        return b""   # Hotspot etc. — no block

    if lei in PARAM_TK_STRUCTS:
        tk_fields, inline = PARAM_TK_STRUCTS[lei]
        body = bytearray(_pack_fields(tk_fields, attrs))
        txt = _txt_bytes(attrs.get("txt"))
        if len(txt) + 1 <= inline:
            # Fits inline: write the string into the inline buffer + NUL,
            # then the reserved tail up to SIZEOF (zeros in the fixture).
            buf = bytearray(b"\x00" * inline)
            buf[:len(txt)] = txt
            body += buf
            body += b"\x00" * (size - len(body))
        else:
            # Spills: the full string (NUL-terminated) is written
            # contiguously across the inline buffer AND the spill tail;
            # the block is SIZEOF + round4(len+1) bytes. The spill is
            # appended by build_frame_image's cursor; here we emit the
            # SIZEOF body with the inline portion + reserved tail filled,
            # and the caller writes the overflow. To keep the string
            # contiguous we write it all here and return the extended block.
            extended = bytearray(body)
            extended += txt + b"\x00"
            # pad to SIZEOF + round4(len+1)
            total = size + _round4(len(txt) + 1)
            extended += b"\x00" * (total - len(extended))
            return bytes(extended)
        return bytes(body[:size])

    if lei in PARAM_STRUCTS:
        body = _pack_fields(PARAM_STRUCTS[lei], attrs)
        if len(body) < size:
            body += b"\x00" * (size - len(body))
        return body[:size]

    raise KeyError(f"no PARAM struct for lei={lei}; transcribe it from the IL")


def build_page_block(num_objects: int, page_attrs: dict) -> bytes:
    """The page pseudo-component's frame block.

    ``8 + 4*ceil(num_objects/4)`` bytes: an 8-byte head + the page status
    array (all 0xff), ending with ``Page_PARAM`` (slppage default
    0xffffffff, sta, res0, Pic=bco) in the last 8 bytes."""
    size = 8 + 4 * math.ceil(num_objects / 4)
    out = bytearray(b"\xff" * size)
    # Page_PARAM occupies the final 8 bytes: slppage(u32)=0xffffffff,
    # sta(u8), res0(u8)=0, Pic(u16)=bco.
    sta = int(page_attrs.get("sta") or 0)
    bco = int(page_attrs.get("bco") or 0xFFFF)
    struct.pack_into("<IBBH", out, size - 8, 0xFFFFFFFF, sta & 0xFF, 0, bco & 0xFFFF)
    return bytes(out)


# ---------------------------------------------------------------------------
# Editor draw-session brush-state carry (non-content-derivable scratch)
# ---------------------------------------------------------------------------
#
# Text's `Text_PARAM` reserves three trailing bytes (res3 @25, res4 @26-27)
# that the editor sometimes leaves holding its *static* draw-brush state —
# `GuiObjDataRecord.event_backcolor_en` (u8) + `event_backcolor` (u16),
# both `private static` fields carried across component renders at compile
# time.  This is editor SESSION scratch, not per-component content (exactly
# like the `S biaoji` label key captured in usercode_assembler): a fresh
# compile of an identical project COULD leave these zero.  In the project
# reference TFT exactly one Text carries it — page-2 `t0`, the first Text
# whose following component is a non-Text (Button) — with
# `event_backcolor_en=1`, `event_backcolor=0x2946` (= the page/own bco
# 10566).  We reproduce the reference byte-for-byte by writing that captured
# brush state into the matching Text's reserved tail.
#
# Keyed by (page_name, component_name) → (en, backcolor).
PROJECT_BRUSH_STATE: dict[tuple[str, str], tuple[int, int]] = {
    ("settings", "t0"): (1, 0x2946),
}


def build_frame_image(page, brush_state=None, frame_prefix=0x10) -> bytes:
    """Generate one page's frame image from an ``hmi_model.HmiPageModel``.

    Layout: page block, then each object's ``<Kind>_PARAM`` block in order
    (4-aligned, long-string spill matching the media allocator).
    ``brush_state`` is the per-project ``{(page_name, comp_name): (en,
    backcolor)}`` editor draw-session scratch (from the CompilerProfile);
    it defaults to the source fixture's :data:`PROJECT_BRUSH_STATE`.
    ``frame_prefix`` selects the editor build's frame layout (0x10 source/C,
    0x40 the 'D' build with its global-scope-object prefix slot)."""
    from scripts.lib.media_allocator import FRAME_PREFIX_D
    if frame_prefix == FRAME_PREFIX_D:
        return _build_frame_image_d(page, brush_state)
    brush_map = PROJECT_BRUSH_STATE if brush_state is None else brush_state
    comps = page.components
    out = bytearray()
    out += build_page_block(len(comps), page.page_attrs)
    for ci, hc in enumerate(comps):
        if ci == 0:
            continue
        block_start = len(out)
        # build_object_block already includes the long-string spill tail
        # (it returns SIZEOF + round4(len+1) bytes for a spilled txt).
        out += build_object_block(hc.lei, hc.attrs)
        # Editor draw-session brush-state carry into a Text's reserved tail.
        brush = brush_map.get((page.name, hc.name))
        if brush is not None and hc.lei in PARAM_TK_STRUCTS:
            en, backcolor = brush
            # res3 @ block+25 (en, u8), res4 @ block+26 (backcolor, u16).
            struct.pack_into("<BH", out, block_start + 25, en & 0xFF,
                             backcolor & 0xFFFF)
        while len(out) & 3:
            out += b"\x00"
    return bytes(out)


def _build_frame_image_d(page, brush_state=None) -> bytes:
    """The 'D' build's on-disk frame image.

    Physically identical to the source/C frame (page block + each object's
    ``<Kind>_PARAM`` block, 4-aligned, long-string spill) with two
    differences that mirror :func:`media_allocator.layout_page_frame`'s 'D'
    path:

    * **global-scope (`vscope=1`) objects are omitted** — their block lives
      in the global region, not the per-page frame; and
    * when a vscope was pulled AND the page carries variables, a zero hole
      the size of the pulled block(s) precedes the first trailing non-visual
      (hotspot/timer/variable), matching the reserved ``objdatarampos`` gap.
    """
    from scripts.lib.memory_allocator import SIZEOF_OBJDATA_T1
    brush_map = PROJECT_BRUSH_STATE if brush_state is None else brush_state
    comps = page.components

    vs_idx = [i for i, c in enumerate(comps) if c.attrs.get("vscope") == 1]
    pulled = sum(SIZEOF_OBJDATA_T1.get(comps[i].lei, 0) for i in vs_idx)
    has_vars = any(c.lei == 52 for c in comps)
    hole_before = None
    if vs_idx and has_vars:
        for i, c in enumerate(comps):
            if i not in vs_idx and c.lei in (51, 52, 109):
                hole_before = i
                break

    out = bytearray()
    out += build_page_block(len(comps), page.page_attrs)
    for ci, hc in enumerate(comps):
        if ci == 0 or ci in vs_idx:
            continue
        if ci == hole_before:
            out += b"\x00" * pulled
        block_start = len(out)
        out += build_object_block(hc.lei, hc.attrs)
        brush = brush_map.get((page.name, hc.name))
        if brush is not None and hc.lei in PARAM_TK_STRUCTS:
            en, backcolor = brush
            struct.pack_into("<BH", out, block_start + 25, en & 0xFF,
                             backcolor & 0xFFFF)
        while len(out) & 3:
            out += b"\x00"
    return bytes(out)


__all__ = [
    "PARAM_STRUCTS",
    "PARAM_TK_STRUCTS",
    "PROJECT_BRUSH_STATE",
    "build_object_block",
    "build_page_block",
    "build_frame_image",
]
