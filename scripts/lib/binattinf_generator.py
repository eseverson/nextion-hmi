"""binattinf_generator — generate per-page binattinf records from the HMI.

The page-wide ``allattbytes`` table (file ``strdata + pagexinxi.attdataaddr``)
holds, per component (in objxinxi order), a fixed-stride block of 24-byte
``binattinf`` records.  Each block is:

  * 8 **head** records (id, type, x, y, endx, endy, w, h) — immutable
    geometry, ``datafrom=False`` (the value IS the literal coordinate),
  * a ``vscope`` record, and
  * one **datafrom** record per RAM-backed attribute the component's class
    declares (sta, bco, pco, val, font, …) whose ``attmemorypos`` is the
    object's media-frame offset ``objdatarampos + attposup`` (NOT the
    literal value — the literal lives in the media blob).

Records sit at class-specific offsets inside the block (see
``tft_attrs_layout.PER_LEI_LAYOUT``); empty offsets are 24 zero bytes.

This module derives every record's field values from the HMI component +
the media allocator's ``objdatarampos`` and emits the block byte-for-byte
via ``tft_attrs_encoder.encode_binattinf``.  Verified against
``source/nextion.hmi.tft``.

## Field rules (reversed from the project TFT)

  * ``str_encodeh_star = 0`` (every record).
  * ``frompageid`` = device page id; ``fromobjid`` = object index on page.
  * ``objdatarampos`` = the media allocator's value for the object.
  * Head records: ``change=True, datafrom=False``; ``attmemorypos`` =
    literal (id, lei, x, y, endx=x+w-1, endy=y+h-1, w, h); per-field type
    & bounds in :data:`HEAD_FIELDS`.
  * ``vscope``: UU8, max 1, value 0, ``datafrom=False``.
  * datafrom attrs: ``datafrom=True``; ``attmemorypos = objdatarampos +
    attposup``; per-attr type / bounds / change in :data:`DATAFROM_ATTRS`.
"""
from __future__ import annotations

from dataclasses import dataclass

from scripts.lib.tft_attrs_encoder import (
    AttRecord, ATTSHULEI_BY_NAME, encode_binattinf,
)
from scripts.lib.tft_attrs_layout import PER_LEI_LAYOUT


# Head records (id, type, x, y, endx, endy, w, h) — order matches the
# block layout's first 8 logical fields. (name, type_name, min, max).
HEAD_FIELDS: list[tuple[str, str, int, int]] = [
    ("id",   "UU8",  0,      255),
    ("type", "UU8",  0,      255),
    ("x",    "x",    -6000,  6000),
    ("y",    "y",    -6000,  6000),
    ("endx", "SS16", -32768, 32767),
    ("endy", "SS16", -32768, 32767),
    ("w",    "w",    -2000,  2000),
    ("h",    "h",    -2000,  2000),
]


@dataclass(frozen=True)
class DatafromAttr:
    """One RAM-backed (datafrom) attribute declaration for a component
    class: its in-frame ``attposup`` offset, storage type, value bounds,
    and runtime-change flag.

    ``merrylenth_override`` forces the record's byte-stride field when it
    differs from the type's natural ``datafenpei`` — used for ``txt``
    (Sstr) whose record carries the inline-buffer size (16 Text / 11
    Button) rather than the 4-byte Sstr stride.

    ``changeid`` is the editor's per-class "changeable attribute"
    enumeration index written into record byte +19 (``att_changeid``).
    Only Slider (1) and Timer (51) carry non-zero values in the fixture
    corpus; everything else is 0.
    """

    name: str
    attposup: int
    type_name: str
    minval: int
    maxval: int
    change: bool
    merrylenth_override: int | None = None
    changeid: int = 0


