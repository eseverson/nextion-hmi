#!/usr/bin/env python3
"""add_xfloat_tft.py — add an XFloat to an existing .tft, directly.

XFloat is the visible-number-display component (lei=59) — what miata-dash
uses for x0..x8 to show RPM, MAP, etc. Adding an x9 would let the
existing firmware (which already writes ``x9.val=`` for speed) actually
display speed without a Nextion-Editor round trip.

Compared to Hotspot, an XFloat needs:

- Init bytecode (setbrush + fstr + fill) written into the strdata
  bytecode region. We splice it at the end of the existing bytecode
  region so no existing object's ``init_off`` shifts.
- 41 × 24 = 984 bytes of allattbytes records (vs 27×24=648 for Hotspot)
  — head fields + 14 declared attrs (sta, style, font, bco, pco, xcen,
  ycen, val, vvs0, vvs1, isbr, spax, spay) populated from CLI args.
- The Attstrpianyi slot table maps 22 AppAttNames slots (vs 9 for
  Hotspot) onto records.

Usage:
    add_xfloat_tft.py source.tft -o new.tft --page 0 \\
                      --x 400 --y 240 --w 76 --h 32 \\
                      --bco 0x2946 --pco 0xFFFF --font 1 --val 0
"""
from __future__ import annotations
import argparse
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.h2_cipher import encrypt as h2_decrypt, decrypt as h2_encrypt  # noqa: E402
from scripts.lib.tft_format import (  # noqa: E402
    H1_START, H1_END, H1_CRC_OFF,
    H2_START, H2_END, H2_CRC_OFF,
    read_model_crc, trailing_crc_mask,
)
from scripts.lib.page_crc import crc32_bytewise  # noqa: E402
from scripts.lib.tft_attrs import (  # noqa: E402
    parse_appinf1_corrected, parse_page_directory, pack_appinf1,
    OBJXINXI_ENTRY_SIZE,
)
from scripts.lib.tft_attrs_encoder import (  # noqa: E402
    build_component_block, build_allattbytes, AttRecord, ATTSHULEI_BY_NAME,
)
from scripts.lib.tft_attrs_layout import PER_LEI_LAYOUT  # noqa: E402
from scripts.lib.tft_init_encoder import (  # noqa: E402
    Component as InitComponent, encode_init_block,
)

XFLOAT_LEI = 59
PAGE_LEI = 121
ATT_RECORD_SIZE = 24
PCH_PAGEDIR_ENTRY_SIZE = 16


# AppAttNames slot indexes for every name we touch on an XFloat.
APP_ATT_SLOTS = {
    "lei": 0, "type": 0,  # alias — slot 0 stores the lei value
    "vscope": 1, "sta": 2, "bco": 4, "picc": 5, "pic": 6,
    "pco": 16, "wid": 18, "val": 20, "maxval": 21, "minval": 22,
    "bco2": 24, "font": 25, "xcen": 26, "ycen": 27, "txt": 28,
    "txt_maxl": 29,
    "x": 42, "y": 43, "endx": 44, "endy": 45, "w": 46, "h": 47,
    "id": 49, "vvs0": 50, "vvs1": 51, "isbr": 54, "spax": 56,
    "spay": 57, "style": 59, "borderc": 60, "borderw": 61,
    "key": 63,
}


def _encode_objxinxi_entry(*, lei: int, id_: int, init_off: int,
                          objdatarampos: int, w: int, h: int,
                          x: int, y: int,
                          slot_to_record: dict[int, int]) -> bytes:
    """232-byte objxinxi entry. See findings/attribute-records.md for
    the layout."""
    buf = bytearray(OBJXINXI_ENTRY_SIZE)
    buf[0] = lei & 0xFF
    buf[1] = id_ & 0xFF
    struct.pack_into("<H", buf, 2, 0x3700)
    struct.pack_into("<I", buf, 4, init_off & 0xFFFFFFFF)
    for i in range(8, 28):
        buf[i] = 0xFF
    struct.pack_into("<I", buf, 28, objdatarampos & 0xFFFFFFFF)
    buf[34] = 0x7F
    struct.pack_into("<HHHH", buf, 44,
                     w & 0xFFFF, h & 0xFFFF,
                     (x + w - 1) & 0xFFFF, (y + h - 1) & 0xFFFF)
    struct.pack_into("<I", buf, 52, init_off & 0xFFFFFFFF)
    for n in range(88):
        struct.pack_into("<H", buf, 56 + n * 2, 0xFFFF)
    for slot, rec in slot_to_record.items():
        struct.pack_into("<H", buf, 56 + slot * 2, rec & 0xFFFF)
    return bytes(buf)


