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
    and runtime-change flag."""

    name: str
    attposup: int
    type_name: str
    minval: int
    maxval: int
    change: bool


# Per-class datafrom attribute tables, reversed from source/nextion.hmi.tft.
# Order doesn't matter (records are placed by name via PER_LEI_LAYOUT).
# Component classes whose binattinf block is byte-verified against
# source/nextion.hmi.tft end-to-end (page-1 / page-3: page + hotspot).
# The other DATAFROM_ATTRS tables below are reversed from the per-attr
# dump but NOT yet byte-verified as whole blocks — they still need the
# att_changeid model, the txt_maxl/Sstr long-string pairing, and the
# `style` layout slot (see findings/next-steps.md). build_component_binattinf
# raises for an unverified lei unless allow_unverified=True.
VERIFIED_LEIS = frozenset({121, 109})


DATAFROM_ATTRS: dict[int, list[DatafromAttr]] = {
    121: [  # Page
        DatafromAttr("sta", 4, "Select", 0, 2, True),
        DatafromAttr("bco", 6, "Color", 0, 65535, False),
    ],
    109: [],  # Hotspot — no RAM-backed attrs (head + vscope only)
    59: [  # XFloat  (NOT yet whole-block verified — see VERIFIED_LEIS)
        DatafromAttr("sta", 0, "Select", 0, 2, True),
        DatafromAttr("style", 1, "style", 0, 3, True),
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
    116: [  # Text
        DatafromAttr("font", 0, "Fontid", 0, 1, False),
        DatafromAttr("isbr", 1, "Select", 0, 1, False),
        DatafromAttr("pco", 2, "Color", 0, 65535, False),
        DatafromAttr("bco", 4, "Color", 0, 65535, False),
        DatafromAttr("xcen", 6, "Select", 0, 2, False),
        DatafromAttr("ycen", 7, "Select", 0, 2, False),
        DatafromAttr("pw", 8, "Select", 0, 1, False),
        DatafromAttr("txt", 9, "Sstr", 0, 0, False),
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
        DatafromAttr("wid", 3, "UU8", 0, 255, False),
        DatafromAttr("hig", 4, "UU8", 0, 255, False),
        DatafromAttr("bco", 6, "Color", 0, 65535, False),
        DatafromAttr("pco", 8, "Color", 0, 65535, False),
        DatafromAttr("val", 10, "UU16", 0, 65535, False),
        DatafromAttr("maxval", 12, "UU16", 0, 65535, False),
        DatafromAttr("minval", 14, "UU16", 0, 65535, False),
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
        DatafromAttr("txt", 13, "Sstr", 0, 0, False),
    ],
    51: [  # Timer
        DatafromAttr("tim", 0, "UU16", 50, 65534, False),
        DatafromAttr("en", 2, "Select", 0, 1, False),
    ],
    52: [  # Variable
        DatafromAttr("val", 0, "SS32", -2147483648, 2147483647, False),
    ],
}


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


def _vscope_record(objdatarampos, frompageid, fromobjid) -> AttRecord:
    tv, df = ATTSHULEI_BY_NAME["UU8"]
    return AttRecord(
        name="vscope", attmemorypos=0, num_maxval=1, num_minval=0,
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
    allow_unverified: bool = False,
) -> bytes:
    """Generate one component's binattinf block (stride * 24 bytes) from
    its geometry + the media allocator's ``objdatarampos``.

    Only the byte-verified classes (:data:`VERIFIED_LEIS`) are emitted by
    default; pass ``allow_unverified=True`` to emit one of the reversed-
    but-not-whole-block-verified classes (its block will be close but not
    guaranteed byte-exact until the att_changeid / txt_maxl / Sstr gaps
    are closed)."""
    if lei not in DATAFROM_ATTRS:
        raise KeyError(
            f"no DATAFROM_ATTRS table for lei={lei}; reverse its RAM-backed "
            f"attrs (attposup/type/bounds/change) and add them"
        )
    if lei not in VERIFIED_LEIS and not allow_unverified:
        raise NotImplementedError(
            f"binattinf for lei={lei} is reversed but not yet whole-block "
            f"verified (att_changeid / txt_maxl / Sstr gaps remain); pass "
            f"allow_unverified=True to emit it anyway"
        )
    stride, offset_map = PER_LEI_LAYOUT[lei]

    records: dict[str, AttRecord] = {}
    records.update(_head_records(lei, comp_id, x, y, w, h,
                                 objdatarampos, frompageid, fromobjid))
    records["vscope"] = _vscope_record(objdatarampos, frompageid, fromobjid)
    for da in DATAFROM_ATTRS[lei]:
        tv, df = ATTSHULEI_BY_NAME[da.type_name]
        records[da.name] = AttRecord(
            name=da.name,
            attmemorypos=objdatarampos + da.attposup,
            num_maxval=da.maxval, num_minval=da.minval,
            objdatarampos=objdatarampos, frompageid=frompageid,
            fromobjid=fromobjid, str_encodeh_star=0, att_changeid=0,
            typevalue=tv, datafenpei=df,
            change=da.change, datafrom=True, ispv=True, pp=True,
        )

    # Place records at their layout offsets; empties are 24 zero bytes.
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
    "DATAFROM_ATTRS",
    "VERIFIED_LEIS",
    "build_component_binattinf",
]