@dataclass(frozen=True)
class LiteralAttr:
    """One non-RAM-backed (datafrom=False) attribute whose binattinf
    record stores the *literal* authored value (e.g. Text/Button sta,
    style, spax, spay, txt_maxl).  ``change=True`` like the head records.

    ``value_key`` names the HMI attribute to read (defaults to ``name``);
    ``default`` is used when the HMI omits it.  ``txt_maxl`` is special:
    its value is the inline-buffer capacity minus 1 (computed by the
    caller), so it uses ``value_key=None`` and the caller supplies it.
    """

    name: str
    type_name: str
    minval: int
    maxval: int
    value_key: str | None = None
    default: int = 0


# Per-class datafrom attribute tables, reversed from source/nextion.hmi.tft.
# Order doesn't matter (records are placed by name via PER_LEI_LAYOUT).
# Component classes whose binattinf block is byte-verified against
# source/nextion.hmi.tft end-to-end.  Every component class in the project
# — Page(121), Hotspot(109), XFloat(59), Text(116), Prog(106), Slider(1),
# Button(98), Timer(51), Variable(52) — now generates its whole binattinf
# block byte-exact: all 4 pages reproduce byte-for-byte (att_changeid,
# literal records, txt inline/spill buffer, the Variable 0xff sentinels +
# sta record are all wired).  build_component_binattinf still raises for a
# lei outside this set unless allow_unverified=True.
VERIFIED_LEIS = frozenset({121, 109, 1, 116, 98, 59, 106, 51, 52, 54,
                           112, 113, 56, 57, 122, 58, 0, 53, 55})