def _make_attrecord(name: str, value: int, type_name: str, *,
                   objdatarampos: int, frompageid: int, fromobjid: int,
                   bounds: tuple[int, int],
                   change: bool = True) -> AttRecord:
    tv, df = ATTSHULEI_BY_NAME[type_name]
    return AttRecord(
        name=name, attmemorypos=value,
        num_minval=bounds[0], num_maxval=bounds[1],
        objdatarampos=objdatarampos, frompageid=frompageid,
        fromobjid=fromobjid, str_encodeh_star=objdatarampos & 0xFF,
        att_changeid=0, typevalue=tv, datafenpei=df,
        change=change, datafrom=True, ispv=True, pp=True,
    )


def _build_xfloat_records(*, page_record_base: int, comp_id: int,
                         objdatarampos: int, frompageid: int,
                         fromobjid: int,
                         x: int, y: int, w: int, h: int,
                         bco: int, pco: int, font: int, val: int,
                         sta: int = 1, style: int = 4) -> bytes:
    """Build the 41-record allattbytes block for an XFloat."""
    records_by_name: dict[str, AttRecord] = {}
    common = dict(objdatarampos=objdatarampos, frompageid=frompageid,
                  fromobjid=fromobjid)
    # Head fields.
    for name, val_, tname, lo, hi, ch in [
        ("type", XFLOAT_LEI, "UU8", 0, 255, False),
        ("id", comp_id, "UU8", 0, 255, False),
        ("vscope", 0, "UU8", 0, 1, False),
        ("x", x, "x", -6000, 6000, False),
        ("y", y, "y", -6000, 6000, False),
        ("w", w, "w", -2000, 2000, False),
        ("h", h, "h", -2000, 2000, False),
        ("endx", x + w - 1, "SS16", -32768, 32767, False),
        ("endy", y + h - 1, "SS16", -32768, 32767, False),
    ]:
        records_by_name[name] = _make_attrecord(name, val_, tname,
                                                 bounds=(lo, hi),
                                                 change=ch, **common)
    # Declared attrs.
    for name, val_, tname, lo, hi in [
        ("sta",   sta,   "Select", 0, 2),
        ("style", style, "Select", 0, 4),
        ("font",  font,  "Fontid", 0, 255),
        ("bco",   bco,   "Color",  0, 65535),
        ("pco",   pco,   "Color",  0, 65535),
        ("xcen",  1,     "Select", 0, 2),
        ("ycen",  1,     "Select", 0, 2),
        ("val",   val,   "SS32",   -2147483648, 2147483647),
        ("vvs0",  0,     "UU8",    0, 255),
        ("vvs1",  0,     "UU8",    0, 255),
        ("isbr",  0,     "Select", 0, 1),
        ("spax",  0,     "UU8",    0, 255),
        ("spay",  0,     "UU8",    0, 255),
    ]:
        records_by_name[name] = _make_attrecord(name, val_, tname,
                                                 bounds=(lo, hi), **common)

    block, _ = build_component_block(
        XFLOAT_LEI, page_record_base=page_record_base,
        records_by_name=records_by_name,
        bytecode_offset=0,  # patched after we know the bytecode offset
    )
    return build_allattbytes(block), records_by_name


def _xfloat_slot_table(page_record_base: int) -> dict[int, int]:
    """Slot → page-wide-record-index for an XFloat at ``base``."""
    _, offset_map = PER_LEI_LAYOUT[XFLOAT_LEI]
    out: dict[int, int] = {}
    for name, off in offset_map.items():
        slot = APP_ATT_SLOTS.get(name, APP_ATT_SLOTS.get(
            "type" if name == "lei" else name))
        if slot is None:
            continue
        out[slot] = page_record_base + off
    return out


