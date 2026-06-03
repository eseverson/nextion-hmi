"""memory_allocator.py — Nextion public-memory layout for component attributes.

The Nextion runtime keeps a flat "public memory" region per page.  The region
starts with 4 bytes per global variable (AppAllvasQty globals), followed by
per-component blocks allocated in the order the components appear on the page
(including the page pseudo-component at slot 0).

Two layout variants exist, controlled by the chip series'
``xiliexinxi.function_objdataraminmemory`` flag (1 = X-series with private
per-page memory, 0 = T-series/F-series with single shared public memory):

T0 (model_series=0, ``function_objdataraminmemory == 0`` in the editor IL
but interpreted as "non-F-series" path historically)
    The 16_loop test fixture path. Block size and attposup values are
    empirically derived (see ``ATTPOSUP_TABLE`` and ``SIZEOF_OBJDATA``).

T1 / F-series (NX*F* displays, ``function_objdataraminmemory == 0`` in the
editor IL — the "F-series" tag in ``findings/text-setbrush-variant.md`` and
``findings/h2-cipher.md`` refers to this path)
    Per-class block size = ``sizeof(<Kind>_PARAM_tk)`` (Button/Button_T/
    Text/Vari) or ``sizeof(<Kind>_PARAM)`` (most others) — **no
    ``PARAM_Head`` prefix in public memory**. Attposup values are read
    directly from the second branch of each class's ``GetAtts_WithNoHead``
    method, with ``loc0`` folded to 0 (the F-series resets it). Per-block
    cursor is aligned to 4 bytes after each allocation.

Empirical findings for T0 (model_series=0)
-------------------------------------------
Derived by inspecting the init-code bytecode in ``tests/editor outputs/_old/
16_loop/16.tft``. The init code touches every byte it initialises with a
local-var-ref (``01 LL LL LL LL``), so contiguous runs of references delimit
each component's allocated block.  Verified attribute positions against the
known OFFSETS dict (all checked byte-for-byte against the TFT):

XFloat (type 59)
    block size : 41 bytes
    bco  at    : +1  (Color, 2 bytes)
    val  at    : +5  (SS32,  4 bytes)
    pco  at    : +3  (Color, 2 bytes)  — derived from layout

Variable / Vari (type 52)
    block size : 11 bytes
    val  at    : +0  (SS32,  4 bytes)  — matches attrs-raw.txt second block

The page pseudo-component (type 56 in the page-record sense, or just the
implicit "page" object) allocates 39 bytes before any user component on page 0
of 16_loop.  This is not a hard constant — callers must pass the correct
``starting_offset`` for the page being allocated.

F-series (T1) — extracted from hmitype.dll IL
---------------------------------------------
``ATTPOSUP_TABLE_T1`` and ``SIZEOF_OBJDATA_T1`` come from parsing
``/tmp/hmitype_all.il`` (the disassembled hmitype.dll).  Each class's
``GetAtts_WithNoHead`` has two branches:

1. ``function_objdataraminmemory == 1`` (X-series / Intelligent / Edge):
   ``loc0 = sizeof(<Kind>_PARAM_Head)``; per-attribute attposup is
   ``loc0 + N`` (so the actual byte offset is ``56 + N`` for most types).

2. ``function_objdataraminmemory == 0`` (T-series and F-series — this is
   the **F-series path** the miata-dash NX4832F035 uses): ``loc0`` is
   reset to 0, then attposup = ``0 + N = N``.  Some attributes use
   sentinel values ``-1`` ("not allocated; baked into bytecode") or
   ``>= 65535`` ("stored in a separate region"); those are excluded from
   the per-class table here.

The F-series block size also differs: in branch 2, GetRamBytes returns
``sizeof(<Kind>_PARAM)`` (no head) for most classes, and
``sizeof(<Kind>_PARAM_tk)`` for the four classes that have a tk variant
(Button, Button_T, Text, Vari).  Hotspot (LEI 109) returns a zero-byte
array — it has no allocated public-memory block.

Usage
-----
::

    # Default T0 path (used by 16_loop self-test):
    alloc = MemoryAllocator(app_allvas_qty=4)
    alloc.cursor = 4 * 4 + 39   # globals + page pseudo-obj
    alloc.add_object(59, "x0")
    offset = alloc.frame_offset("x0", "bco")   # → 1 (T0 table)

    # F-series path (miata-dash):
    alloc = MemoryAllocator(app_allvas_qty=4, series="T1")
    alloc.cursor = 4 * 4
    alloc.add_object(121, "page0")   # Page block — 8 bytes
    alloc.add_object(59, "x9")       # Xfloat block — 24 bytes
    offset = alloc.frame_offset("x9", "val")   # → memorypos + 12
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


# ---------------------------------------------------------------------------
# SIZEOF_OBJDATA (T0 path)
# Public-memory block size (bytes) for each component type.
# Empirically derived from 16_loop/16.tft (T0/non-F-series path).
# ---------------------------------------------------------------------------

SIZEOF_OBJDATA: Dict[int, int] = {
    59: 41,   # XFloat  — 11-byte init block, 41-byte total stride (no alignment)
    52: 11,   # Variable/Vari — stride observed between 7 consecutive Vari objects
}


# ---------------------------------------------------------------------------
# SIZEOF_OBJDATA_T1 (F-series path)
# Block size = sizeof(<Kind>_PARAM_tk) for Button/Button_T/Text/Vari, else
# sizeof(<Kind>_PARAM). Hotspot (109) returns a zero-byte array.
#
# Sizes extracted from struct field-by-field with CLR sequential layout
# (natural alignment, pack=1 for *_tk variants, pack=8 otherwise).
# Computed by ``scripts/research/extract_param_sizes.py``.
#
# After each allocation the F-series allocator aligns the cursor to 4 bytes
# (see ``findings/memory-allocation.md`` § per-page object loop).
# ---------------------------------------------------------------------------

SIZEOF_OBJDATA_T1: Dict[int, int] = {
    0:   36,   # Curve (Waveform)        — Curve_PARAM
    1:   28,   # Slider                  — Slider_PARAM
    5:    4,   # Touchcap                — Touchcap_PARAM
    51:   4,   # Timer                   — Timer_PARAM
    52:   4,   # Vari (Variable)         — Vari_PARAM_tk (just `val` u32)
    53:  13,   # Button_T (touch Button) — Button_T_PARAM_tk (pack=1, 13 raw bytes)
    55:  36,   # GText (ScrollingText in attribute-records.md) — GText_PARAM
    56:  12,   # CheckBox                — CheckBox_PARAM
    57:   8,   # Radio                   — Radio_PARAM
    58:  16,   # Qrcode                  — Qrcode_PARAM
    59:  24,   # XFloat                  — Xfloat_PARAM
    98:  13,   # Button                  — Button_PARAM_tk (pack=1, 13 raw bytes)
    106:  8,   # Prog (ProgressBar)      — Prog_PARAM
    109:  0,   # Hotspot (Touch)         — empty array; no block allocated
    112:  4,   # Pic                     — Pic_PARAM (single Picid)
    113:  8,   # Picc (CropPicture)      — Picc_PARAM
    116:  9,   # Text                    — Text_PARAM_tk (pack=1, 9 raw bytes)
    121:  8,   # Page                    — Page_PARAM (4 navigation u8s + sta + Pic)
    122: 24,   # Zhizhen (Gauge)         — Zhizhen_PARAM
}


# ---------------------------------------------------------------------------
# PARAM_HEAD_SIZES
# Byte size of each ``<Kind>_PARAM_Head`` ValueType struct in hmitype.dll.
# This is the FIXED-portion prefix every component allocates in public
# memory before its declared attrs land. The frame-offset formula
# ``obj.memorypos + Upatt0.attposup`` needs this to position
# ``Upatt0.attposup`` correctly inside the per-component allocation.
#
# Almost every component has the same layout
# ``_PARAM_Head { objdata_Ram objram; objeffecttype objeffect; }``
# = objdata_Ram(52) + objeffecttype(4) = 56 bytes.
#
# objdata_Ram = 4×u8 + eventtyte(24) + i32 + 4×u8 + 8×i16 = 52
# objeffecttype = u8 + u8 + u16                            = 4
# eventtyte = 6 × u32                                       = 24
#
# Exceptions: Timer / Touchcap have no `objdata_Ram` (28 bytes).
# Vari has only 4 bytes (4 × u8 head).
#
# Recovered from /tmp/hmitype_all.il struct declarations:
#   .class nested public sequential ansi sealed beforefieldinit
#   <Kind>_PARAM_Head extends [mscorlib]System.ValueType
# (with GText_PARAM_Head / Printer3D_PARAM_Head top-level instead).
# No class-level size constants exist; sizes are summed from
# field widths assuming standard CLR `sequential` packing.
#
# Hotspot (LEI 109) reuses GuiObjTouch's Touch_PARAM_Head (56 bytes).
#
# These sizes only apply on the X-series path
# (``function_objdataraminmemory == 1``).  The F-series allocator does
# **not** prepend PARAM_Head to public-memory blocks — see
# ``SIZEOF_OBJDATA_T1`` above.
# ---------------------------------------------------------------------------

PARAM_HEAD_SIZES: Dict[str, int] = {
    "Page":     56,   # GuiObjPage
    "Pic":      56,   # GuiObjPic
    "Picc":     56,   # GuiObjPicc
    "Text":     56,   # GuiObjText
    "Button":   56,   # GuiObjButton
    "Button_T": 56,   # GuiObjButton_T
    "Prog":     56,   # GuiObjProg
    "CheckBox": 56,   # GuiObjCheckBox
    "Radio":    56,   # GuiObjRadio
    "Slider":   56,   # GuiObjSlider
    "Xfloat":   56,   # GuiObjXfloat
    "Qrcode":   56,   # GuiObjQrcode
    "Curve":    56,   # GuiObjCurve
    "Zhizhen":  56,   # GuiObjZhizhen (gauge)
    "GText":    56,   # GuiObjGText (top-level, not nested)
    "Touch":    56,   # GuiObjTouch — Hotspot (LEI 109)
    "Timer":    28,   # 4×u8 + eventtyte (no objdata_Ram)
    "Touchcap": 28,   # 4×u8 + eventtyte
    "Vari":      4,   # 4×u8 only — no objdata_Ram, no eventtyte
}


# ---------------------------------------------------------------------------
# ATTPOSUP_TABLE (T0 path)
# Per-type, per-attribute byte offset *within* the allocated block.
# "attpos" here means the distance from the component's memorypos to the
# first byte of the attribute's value in public memory.
#
# Derived empirically for T0 (model_series=0):
#   XFloat x0: memorypos=0x37, x0.bco=0x38 → bco_attpos=1
#                              x0.val=0x3c  → val_attpos=5
#   Vari bco:  memorypos=0x37b, bco.val=0x37b → val_attpos=0
# ---------------------------------------------------------------------------

ATTPOSUP_TABLE: Dict[int, Dict[str, int]] = {
    59: {   # XFloat
        "bco":  1,   # Color (2 bytes) at byte 1
        "picc": 1,   # Picid aliases bco
        "pic":  1,   # Picid aliases bco
        "pco":  3,   # Color (2 bytes) at byte 3
        "val":  5,   # SS32  (4 bytes) at byte 5
    },
    52: {   # Variable / Vari
        "val":  0,   # SS32 (4 bytes), first field
    },
}


# ---------------------------------------------------------------------------
# ATTPOSUP_TABLE_T1 (F-series path)
# Per-type, per-attribute byte offset within the allocated F-series block.
# Extracted from the second branch of each
# ``hmitype.GuiObj<Kind>.GetAtts_WithNoHead`` method in
# /tmp/hmitype_all.il, with ``loc0`` folded to 0 (since F-series resets
# the local).  Sentinel attposup values are EXCLUDED:
#   * ``-1``  → "not allocated; constant baked into bytecode"
#   * ``>= 65535`` → "stored in a separate memory region"
#     (typically Sstr/molloc indirection)
#
# UNVERIFIED against a byte-checked F-series fixture.  All entries below
# come from IL inspection alone.  The Slider entry (val=10) matches the
# previously-noted ``h0.memorypos + 10 = 0x454`` reference in
# ``findings/memory-allocation.md`` § "Worked example — project page 0";
# the rest are IL-only.
# ---------------------------------------------------------------------------

ATTPOSUP_TABLE_T1: Dict[int, Dict[str, int]] = {
    0: {   # Curve (Waveform) — GuiObjCurve
        "sta":      0,
        "dir":      1,
        "ch":       2,
        "bco":      4, "picc": 4, "pic": 4,
        "gdc":      6,
        "gdw":      8,
        "gdh":      9,
        "objWid":  10,
        "objHig":  12,
        "pco0":    14, "pco1": 16, "pco2": 18, "pco3": 20,
        "inittrue": 22,
        "dis":     24,
        "molloc_s": 28,
        "molloc":   32,
    },
    1: {   # Slider — GuiObjSlider
        "mode":   0,
        "sta":    1,
        "psta":   2,
        "wid":    3,
        "hig":    4,
        "dis":    5,
        "pic":    6, "picc": 6, "bco": 6,
        "pic2":   8, "pco":  8,
        "val":   10,
        "maxval": 12,
        "minval": 14,
        "ch":    16,
        "pic1":  22, "picc1": 22, "bco1": 22,
    },
    5: {   # Touchcap — GuiObjTouchcap (single attribute)
        "val": 0,
    },
    51: {  # Timer — GuiObjTimer
        "tim": 0,
        "en":  2,
    },
    52: {  # Vari (Variable) — GuiObjVari
        # sta / txt / txt_maxl have sentinel attposup (-1 / 65539) → omitted.
        "val": 0,
    },
    53: {  # Button_T (touch Button) — GuiObjButton_T
        # sta / style / borderc / borderw / txt_maxl / spax / spay = -1 → omitted.
        # txt = 65548 (Sstr indirect) → omitted.
        "font":  0,
        "val":   1,
        "pic":   2, "picc": 2, "bco":  2,
        "pic2":  4, "picc2": 4, "bco2": 4,
        "pco":   6,
        "pco2":  8,
        "xcen": 10,
        "ycen": 11,
        "isbr": 12,
    },
    55: {  # GText / SLText (LEI 55 mapping is ambiguous; see notes below).
        # Using GText layout (matches existing tft_attrs_layout.PER_LEI_LAYOUT[55]).
        # ``GuiObjGText.GetAtts_WithNoHead`` F-series branch:
        "sta":      0,
        "style":    1,
        "borderc":  2,
        "borderw":  4,
        "font":     5,
        "bco":      6, "picc": 6, "pic": 6,
        "pco":      8,
        "xcen":    10,
        "ycen":    11,
        "dir":     12,
        "dis":     13,
        "tim":     14,
        "en":      16,
        "txt_maxl": 18,
        "txt":     20,
        "isbr":    24,
        "spax":    25,
        "spay":    26,
        "vvs0":    28, "vvs1": 30, "vvs2": 32, "vvs3": 34,
    },
    56: {  # CheckBox — GuiObjCheckBox (single-branch with conditional reset)
        "style":   0,
        "borderw": 1,
        "borderc": 2,
        "bco":     4,
        "pco":     6,
        "val":     8,
    },
    57: {  # Radio — GuiObjRadio (single-branch with conditional reset)
        "bco": 0,
        "pco": 2,
        "val": 4,
    },
    58: {  # Qrcode — GuiObjQrcode
        "sta":      0,
        "dis":      1,
        "bco":      2,
        "pco":      4,
        "pic":      6,
        "txt_maxl": 8,
        "txt":     12,
    },
    59: {  # XFloat — GuiObjXfloat
        # key = -1 → omitted.
        "sta":      0,
        "style":    1,
        "borderc":  2,
        "borderw":  4,
        "font":     5,
        "bco":      6, "picc": 6, "pic": 6,
        "pco":      8,
        "xcen":    10,
        "ycen":    11,
        "val":     12,
        "vvs0":    16,
        "vvs1":    17,
        "isbr":    20,
        "spax":    21,
        "spay":    22,
    },
    98: {  # Button — GuiObjButton (same on-disk layout as Button_T_PARAM_tk)
        # sta / style / borderc / borderw / txt_maxl / spax / spay = -1 → omitted.
        # txt = 65548 → omitted.
        "font":  0,
        "val":   1,
        "pic":   2, "picc": 2, "bco":  2,
        "pic2":  4, "picc2": 4, "bco2": 4,
        "pco":   6,
        "pco2":  8,
        "xcen": 10,
        "ycen": 11,
        "isbr": 12,
    },
    106: { # Prog (ProgressBar) — GuiObjProg
        "sta":  0,
        "dez":  1,
        "val":  2,
        "dis":  3,
        "bco":  4, "bpic": 4,
        "pco":  6, "ppic": 6,
    },
    # 109: Hotspot — no attributes (block size 0).
    112: { # Pic — GuiObjPic
        "pic": 0,
    },
    113: { # Picc (CropPicture) — GuiObjPicc
        # vvs0 / vvs1 = -1 in F-series → not allocated.
        "picc": 0,
    },
    116: { # Text — GuiObjText (uses Text_PARAM_tk, 9 bytes)
        # sta / style / key / borderc / borderw / txt_maxl / spax / spay = -1 → omitted.
        # txt = 65544 → omitted.
        "font":  0,
        "isbr":  1,
        "pco":   2,
        "bco":   4, "picc": 4, "pic": 4,
        "xcen":  6,
        "ycen":  7,
        "pw":    8,
    },
    121: { # Page — GuiObjPage
        "up":    0,
        "down":  1,
        "left":  2,
        "right": 3,
        "sta":   4,
        "bco":   6, "pic": 6,
    },
    122: { # Zhizhen (Gauge / Pointer) — GuiObjZhizhen
        "sta":     0,
        "bco":     2, "picc": 2, "pic":  2,
        "val":     4,
        "format":  6,
        "up":      8,
        "down":   10,
        "left":   12,
        "pco":    14,
        "pco2":   16,
        "hig":    18,
        "wid":    20,
        "vvs0":   21,
        "vvs1":   22,
        "vvs2":   23,
    },
}


# ---------------------------------------------------------------------------
# MemoryAllocator
# ---------------------------------------------------------------------------

@dataclass
class _ComponentRecord:
    comp_type: int
    comp_name: str
    memorypos: int   # absolute offset within public memory


@dataclass
class MemoryAllocator:
    """Allocates public-memory slots for page components and resolves
    component-attribute byte offsets.

    Parameters
    ----------
    app_allvas_qty:
        Number of global ``int`` variables declared in ``Program.s``
        (``AppAllvasQty`` in the TFT header).  Each global occupies 4 bytes
        starting at offset 0.
    cursor:
        Current allocation pointer.  Defaults to ``4 * app_allvas_qty``,
        i.e. right after the global-variable area.  Override if you want to
        skip the implicit page pseudo-component block first.
    series:
        ``"T0"`` (default) selects the empirically-derived 16_loop layout
        (XFloat / Vari only).  ``"T1"`` selects the F-series tables
        (extended coverage, IL-derived, with 4-byte block alignment).

    Examples
    --------
    Round-trip the 16_loop page-0 layout (T0)::

        alloc = MemoryAllocator(app_allvas_qty=4)
        alloc.cursor = 4 * 4 + 39   # 16 bytes globals + 39-byte page block
        alloc.add_object(59, "x0")
        assert alloc.frame_offset("x0", "bco") == 0x38
        assert alloc.frame_offset("x0", "val") == 0x3c
    """

    app_allvas_qty: int = 0
    series: str = "T0"
    cursor: int = field(init=False)
    _components: Dict[str, _ComponentRecord] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.cursor = 4 * self.app_allvas_qty
        if self.series not in ("T0", "T1"):
            raise ValueError(f"unsupported series {self.series!r}; expected 'T0' or 'T1'")

    # ------------------------------------------------------------------

    @property
    def _sizeof_table(self) -> Dict[int, int]:
        return SIZEOF_OBJDATA_T1 if self.series == "T1" else SIZEOF_OBJDATA

    @property
    def _attposup_table(self) -> Dict[int, Dict[str, int]]:
        return ATTPOSUP_TABLE_T1 if self.series == "T1" else ATTPOSUP_TABLE

    def add_object(self, comp_type: int, comp_name: str) -> int:
        """Reserve a public-memory block for *comp_name* of *comp_type*.

        Returns the component's ``memorypos`` (absolute offset where its
        block begins).  Raises ``ValueError`` if the type is unknown.
        """
        sz_table = self._sizeof_table
        if comp_type not in sz_table:
            raise ValueError(
                f"unknown component type {comp_type} for {comp_name!r}: "
                f"add it to SIZEOF_OBJDATA{'_T1' if self.series == 'T1' else ''} "
                f"to continue"
            )
        mempos = self.cursor
        self._components[comp_name] = _ComponentRecord(
            comp_type=comp_type,
            comp_name=comp_name,
            memorypos=mempos,
        )
        self.cursor += sz_table[comp_type]
        if self.series == "T1":
            # F-series aligns each block to 4 bytes (see
            # findings/memory-allocation.md § per-page object loop).
            while self.cursor & 3:
                self.cursor += 1
        return mempos

    def frame_offset(self, comp_name: str, attr_name: str) -> int:
        """Return the absolute public-memory byte offset of *attr_name* on
        component *comp_name*.

        Raises ``KeyError`` if the component or attribute is unknown.
        """
        rec = self._components[comp_name]
        attpos_table = self._attposup_table.get(rec.comp_type, {})
        if attr_name not in attpos_table:
            raise KeyError(
                f"attribute {attr_name!r} not found for type {rec.comp_type} "
                f"({comp_name!r}); known attrs: {sorted(attpos_table)}"
            )
        return rec.memorypos + attpos_table[attr_name]

    def has_component(self, comp_name: str) -> bool:
        """Return True if *comp_name* has been registered."""
        return comp_name in self._components


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # ---- T0 self-test --------------------------------------------------
    # Verify ATTPOSUP offsets against byte-verified positions from
    # 16_loop/16.tft (T0 fixture, model_series=0). The page contains
    # many unknown intermediate components, so we set cursor explicitly
    # for each known component rather than assuming sequential order.

    alloc = MemoryAllocator(app_allvas_qty=4)

    # XFloat (type 59): memorypos = bco_offset - ATTPOSUP_TABLE[59]["bco"]
    _bco_ap = ATTPOSUP_TABLE[59]["bco"]
    for comp_name, bco_offset in [
        ("x0", 0x38), ("x2", 0x8a), ("x4", 0xdc), ("x5", 0xe5),
        ("x1", 0x105), ("x6", 0x161), ("x7", 0x178), ("x8", 0x1a1),
    ]:
        alloc.cursor = bco_offset - _bco_ap
        alloc.add_object(59, comp_name)

    assert alloc.frame_offset("x0", "bco") == 0x38
    assert alloc.frame_offset("x0", "val") == 0x3c
    assert alloc.frame_offset("x2", "bco") == 0x8a
    assert alloc.frame_offset("x2", "val") == 0x8e
    assert alloc.frame_offset("x4", "bco") == 0xdc
    assert alloc.frame_offset("x4", "val") == 0xe0
    assert alloc.frame_offset("x5", "bco") == 0xe5
    assert alloc.frame_offset("x5", "val") == 0xe9
    assert alloc.frame_offset("x1", "bco") == 0x105
    assert alloc.frame_offset("x1", "val") == 0x109
    assert alloc.frame_offset("x6", "bco") == 0x161
    assert alloc.frame_offset("x6", "val") == 0x165
    assert alloc.frame_offset("x7", "bco") == 0x178
    assert alloc.frame_offset("x7", "val") == 0x17c
    assert alloc.frame_offset("x8", "bco") == 0x1a1
    assert alloc.frame_offset("x8", "val") == 0x1a5

    # x7 and x8 are consecutive in memory (no unknown component between them);
    # their stride must equal SIZEOF_OBJDATA[59].
    assert (0x1a1 - _bco_ap) - (0x178 - _bco_ap) == SIZEOF_OBJDATA[59]

    # Vari (type 52): val_attpos=0 so memorypos == val offset
    _val_ap = ATTPOSUP_TABLE[52]["val"]
    for comp_name, val_offset in [
        ("bco", 0x37b), ("blu", 0x386), ("red", 0x391),
        ("wht", 0x39c), ("yel", 0x3a7), ("org", 0x3b2), ("grn", 0x3bd),
    ]:
        alloc.cursor = val_offset - _val_ap
        alloc.add_object(52, comp_name)

    assert alloc.frame_offset("bco", "val") == 0x37b
    assert alloc.frame_offset("blu", "val") == 0x386
    assert alloc.frame_offset("red", "val") == 0x391
    assert alloc.frame_offset("wht", "val") == 0x39c
    assert alloc.frame_offset("yel", "val") == 0x3a7
    assert alloc.frame_offset("org", "val") == 0x3b2
    assert alloc.frame_offset("grn", "val") == 0x3bd

    # Consecutive Vari stride must equal SIZEOF_OBJDATA[52].
    assert 0x386 - 0x37b == SIZEOF_OBJDATA[52]

    # ---- T1 / F-series self-test --------------------------------------
    # No byte-verified F-series fixture exists in the repo yet, so these
    # tests exercise table consistency only:
    #
    #   * Every known F-series LEI is present in both SIZEOF_OBJDATA_T1
    #     and ATTPOSUP_TABLE_T1 (or excluded with reason — Hotspot only).
    #   * Attribute offsets fit within the block size for each LEI.
    #   * 4-byte alignment is applied between sequential add_object calls.
    #   * The XFloat val attposup is +12 (matches the IL-derived value;
    #     compare with T0's val=+5 — they differ because T0 uses a
    #     different, shorter block layout).

    # Sanity: every LEI in attposup table has a sizeof entry.
    for lei in ATTPOSUP_TABLE_T1:
        assert lei in SIZEOF_OBJDATA_T1, (
            f"LEI {lei} has attposup but no SIZEOF_OBJDATA_T1 entry"
        )
        # Every attribute offset must be < block size.
        sz = SIZEOF_OBJDATA_T1[lei]
        for attr, off in ATTPOSUP_TABLE_T1[lei].items():
            assert 0 <= off < sz, (
                f"LEI {lei} attr {attr!r} offset {off} out of block "
                f"size {sz}"
            )

    # 4-byte alignment test: lay out a small page and verify cursor jumps.
    t1 = MemoryAllocator(app_allvas_qty=0, series="T1")
    t1.add_object(121, "page0")   # 8 bytes (Page_PARAM, already 4-aligned)
    assert t1.cursor == 8
    t1.add_object(59, "x9")        # 24 bytes (Xfloat_PARAM)
    assert t1.cursor == 8 + 24, f"cursor={t1.cursor}"
    t1.add_object(98, "b0")        # 13 bytes (Button_PARAM_tk) → aligned to 16
    assert t1.cursor == 8 + 24 + 16, f"cursor={t1.cursor}"
    t1.add_object(116, "t0")       # 9 bytes (Text_PARAM_tk) → aligned to 12
    assert t1.cursor == 8 + 24 + 16 + 12, f"cursor={t1.cursor}"

    # Frame offsets through the table:
    # x9.memorypos = 8 (after Page_PARAM 8 bytes); val_attposup = 12.
    assert t1.frame_offset("x9", "val") == 8 + 12
    assert t1.frame_offset("x9", "bco") == 8 + 6
    assert t1.frame_offset("page0", "sta") == 0 + 4
    # b0.memorypos = 8 + 24 = 32; val_attposup = 1.
    assert t1.frame_offset("b0", "val") == 32 + 1
    # t0.memorypos = 32 + 16 (Button block padded) = 48; pco_attposup = 2.
    assert t1.frame_offset("t0", "pco") == 48 + 2

    # XFloat val differs T0 (+5) vs T1 (+12) — sanity confirm both tables
    # are honored independently:
    assert ATTPOSUP_TABLE[59]["val"] == 5
    assert ATTPOSUP_TABLE_T1[59]["val"] == 12

    print("memory_allocator self-test OK")