DATAFROM_ATTRS: dict[int, list[DatafromAttr]] = {
    121: [  # Page
        DatafromAttr("sta", 4, "Select", 0, 2, True),
        DatafromAttr("bco", 6, "Color", 0, 65535, False),
    ],
    109: [],  # Hotspot — no RAM-backed attrs (head + vscope only)
    59: [  # XFloat  (NOT yet whole-block verified — see VERIFIED_LEIS)
        DatafromAttr("sta", 0, "Select", 0, 2, True),
        DatafromAttr("style", 1, "Select", 0, 3, True),
        DatafromAttr("font", 5, "Fontid", 0, 1, False),
        DatafromAttr("bco", 6, "Color", 0, 65535, False),
        DatafromAttr("pco", 8, "Color", 0, 65535, False),
        DatafromAttr("xcen", 10, "Select", 0, 2, False),
        DatafromAttr("ycen", 11, "Select", 0, 2, False),
        DatafromAttr("val", 12, "SS32", -2147483648, 2147483647, False),
        DatafromAttr("vvs0", 16, "UU8", 0, 10, False),
        DatafromAttr("vvs1", 17, "UU8", 0, 8, False),
        DatafromAttr("isbr", 20, "Select", 0, 1, False),
        DatafromAttr("spax", 21, "UU8", 0, 255, False),
        DatafromAttr("spay", 22, "UU8", 0, 255, False),
    ],
    54: [  # Number (numeric display; XFloat-like with lenth/format)
        DatafromAttr("sta", 0, "Select", 0, 2, True),
        DatafromAttr("style", 1, "Select", 0, 3, True),
        DatafromAttr("font", 5, "Fontid", 0, 1, False),
        DatafromAttr("bco", 6, "Color", 0, 65535, False),
        DatafromAttr("pco", 8, "Color", 0, 65535, False),
        DatafromAttr("xcen", 10, "Select", 0, 2, False),
        DatafromAttr("ycen", 11, "Select", 0, 2, False),
        DatafromAttr("val", 12, "SS32", -2147483648, 2147483647, False),
        DatafromAttr("lenth", 16, "Select", 0, 15, False),
        DatafromAttr("format", 17, "Select", 0, 2, False),
        DatafromAttr("isbr", 20, "Select", 0, 1, False),
        DatafromAttr("spax", 21, "UU8", 0, 255, False),
        DatafromAttr("spay", 22, "UU8", 0, 255, False),
    ],
    116: [  # Text
        DatafromAttr("font", 0, "Fontid", 0, 1, False),
        DatafromAttr("isbr", 1, "Select", 0, 1, False),
        DatafromAttr("pco", 2, "Color", 0, 65535, False),
        DatafromAttr("bco", 4, "Color", 0, 65535, False),
        DatafromAttr("xcen", 6, "Select", 0, 2, False),
        DatafromAttr("ycen", 7, "Select", 0, 2, False),
        DatafromAttr("pw", 8, "Select", 0, 1, False),
        DatafromAttr("txt", 9, "Sstr", 0, 0, False, merrylenth_override=16),
    ],
    106: [  # Prog
        DatafromAttr("sta", 0, "Select", 0, 1, True),
        DatafromAttr("dez", 1, "Select", 0, 1, True),
        DatafromAttr("val", 2, "UU8", 0, 100, False),
        DatafromAttr("dis", 3, "UU8", 0, 100, False),
        DatafromAttr("bco", 4, "Color", 0, 65535, False),
        DatafromAttr("pco", 6, "Color", 0, 65535, False),
    ],
    1: [  # Slider
        DatafromAttr("mode", 0, "Select", 0, 1, True),
        DatafromAttr("sta", 1, "Select", 0, 2, True),
        DatafromAttr("psta", 2, "Select", 0, 1, True),
        DatafromAttr("wid", 3, "UU8", 0, 255, False, changeid=1),
        DatafromAttr("hig", 4, "UU8", 0, 255, False, changeid=2),
        DatafromAttr("bco", 6, "Color", 0, 65535, False),
        DatafromAttr("pco", 8, "Color", 0, 65535, False),
        DatafromAttr("val", 10, "UU16", 0, 65535, False, changeid=5),
        DatafromAttr("maxval", 12, "UU16", 0, 65535, False, changeid=3),
        DatafromAttr("minval", 14, "UU16", 0, 65535, False, changeid=4),
        DatafromAttr("ch", 16, "UU8", 0, 255, False),
        DatafromAttr("bco1", 22, "Color", 0, 65535, False),
    ],
    98: [  # Button
        DatafromAttr("font", 0, "Fontid", 0, 1, False),
        DatafromAttr("val", 1, "Select", 0, 1, False),
        DatafromAttr("bco", 2, "Color", 0, 65535, False),
        DatafromAttr("bco2", 4, "Color", 0, 65535, False),
        DatafromAttr("pco", 6, "Color", 0, 65535, False),
        DatafromAttr("pco2", 8, "Color", 0, 65535, False),
        DatafromAttr("xcen", 10, "Select", 0, 2, False),
        DatafromAttr("ycen", 11, "Select", 0, 2, False),
        DatafromAttr("isbr", 12, "Select", 0, 1, False),
        DatafromAttr("txt", 13, "Sstr", 0, 0, False, merrylenth_override=11),
    ],
    51: [  # Timer
        DatafromAttr("tim", 0, "UU16", 50, 65534, False, changeid=1),
        DatafromAttr("en", 2, "Select", 0, 1, False, changeid=2),
    ],
    52: [  # Variable
        DatafromAttr("val", 0, "SS32", -2147483648, 2147483647, False),
    ],
    # --- untested.tft error-page types (me/miata-dash#38) ----------------
    112: [  # Picture — single Picid
        DatafromAttr("pic", 0, "Picid", 0, -1, False),
    ],
    113: [  # Picc (CropPicture) — Picid + two SS16 vvs literals
        DatafromAttr("picc", 0, "Picid", 0, -1, False),
    ],
    56: [  # CheckBox
        DatafromAttr("style", 0, "Select", 0, 3, True),
        DatafromAttr("bco", 4, "Color", 0, 65535, False),
        DatafromAttr("pco", 6, "Color", 0, 65535, False),
        DatafromAttr("val", 8, "Select", 0, 1, False),
    ],
    57: [  # Radio
        DatafromAttr("bco", 0, "Color", 0, 65535, False),
        DatafromAttr("pco", 2, "Color", 0, 65535, False),
        DatafromAttr("val", 4, "Select", 0, 1, False),
    ],
    122: [  # Gauge / Zhizhen
        DatafromAttr("sta", 0, "Select", 0, 2, True),
        DatafromAttr("bco", 2, "Color", 0, 65535, False),
        DatafromAttr("val", 4, "UU16", 0, 360, False),
        DatafromAttr("pco", 14, "Color", 0, 65535, False),
        DatafromAttr("wid", 20, "UU8", 1, 20, False),
    ],
    58: [  # QRcode
        DatafromAttr("sta", 0, "Select", 0, 1, True),
        DatafromAttr("dis", 1, "UU8", 0, 255, False),
        DatafromAttr("bco", 2, "Color", 0, 65535, False),
        DatafromAttr("pco", 4, "Color", 0, 65535, False),
        DatafromAttr("txt", 100, "Sstr", 0, 0, False, merrylenth_override=31),
        DatafromAttr("txt_maxl", 8, "UU16", 0, 192, True),
    ],
    0: [  # Waveform / Curve
        DatafromAttr("sta", 0, "Select", 0, 2, True),
        DatafromAttr("dir", 1, "Select", 0, 2, True),
        DatafromAttr("ch", 2, "Select", 1, 4, True),
        DatafromAttr("bco", 4, "Color", 0, 65535, False),
        DatafromAttr("gdc", 6, "Color", 0, 65535, False),
        DatafromAttr("gdw", 8, "UU8", 0, 255, False),
        DatafromAttr("gdh", 9, "UU8", 0, 255, False),
        DatafromAttr("pco0", 14, "Color", 0, 65535, False),
        DatafromAttr("dis", 24, "UU16", 10, 1000, False),
    ],
    53: [  # Button_T (DualStateButton)
        DatafromAttr("font", 0, "Fontid", 0, 1, False),
        DatafromAttr("val", 1, "Select", 0, 1, False),
        DatafromAttr("bco", 2, "Color", 0, 65535, False),
        DatafromAttr("bco2", 4, "Color", 0, 65535, False),
        DatafromAttr("pco", 6, "Color", 0, 65535, False),
        DatafromAttr("pco2", 8, "Color", 0, 65535, False),
        DatafromAttr("xcen", 10, "Select", 0, 2, False),
        DatafromAttr("ycen", 11, "Select", 0, 2, False),
        DatafromAttr("isbr", 12, "Select", 0, 1, False),
        DatafromAttr("txt", 13, "Sstr", 0, 0, False, merrylenth_override=11),
    ],
    55: [  # GText / ScrollingText
        DatafromAttr("sta", 0, "Select", 0, 2, True),
        DatafromAttr("style", 1, "Select", 0, 3, True),
        DatafromAttr("font", 5, "Fontid", 0, 1, False),
        DatafromAttr("bco", 6, "Color", 0, 65535, False),
        DatafromAttr("pco", 8, "Color", 0, 65535, False),
        DatafromAttr("xcen", 10, "Select", 0, 2, False),
        DatafromAttr("ycen", 11, "Select", 0, 2, False),
        DatafromAttr("dir", 12, "Select", 0, 3, False),
        DatafromAttr("dis", 13, "Select", 2, 50, False),
        DatafromAttr("tim", 14, "UU16", 80, 65534, False),
        DatafromAttr("en", 16, "Select", 0, 1, False),
        DatafromAttr("txt", 72, "Sstr", 0, 0, False, merrylenth_override=11),
        DatafromAttr("txt_maxl", 18, "UU16", 0, 65534, True),
        DatafromAttr("isbr", 24, "Select", 0, 1, False),
        DatafromAttr("spax", 25, "UU8", 0, 255, False),
        DatafromAttr("spay", 26, "UU8", 0, 255, False),
        DatafromAttr("vvs0", 28, "SS16", -2147483648, 2147483647, False),
        DatafromAttr("vvs1", 30, "SS16", -2147483648, 2147483647, False),
        DatafromAttr("vvs2", 32, "SS16", -2147483648, 2147483647, False),
        DatafromAttr("vvs3", 34, "SS16", -2147483648, 2147483647, False),
    ],
}


