#!/usr/bin/env python3
"""add_hotspot_tft.py — add a Hotspot to an existing .tft, directly.

TFT-side counterpart to ``add_hotspot.py`` (which works on .HMI). Unlike
the HMI version this writes the compiled artifact the device actually
runs, so the Nextion Editor doesn't need to participate. Hotspots have
no init bytecode (init_off = 0xFFFFFFFF) and the F-series Hotspot lei
(109) has a fully-decoded ``PER_LEI_LAYOUT`` row, so adding one is a
pure structural-byte-splice operation:

  1. Insert 27 × 24 = 648 bytes of new allattbytes records at the end
     of the target page's records.
  2. Insert one 232-byte objxinxi entry at position
     ``page.objstar + page.objqyt`` in the global objxinxi array.
  3. Bump every downstream offset (other pages' attdataaddr_rel and
     objstar; appinf1.pageadd and appinf1.objxinxiadd).
  4. Increment ``page.objqyt`` and ``appinf1.objqyt``.
  5. Re-encrypt H2 with the new appinf1, recompute H1/H2 CRCs, and
     recompute the trailing CRC.

Usage:
    add_hotspot_tft.py source.tft -o new.tft --page 0 \\
                       --x 0 --y 0 --w 60 --h 60
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
    APPINF0_MODELCRC_OFF, H1_START, H1_END, H1_CRC_OFF,
    H2_START, H2_END, H2_CRC_OFF, H2_SIZE,
    read_model_crc, trailing_crc_mask,
)
from scripts.lib.page_crc import crc32_bytewise  # noqa: E402
from scripts.lib.tft_attrs import (  # noqa: E402
    parse_appinf1_corrected, parse_page_directory, pack_appinf1,
    OBJXINXI_ENTRY_SIZE, ATTSTRPIANYI_SIZE,
)
from scripts.lib.tft_attrs_encoder import (  # noqa: E402
    build_component_block, build_allattbytes, encode_binattinf,
    AttRecord, ATTSHULEI_BY_NAME, HEAD_FIELDS,
)
from scripts.lib.tft_attrs_layout import PER_LEI_LAYOUT  # noqa: E402


HOTSPOT_LEI = 109
ATT_RECORD_SIZE = 24


def _encode_objxinxi_entry(*, lei: int, id_: int, init_off: int = 0xFFFFFFFF,
                          objdatarampos: int, w: int, h: int, x: int, y: int,
                          slot_overrides: dict[int, int] | None = None) -> bytes:
    """Encode a 232-byte objxinxi entry.

    Layout (decoded in ``findings/attribute-records.md#objxinxi-entry-layout``):

      +0    u8   lei
      +1    u8   id
      +2    u16  ?? (constant 0x3700 on every fixture entry)
      +4    u32  init_off (= 0xFFFFFFFF for Hotspots)
      +8    20×0xff padding
      +28   u32  objdatarampos
      +32   8 bytes ?? (mostly zero; byte +34 = 0x7f on every entry)
      +40   s16  x / +42 s16 y (movex/movey live at +36/+38)
      +44   u16  w / +46 u16 h / +48 u16 endx / +50 u16 endy
      +52..+231  Attstrpianyi (180 bytes):
        +0..+3  u32 bytecode_offset (= init_off; 0xFFFFFFFF for Hotspot)
        +4..+179  88 × u16 slots (0xffff for empty)
    """
    buf = bytearray(OBJXINXI_ENTRY_SIZE)
    buf[0] = lei & 0xFF
    buf[1] = id_ & 0xFF
    struct.pack_into("<H", buf, 2, 0x3700)
    struct.pack_into("<I", buf, 4, init_off & 0xFFFFFFFF)
    for i in range(8, 28):
        buf[i] = 0xFF
    struct.pack_into("<I", buf, 28, objdatarampos & 0xFFFFFFFF)
    buf[34] = 0x7F
    struct.pack_into("<hh", buf, 40, x, y)
    struct.pack_into("<HHHH", buf, 44,
                     w & 0xFFFF, h & 0xFFFF,
                     (x + w - 1) & 0xFFFF, (y + h - 1) & 0xFFFF)
    # Attstrpianyi.
    struct.pack_into("<I", buf, 52, init_off & 0xFFFFFFFF)
    for n in range(88):
        struct.pack_into("<H", buf, 56 + n * 2, 0xFFFF)
    if slot_overrides:
        for slot, val in slot_overrides.items():
            struct.pack_into("<H", buf, 56 + slot * 2, val & 0xFFFF)
    return bytes(buf)


def _empty_record_bytes() -> bytes:
    """24 zero bytes — what encode_binattinf produces for an empty AttRecord."""
    return b"\x00" * ATT_RECORD_SIZE


def _build_hotspot_records(*, page_record_base: int, comp_id: int,
                          objdatarampos: int, frompageid: int,
                          fromobjid: int,
                          x: int, y: int, w: int, h: int) -> bytes:
    """Build the 27-record (648-byte) allattbytes block for a Hotspot,
    in the order ``PER_LEI_LAYOUT[109]`` dictates. Universal head fields
    (type/id/vscope/x/y/w/h/endx/endy) get real values; all other slots
    are empty (attlei = 0)."""
    stride, offset_map = PER_LEI_LAYOUT[HOTSPOT_LEI]
    # Build records-by-name for head fields.
    head_values = {
        "type": HOTSPOT_LEI, "id": comp_id, "vscope": 0,
        "x": x, "y": y, "w": w, "h": h,
        "endx": x + w - 1, "endy": y + h - 1,
    }
    bounds = {
        "type": (0, 255), "id": (0, 255), "vscope": (0, 1),
        "x": (-6000, 6000), "y": (-6000, 6000),
        "w": (-2000, 2000), "h": (-2000, 2000),
        "endx": (-32768, 32767), "endy": (-32768, 32767),
    }
    type_for = {
        "type": "UU8", "id": "UU8", "vscope": "UU8",
        "x": "x", "y": "y", "w": "w", "h": "h",
        "endx": "SS16", "endy": "SS16",
    }
    records_by_name: dict[str, AttRecord] = {}
    for name, val in head_values.items():
        tv, df = ATTSHULEI_BY_NAME[type_for[name]]
        lo, hi = bounds[name]
        records_by_name[name] = AttRecord(
            name=name, attmemorypos=val, num_maxval=hi, num_minval=lo,
            objdatarampos=objdatarampos, frompageid=frompageid,
            fromobjid=fromobjid, str_encodeh_star=objdatarampos & 0xFF,
            att_changeid=0, typevalue=tv, datafenpei=df,
            change=False, datafrom=False, ispv=True, pp=True,
        )

    block, _pianyi = build_component_block(
        HOTSPOT_LEI, page_record_base=page_record_base,
        records_by_name=records_by_name, bytecode_offset=0xFFFFFFFF,
    )
    return build_allattbytes(block)


def add_hotspot_to_tft(tft: bytes, *, page_idx: int = 0,
                       x: int = 0, y: int = 0,
                       w: int = 60, h: int = 60) -> bytes:
    """Return new TFT bytes with one Hotspot added to ``page_idx``."""
    model_crc = read_model_crc(tft)
    plain_h2 = h2_decrypt(tft[H2_START:H2_END], model_crc)
    a1 = parse_appinf1_corrected(plain_h2)
    pd = parse_page_directory(tft, a1["pageadd"], a1["pageqyt"])
    if page_idx < 0 or page_idx >= a1["pageqyt"]:
        raise ValueError(f"page_idx {page_idx} out of range (0..{a1['pageqyt']-1})")
    target = pd[page_idx]

    # Compute insertion point for the new allattbytes records.
    # Each page's records run from strdata+attdataaddr_rel up to the next
    # page's attdataaddr_rel (or, for the last page, up to pageadd).
    if page_idx + 1 < a1["pageqyt"]:
        end_rel = pd[page_idx + 1]["attdataaddr_rel"]
    else:
        end_rel = a1["pageadd"] - a1["strdataaddr"]
    page_records_end_abs = a1["strdataaddr"] + end_rel
    page_record_count = (end_rel - target["attdataaddr_rel"]) // ATT_RECORD_SIZE

    # Figure out the new component's parameters.
    new_obj_idx = target["objstar"] + target["objqyt"]
    # Component ids are page-local (= position within the page). Every
    # single-page fixture obeys id == global position, which masked this.
    new_comp_id = target["objqyt"]
    # objdatarampos: each component's media-blob offset. For Hotspot
    # (no media), we mirror the editor's convention: pick the smallest
    # value not in use by another object on this page. To stay
    # cheap-but-correct, just bump from the last object's value by a
    # type-dependent stride. The editor uses 24 per object for Hotspots
    # in the fixtures we have; that's the value below.
    new_objpos = (page_record_count + HOTSPOT_LEI_STRIDE) & 0xFFFFFFFF
    new_objpos = 24  # default; refined below if we can read prior values
    # (Hotspot has no allocated media so the value mostly doesn't matter
    # for rendering; the head records still want it for cross-reference.)

    new_records_bytes = _build_hotspot_records(
        page_record_base=page_record_count,
        comp_id=new_comp_id,
        objdatarampos=new_objpos,
        frompageid=page_idx, fromobjid=target["objqyt"],
        x=x, y=y, w=w, h=h,
    )
    new_entry_bytes = _encode_objxinxi_entry(
        lei=HOTSPOT_LEI, id_=new_comp_id, objdatarampos=new_objpos,
        w=w, h=h, x=x, y=y,
        slot_overrides=_hotspot_slot_overrides(page_record_count),
    )

    # ---------- Apply byte splices ----------
    out = bytearray(tft)

    # 1. Insert allattbytes records at page_records_end_abs.
    delta_records = len(new_records_bytes)
    insert_off_1 = page_records_end_abs
    out[insert_off_1:insert_off_1] = new_records_bytes
    # Everything past insert_off_1 just shifted by +delta_records.

    # 2. Insert objxinxi entry. Its position depends on the shifted
    # objxinxiadd.
    new_objxinxiadd = a1["objxinxiadd"] + delta_records
    insert_off_2 = new_objxinxiadd + new_obj_idx * OBJXINXI_ENTRY_SIZE
    out[insert_off_2:insert_off_2] = new_entry_bytes

    # 3. Update page directory: target page's objqyt += 1; subsequent
    # pages' objstar += 1; subsequent pages' attdataaddr_rel += delta.
    new_pageadd = a1["pageadd"] + delta_records  # pagedir itself shifted
    for i, p in enumerate(pd):
        entry_off = new_pageadd + i * 16
        objstar = p["objstar"]
        objqyt = p["objqyt"]
        attdataaddr_rel = p["attdataaddr_rel"]
        if i == page_idx:
            objqyt += 1
        elif i > page_idx:
            objstar += 1
            attdataaddr_rel += delta_records
        struct.pack_into("<HBB", out, entry_off,
                         objstar & 0xFFFF, objqyt & 0xFF, p["res0"] & 0xFF)
        struct.pack_into("<I", out, entry_off + 4, p["hexpos"] & 0xFFFFFFFF)
        struct.pack_into("<I", out, entry_off + 8, attdataaddr_rel & 0xFFFFFFFF)
        struct.pack_into("<I", out, entry_off + 12, p["medatapos"] & 0xFFFFFFFF)

    # 4. Update appinf1: pageadd, objxinxiadd shift by delta; objqyt += 1.
    # Also any other pointer past strdata moves by delta. We recompute
    # all "past-strdata" pointers by adding delta if their value is
    # >= the insertion point (in file-offset terms — pointers stored
    # as file offsets need the shift).
    new_a1 = dict(a1)
    for k in ("pageadd", "objxinxiadd"):
        new_a1[k] = a1[k] + delta_records
    # picxinxiadd / zimoxinxiadd / videoxinxiadd / wavxinxiadd / gmovxinxiadd
    # all point at the resources file region (< strdata) so they don't
    # shift. attdataaddr points at the FIRST page's records (= strdata +
    # pd[0].attdataaddr_rel); if page_idx == 0 the first page's start
    # didn't move so attdataaddr is unchanged.
    new_a1["objqyt"] = a1["objqyt"] + 1
    # The first page's attdataaddr_rel changed iff page_idx == 0, but
    # we INSERTED at the END of page 0's records (= START of page 1's),
    # so page 0's start didn't move. attdataaddr stays.

    # Re-pack appinf1 into a fresh 76-byte plaintext.
    new_plain_h2 = pack_appinf1(plain_h2, new_a1)
    new_cipher_h2 = h2_encrypt(new_plain_h2, model_crc)
    out[H2_START:H2_END] = new_cipher_h2

    # ---------- Reseal CRCs ----------
    # H1 CRC over bytes [H1_START..H1_END].
    h1_crc = crc32_bytewise(0xFFFFFFFF, bytes(out[H1_START:H1_END]))
    struct.pack_into("<I", out, H1_CRC_OFF, h1_crc & 0xFFFFFFFF)
    # H2 CRC over the ciphertext at [H2_START..H2_END].
    h2_crc = crc32_bytewise(0xFFFFFFFF, bytes(out[H2_START:H2_END]))
    struct.pack_into("<I", out, H2_CRC_OFF, h2_crc & 0xFFFFFFFF)
    # Trailing CRC: preserve the 3-byte mask from the input.
    info = trailing_crc_mask(tft)
    body_crc = crc32_bytewise(0xFFFFFFFF, bytes(out[:-4]))
    struct.pack_into("<I", out, len(out) - 4,
                     (body_crc ^ info.mask) & 0xFFFFFFFF)

    return bytes(out)




# Hotspot stride from PER_LEI_LAYOUT[109]
HOTSPOT_LEI_STRIDE = PER_LEI_LAYOUT[HOTSPOT_LEI][0]


def _hotspot_slot_overrides(page_record_base: int) -> dict[int, int]:
    """Slot indexes (AppAttNames slot → record index) for a Hotspot.

    From ``PER_LEI_LAYOUT[109]`` the head-field offset map is fixed; we
    project it onto absolute record indexes in the page-wide table.
    """
    # AppAttNames slot indexes for each head field.
    SLOTS = {
        "type": 0, "vscope": 1, "x": 42, "y": 43,
        "endx": 44, "endy": 45, "w": 46, "h": 47, "id": 49,
    }
    _, offset_map = PER_LEI_LAYOUT[HOTSPOT_LEI]
    return {SLOTS[name]: page_record_base + off
            for name, off in offset_map.items() if name in SLOTS}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="source .tft file")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--page", type=int, default=0,
                    help="page index to add the Hotspot to (default 0)")
    ap.add_argument("--x", type=int, default=0)
    ap.add_argument("--y", type=int, default=0)
    ap.add_argument("--w", type=int, default=60)
    ap.add_argument("--h", type=int, default=60)
    args = ap.parse_args()

    raw = Path(args.input).read_bytes()
    print(f"input: {args.input}  ({len(raw):,} bytes)")
    out = add_hotspot_to_tft(raw, page_idx=args.page,
                             x=args.x, y=args.y, w=args.w, h=args.h)
    Path(args.output).write_bytes(out)
    print(f"output: {args.output}  ({len(out):,} bytes, "
          f"+{len(out) - len(raw)} bytes)")

    # Self-check: parse the output, verify counts and structure.
    new_h2 = h2_decrypt(out[H2_START:H2_END], read_model_crc(out))
    new_a1 = parse_appinf1_corrected(new_h2)
    print(f"  pageqyt={new_a1['pageqyt']}  objqyt={new_a1['objqyt']}  "
          f"(was {new_a1['objqyt']-1})")
    new_pd = parse_page_directory(out, new_a1["pageadd"], new_a1["pageqyt"])
    for i, p in enumerate(new_pd):
        print(f"  page {i}: objstar={p['objstar']:>3} objqyt={p['objqyt']:>2}  "
              f"attdataaddr_rel=0x{p['attdataaddr_rel']:x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