def add_xfloat_to_tft(
    tft: bytes, *,
    page_idx: int = 0,
    x: int = 400, y: int = 240, w: int = 76, h: int = 32,
    bco: int = 0x2946, pco: int = 0xFFFF, font: int = 1, val: int = 0,
    sta: int = 1, style: int = 4,
) -> bytes:
    """Return new TFT bytes with one XFloat added to ``page_idx``."""
    model_crc = read_model_crc(tft)
    plain_h2 = h2_decrypt(tft[H2_START:H2_END], model_crc)
    a1 = parse_appinf1_corrected(plain_h2)
    pd = parse_page_directory(tft, a1["pageadd"], a1["pageqyt"])
    if not (0 <= page_idx < a1["pageqyt"]):
        raise ValueError(f"page_idx {page_idx} out of range")
    target = pd[page_idx]

    # Page-wide record count BEFORE our insertion.
    if page_idx + 1 < a1["pageqyt"]:
        end_rel = pd[page_idx + 1]["attdataaddr_rel"]
    else:
        end_rel = a1["pageadd"] - a1["strdataaddr"]
    page_records_end_abs = a1["strdataaddr"] + end_rel
    page_record_count = (end_rel - target["attdataaddr_rel"]) // ATT_RECORD_SIZE

    new_obj_idx = target["objstar"] + target["objqyt"]
    new_comp_id = new_obj_idx
    new_objpos = 24  # cosmetic for now; firmware reads via PCH-based refs

    # 1. Build the records first (no bytecode dependency).
    new_records_bytes, recs_by_name = _build_xfloat_records(
        page_record_base=page_record_count,
        comp_id=new_comp_id,
        objdatarampos=new_objpos,
        frompageid=page_idx, fromobjid=target["objqyt"],
        x=x, y=y, w=w, h=h,
        bco=bco, pco=pco, font=font, val=val, sta=sta, style=style,
    )

    # 2. Generate init bytecode. Need page_record_base + offset_map lookup
    # for every attr_addr callback.
    _, offset_map = PER_LEI_LAYOUT[XFLOAT_LEI]
    # Find the Page (obj0 on this page) record indexes to resolve page_bco.
    # Page records start at the FIRST record of the FIRST object on the page.
    page_obj_records_base = target["objstar"]  # not really — this is the
    # object index, not record index. Look up the Page's records directly:
    # they start at page_record_base 0 relative to the page's record table.
    page_layout = PER_LEI_LAYOUT[PAGE_LEI]
    page_bco_idx = 0 + page_layout[1]["bco"]   # base 0 for first obj on page

    def attr_addr(name: str) -> int:
        if name == "page_bco":
            return page_bco_idx
        if name in offset_map:
            return page_record_count + offset_map[name]
        # Aliases the init encoder may ask for that aren't in offset_map:
        if name in ("picc", "pic"):  # share attpos with bco for XFloat
            return page_record_count + offset_map["bco"]
        raise KeyError(f"no addr for attr {name!r}")

    init_comp = InitComponent(
        comp_type=XFLOAT_LEI, comp_id=new_comp_id, page_id=page_idx,
        x=x, y=y, w=w, h=h,
        attrs={"sta": sta, "style": style},
    )
    bytecode = encode_init_block(init_comp, attr_addr)
    bytecode_size = len(bytecode)

    # 3. The bytecode goes at the end of the existing strdata bytecode
    # region. That region's end is just before page 0's records start
    # (page 0's attdataaddr_rel marks the bytecode→records boundary).
    bytecode_insert_off = a1["strdataaddr"] + pd[0]["attdataaddr_rel"]
    bytecode_offset_in_strdata = pd[0]["attdataaddr_rel"]  # = init_off

    # 4. Build the objxinxi entry (now we know init_off).
    new_entry_bytes = _encode_objxinxi_entry(
        lei=XFLOAT_LEI, id_=new_comp_id, init_off=bytecode_offset_in_strdata,
        objdatarampos=new_objpos, w=w, h=h, x=x, y=y,
        slot_to_record=_xfloat_slot_table(page_record_count),
    )

    # 5. Splice everything. The bytecode insertion shifts page 0's
    # records (and everything after) by +bytecode_size. So we recompute
    # the records insertion point relative to the SHIFTED layout.
    out = bytearray(tft)

    # 5a. Insert bytecode at the bytecode/records boundary.
    out[bytecode_insert_off:bytecode_insert_off] = bytecode
    # Everything past bytecode_insert_off shifted by +bytecode_size.

    # 5b. Insert records at the end of page_idx's records (in the
    # shifted layout).
    records_insert_off = page_records_end_abs + bytecode_size
    out[records_insert_off:records_insert_off] = new_records_bytes
    delta_records = len(new_records_bytes)

    # 5c. Insert objxinxi entry. objxinxiadd shifted by bytecode_size
    # and by delta_records.
    new_objxinxiadd = a1["objxinxiadd"] + bytecode_size + delta_records
    entry_insert_off = new_objxinxiadd + new_obj_idx * OBJXINXI_ENTRY_SIZE
    out[entry_insert_off:entry_insert_off] = new_entry_bytes

    # 6. Rewrite the page directory.
    new_pageadd = a1["pageadd"] + bytecode_size + delta_records
    for i, p in enumerate(pd):
        entry_off = new_pageadd + i * PCH_PAGEDIR_ENTRY_SIZE
        objstar = p["objstar"]
        objqyt = p["objqyt"]
        attdataaddr_rel = p["attdataaddr_rel"]
        hexpos = p["hexpos"]
        if i == page_idx:
            objqyt += 1
        if i > page_idx:
            objstar += 1
        if i == 0:
            attdataaddr_rel += bytecode_size
        else:
            attdataaddr_rel += bytecode_size
        if i > page_idx:
            attdataaddr_rel += delta_records
        # hexpos: points into the bytecode region. Existing pages all
        # have hexpos < bytecode_insert_off so they don't shift.
        struct.pack_into("<HBB", out, entry_off,
                         objstar & 0xFFFF, objqyt & 0xFF, p["res0"] & 0xFF)
        struct.pack_into("<I", out, entry_off + 4, hexpos & 0xFFFFFFFF)
        struct.pack_into("<I", out, entry_off + 8,
                         attdataaddr_rel & 0xFFFFFFFF)
        struct.pack_into("<I", out, entry_off + 12,
                         p["medatapos"] & 0xFFFFFFFF)

    # 7. Update appinf1.
    new_a1 = dict(a1)
    new_a1["attdataaddr"] = a1["attdataaddr"] + bytecode_size
    new_a1["pageadd"] = a1["pageadd"] + bytecode_size + delta_records
    new_a1["objxinxiadd"] = a1["objxinxiadd"] + bytecode_size + delta_records
    new_a1["objqyt"] = a1["objqyt"] + 1
    # MainCodeHex points at the start of the bytecode region (= H1 size,
    # = 76). Unchanged.
    new_plain_h2 = pack_appinf1(plain_h2, new_a1)
    new_cipher_h2 = h2_encrypt(new_plain_h2, model_crc)
    out[H2_START:H2_END] = new_cipher_h2

    # 8. Update every other objxinxi entry's init_off:
    # entries whose init_off > bytecode_insert_off (in strdata-relative
    # terms) shifted by +bytecode_size. We INSERTED at the END of the
    # bytecode region, so no existing init_off shifted (they all point
    # *before* the insertion point).
    # No updates needed for existing entries.

    # 9. Reseal CRCs.
    h1_crc = crc32_bytewise(0xFFFFFFFF, bytes(out[H1_START:H1_END]))
    struct.pack_into("<I", out, H1_CRC_OFF, h1_crc & 0xFFFFFFFF)
    h2_crc = crc32_bytewise(0xFFFFFFFF, bytes(out[H2_START:H2_END]))
    struct.pack_into("<I", out, H2_CRC_OFF, h2_crc & 0xFFFFFFFF)
    info = trailing_crc_mask(tft)
    body_crc = crc32_bytewise(0xFFFFFFFF, bytes(out[:-4]))
    struct.pack_into("<I", out, len(out) - 4,
                     (body_crc ^ info.mask) & 0xFFFFFFFF)

    return bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--x", type=int, default=400)
    ap.add_argument("--y", type=int, default=240)
    ap.add_argument("--w", type=int, default=76)
    ap.add_argument("--h", type=int, default=32)
    ap.add_argument("--bco", type=lambda s: int(s, 0), default=0x2946)
    ap.add_argument("--pco", type=lambda s: int(s, 0), default=0xFFFF)
    ap.add_argument("--font", type=int, default=1)
    ap.add_argument("--val", type=int, default=0)
    ap.add_argument("--sta", type=int, default=1)
    ap.add_argument("--style", type=int, default=4)
    args = ap.parse_args()

    raw = Path(args.input).read_bytes()
    print(f"input: {args.input}  ({len(raw):,} bytes)")
    out = add_xfloat_to_tft(
        raw, page_idx=args.page, x=args.x, y=args.y, w=args.w, h=args.h,
        bco=args.bco, pco=args.pco, font=args.font, val=args.val,
        sta=args.sta, style=args.style,
    )
    Path(args.output).write_bytes(out)
    print(f"output: {args.output}  ({len(out):,} bytes, "
          f"+{len(out) - len(raw)} bytes)")

    # Self-check.
    new_a1 = parse_appinf1_corrected(
        h2_decrypt(out[H2_START:H2_END], read_model_crc(out)))
    new_pd = parse_page_directory(out, new_a1["pageadd"], new_a1["pageqyt"])
    print(f"  pageqyt={new_a1['pageqyt']}  objqyt={new_a1['objqyt']}")
    for i, p in enumerate(new_pd):
        print(f"  page {i}: objstar={p['objstar']:>3} objqyt={p['objqyt']:>2} "
              f"attdataaddr_rel=0x{p['attdataaddr_rel']:x} "
              f"hexpos=0x{p['hexpos']:x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