# Inline `txt` buffer capacity per type (bytes); the `txt_maxl` literal
# record stores capacity-1, and the `txt` datafrom record's merrylenth is
# this value when the string fits inline.
INLINE_TXT_BUFFER: dict[int, int] = {116: 16, 98: 11, 53: 11, 58: 31, 55: 11}


# Per-class non-datafrom literal records (value stored verbatim,
# change=True, datafrom=False).  ``txt_maxl`` is appended automatically
# for types in INLINE_TXT_BUFFER (capacity-1, Strlenth, max 65534).
LITERAL_ATTRS: dict[int, list[LiteralAttr]] = {
    116: [  # Text
        LiteralAttr("sta", "Select", 0, 2, default=0),
        LiteralAttr("style", "Select", 0, 3, default=0),
        LiteralAttr("spax", "UU8", 0, 255, default=0),
        LiteralAttr("spay", "UU8", 0, 255, default=0),
    ],
    98: [  # Button
        LiteralAttr("sta", "Select", 0, 2, default=0),
        LiteralAttr("style", "Select", 0, 4, default=0),
        LiteralAttr("spax", "UU8", 0, 255, default=0),
        LiteralAttr("spay", "UU8", 0, 255, default=0),
    ],
    52: [  # Variable
        LiteralAttr("sta", "Select", 0, 1, default=0),
    ],
    # --- untested.tft error-page types (me/miata-dash#38) ----------------
    113: [  # Picc — two SS16 crop-window literals (default 0)
        LiteralAttr("vvs0", "SS16", -2147483648, 2147483647, default=0),
        LiteralAttr("vvs1", "SS16", -2147483648, 2147483647, default=0),
    ],
    53: [  # Button_T — sta/style/spax/spay literals (txt_maxl auto-appended)
        LiteralAttr("sta", "Select", 0, 2, default=0),
        LiteralAttr("style", "Select", 0, 4, default=0),
        LiteralAttr("spax", "UU8", 0, 255, default=0),
        LiteralAttr("spay", "UU8", 0, 255, default=0),
    ],
}


# Component classes whose binattinf records use the 0xff/0xff
# frompageid/fromobjid sentinels (page-global components) rather than the
# page/object index.  Variables (lei 52) are page-global.
SENTINEL_FROM_LEIS = frozenset({52})


def _round4(n: int) -> int:
    return (n + 3) & ~3


def _txt_merrylenth(inline_buffer: int, txt, txt_maxl=None) -> int:
    """The ``txt`` Sstr record's ``merrylenth`` (and ``txt_maxl`` = this
    minus 1) for a component with the given inline buffer capacity.

    The capacity is the editor's configured maximum text length + 1 (the
    NUL), floored at the inline buffer:

      * ``txt_maxl + 1`` when the HMI declares a ``txt_maxl`` larger than
        the inline buffer (the configured field width, **independent of the
        current ``txt`` string** — the live s0 has ``txt=''`` but
        ``txt_maxl=40`` → 41); else
      * ``inline_buffer`` (e.g. Text 16, Button 11).

    The current string's *spill* into the media frame is a separate concern
    (the media allocator), but its capacity coincides: a fitting string
    keeps the inline buffer, and a string sized to ``txt_maxl`` spills to
    the same ``txt_maxl + 1`` (verified on the source s0
    "Danger to manifold!!" = 20 → 16 + 24 + 1 = 41 = ``txt_maxl + 1``).
    """
    if txt_maxl is not None:
        return max(inline_buffer, int(txt_maxl) + 1)
    if not isinstance(txt, str):
        return inline_buffer
    blen = len(txt.encode("latin-1", "replace"))
    if blen + 1 <= inline_buffer:
        return inline_buffer
    return inline_buffer + _round4(blen + 1) + 1


def _head_records(lei, comp_id, x, y, w, h, objdatarampos, frompageid,
                  fromobjid) -> dict[str, AttRecord]:
    endx = x + w - 1 if w else 0
    endy = y + h - 1 if h else 0
    values = {"id": comp_id, "type": lei, "x": x, "y": y,
              "endx": endx, "endy": endy, "w": w, "h": h}
    out: dict[str, AttRecord] = {}
    for name, type_name, lo, hi in HEAD_FIELDS:
        tv, df = ATTSHULEI_BY_NAME[type_name]
        out[name] = AttRecord(
            name=name, attmemorypos=values[name],
            num_maxval=hi, num_minval=lo,
            objdatarampos=objdatarampos, frompageid=frompageid,
            fromobjid=fromobjid, str_encodeh_star=0, att_changeid=0,
            typevalue=tv, datafenpei=df,
            change=True, datafrom=False, ispv=True, pp=True,
        )
    return out


def _vscope_record(objdatarampos, frompageid, fromobjid,
                   is_global=False) -> AttRecord:
    # The vscope record's attmemorypos flags global scope: 1 for a
    # ``vscope=1`` (page-global) component, 0 for a page-local one.
    tv, df = ATTSHULEI_BY_NAME["UU8"]
    return AttRecord(
        name="vscope", attmemorypos=1 if is_global else 0,
        num_maxval=1, num_minval=0,
        objdatarampos=objdatarampos, frompageid=frompageid,
        fromobjid=fromobjid, str_encodeh_star=0, att_changeid=0,
        typevalue=tv, datafenpei=df,
        change=True, datafrom=False, ispv=True, pp=True,
    )


def build_component_binattinf(
    lei: int,
    comp_id: int,
    x: int, y: int, w: int, h: int,
    *,
    objdatarampos: int,
    frompageid: int,
    fromobjid: int,
    authored: dict | None = None,
    allow_unverified: bool = False,
    font_count: int | None = None,
) -> bytes:
    """Generate one component's binattinf block (stride * 24 bytes) from
    its geometry + HMI attributes + the media allocator's ``objdatarampos``.

    ``authored`` carries the component's HMI attribute values (used for the
    literal records — sta/style/spax/spay).  Only the byte-verified classes
    (:data:`VERIFIED_LEIS`) are emitted by default; pass
    ``allow_unverified=True`` to emit a reversed-but-not-whole-block-verified
    class (the ``att_changeid`` enumeration gap may still differ).

    ``font_count`` is the project's font count (appinf1 ``zimoqyt``); when
    given, every ``Fontid`` attribute's ``num_maxval`` is set to
    ``font_count - 1`` (a resources-coupled bound — the source fixture has 2
    fonts → max 1, the live project has 3 → max 2)."""
    if lei not in DATAFROM_ATTRS:
        raise KeyError(
            f"no DATAFROM_ATTRS table for lei={lei}; reverse its RAM-backed "
            f"attrs (attposup/type/bounds/change) and add them"
        )
    if lei not in VERIFIED_LEIS and not allow_unverified:
        raise NotImplementedError(
            f"binattinf for lei={lei} is reversed but not yet whole-block "
            f"verified (the att_changeid enumeration gap remains); pass "
            f"allow_unverified=True to emit it anyway"
        )
    authored = authored or {}
    stride, offset_map = PER_LEI_LAYOUT[lei]

    # Page-global components (Variable) tag every record with 0xff/0xff
    # frompageid/fromobjid sentinels instead of the page/object index.
    if lei in SENTINEL_FROM_LEIS:
        frompageid = 0xFF
        fromobjid = 0xFF

    def mk(name, mem, mx, mn, type_name, *, change, datafrom,
           merrylenth=None, changeid=0):
        tv, df = ATTSHULEI_BY_NAME[type_name]
        return AttRecord(
            name=name, attmemorypos=mem, num_maxval=mx, num_minval=mn,
            objdatarampos=objdatarampos, frompageid=frompageid,
            fromobjid=fromobjid, str_encodeh_star=0, att_changeid=changeid,
            typevalue=tv, datafenpei=(merrylenth if merrylenth is not None else df),
            change=change, datafrom=datafrom, ispv=True, pp=True,
        )

    records: dict[str, AttRecord] = {}
    records.update(_head_records(lei, comp_id, x, y, w, h,
                                 objdatarampos, frompageid, fromobjid))
    records["vscope"] = _vscope_record(
        objdatarampos, frompageid, fromobjid,
        is_global=bool(authored.get("vscope") == 1))

    # txt buffer capacity (merrylenth field for the txt Sstr record) — the
    # inline buffer when the string fits, else inline + round4(len+1) + 1
    # when it spills into the media-frame tail (see _txt_merrylenth).
    txt_merrylenth = None
    if lei in INLINE_TXT_BUFFER:
        inline_buf = INLINE_TXT_BUFFER[lei]
        # A GLOBAL (vscope=1) Text stores its txt in the 20-byte global-memory
        # record whose inline budget is 11 (not the 16 a page-frame Text has);
        # its binattinf txt capacity / Strlenth follow that (me/miata-dash#35:
        # global_mixed's t0 → merrylenth 11, txt_maxl 10).
        if lei == 116 and authored.get("vscope") == 1:
            inline_buf = 11
        txt_merrylenth = _txt_merrylenth(
            inline_buf, authored.get("txt"),
            authored.get("txt_maxl"))

    # datafrom (RAM-backed) render records: attmemorypos = frame offset.
    for da in DATAFROM_ATTRS[lei]:
        merrylenth = da.merrylenth_override
        if da.name == "txt" and txt_merrylenth is not None:
            merrylenth = txt_merrylenth
        # Fontid bound is resources-coupled: max = font_count - 1.
        maxval = da.maxval
        if da.type_name == "Fontid" and font_count is not None:
            maxval = max(0, font_count - 1)
        records[da.name] = mk(
            da.name, objdatarampos + da.attposup, maxval, da.minval,
            da.type_name, change=da.change, datafrom=True,
            merrylenth=merrylenth, changeid=da.changeid,
        )

    # Non-datafrom literal records (sta/style/spax/spay store HMI value).
    for la in LITERAL_ATTRS.get(lei, []):
        key = la.value_key or la.name
        val = authored.get(key, la.default)
        records[la.name] = mk(
            la.name, int(val) if val is not None else 0,
            la.maxval, la.minval, la.type_name,
            change=True, datafrom=False,
        )

    # txt_maxl Strlenth literal record (txt capacity - 1) — only when the
    # type doesn't already declare txt_maxl as a datafrom record (QRcode /
    # GText carry a RAM-backed UU16 txt_maxl instead; see DATAFROM_ATTRS).
    if (txt_merrylenth is not None
            and not any(da.name == "txt_maxl" for da in DATAFROM_ATTRS[lei])):
        records["txt_maxl"] = mk(
            "txt_maxl", txt_merrylenth - 1, 65534, 0, "Strlenth",
            change=True, datafrom=False,
        )

    zero = b"\x00" * 24
    block = [zero] * stride
    for name, rec in records.items():
        off = offset_map.get(name)
        if off is not None:
            block[off] = encode_binattinf(rec)
    return b"".join(block)


__all__ = [
    "HEAD_FIELDS",
    "DatafromAttr",
    "LiteralAttr",
    "DATAFROM_ATTRS",
    "LITERAL_ATTRS",
    "INLINE_TXT_BUFFER",
    "VERIFIED_LEIS",
    "build_component_binattinf",
]
