#!/usr/bin/env python3
"""add_nav_hotspot_hmi.py — add a page-navigation Hotspot to an HMI page
by cloning the page's existing nav hotspot (m0).

Replaces the fixture-template approach of ``add_hotspot.py``, which is
unsafe on pages with event handlers (me/nextion#1): it spliced the
template at the PCH-derived ``max_end`` and discarded the bytes after
it, amputating the last component's overflow.

The editor's real add algorithm, isolated from the clean fixture pair
``06_bco_magenta`` -> ``07_add_hotspot``:

  * insert a new PCH entry at the end of the array:
    ``(last_start + last_size + 12, record_len, 0)``;
  * shift every existing PCH ``startOffset`` by +12;
  * append the component's full record bytes at the END of the blob
    (the data region is otherwise untouched);
  * ``numberobj += 1``, ``datasize`` = new length, page CRC.

Rather than carrying a template from an old editor version, the new
record is a byte clone of the page's live ``m0`` hotspot — same editor
conventions, same 509-byte shape — with id / objname / x / endx and the
``codesdown`` script's page digit patched (all fixed-width, so the
clone never resizes).

Usage:
    add_nav_hotspot_hmi.py in.HMI --page 0 --target-page 2 -o out.HMI
        (clones 0.pa's m0 at x=0, names it m1, codesdown = "page 2")
"""
from __future__ import annotations
import argparse
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.page_crc import page_crc  # noqa: E402
from scripts.tools.resize_str_hmi import (_get_live_pa,  # noqa: E402
                                          resize_str_in_hmi)

PCH_BASE = 0x38
PCH_SIZE = 12


def _attr_value_off(rec: bytes, typebyte: int, name: bytes) -> int:
    """Offset of an attribute record's value field inside ``rec``."""
    pat = bytes([typebyte, 0, 0, 0]) + name.ljust(8, b"\x00") + b"\x00" * 8
    o = rec.find(pat)
    if o < 0:
        raise ValueError(f"attr {name!r} (typebyte {typebyte:#x}) not in record")
    return o + 20


def _extract_m0_record(pa: bytes) -> tuple[int, bytes]:
    """Return (start, bytes) of the page's m0 hotspot record, from its
    ``att-NN`` marker through the trailing ``codesup-0`` + u32."""
    o = pa.find(b"\x12\x00\x00\x00objname\x00" + b"\x00" * 8 + b"m0")
    if o < 0:
        raise ValueError("no m0 objname record on this page")
    t = pa.rfind(b"\x11\x00\x00\x00type\x00", 0, o)
    m = pa.rfind(b"att-", 0, t) - 4
    cd = pa.find(b"codesdown-1", t)
    if cd < 0:
        raise ValueError("m0 has no codesdown script to clone")
    script_len = struct.unpack_from("<I", pa, cd + 11)[0]
    cu = pa.find(b"codesup-0", cd + 15 + script_len)
    end = cu + 9 + 4
    return m, pa[m:end]


def add_nav_hotspot_to_pa(pa: bytes, target_page: int) -> bytes:
    _, rec = _extract_m0_record(pa)
    rec = bytearray(rec)

    nobj = struct.unpack_from("<I", pa, 12)[0]
    rec[_attr_value_off(rec, 0x11, b"id")] = nobj
    name_off = _attr_value_off(rec, 0x12, b"objname")
    rec[name_off:name_off + 2] = b"m1"
    struct.pack_into("<H", rec, _attr_value_off(rec, 0x12, b"x"), 0)
    struct.pack_into("<H", rec, _attr_value_off(rec, 0x12, b"endx"), 59)
    cd = bytes(rec).find(b"codesdown-1")
    script_len = struct.unpack_from("<I", rec, cd + 11)[0]
    old = rec[cd + 15:cd + 15 + script_len].decode("ascii")
    new = f"page {target_page}"
    if len(new) != script_len:
        raise ValueError(f"script {old!r} -> {new!r} changes length")
    rec[cd + 15:cd + 15 + script_len] = new.encode("ascii")
    print(f"cloned m0 ({len(rec)} bytes): id={nobj} objname=m1 x=0 "
          f"script {old!r} -> {new!r}")

    blob = bytearray(pa[:PCH_BASE])
    last_s = last_sz = 0
    for i in range(nobj):
        s, sz, third = struct.unpack_from("<III", pa, PCH_BASE + i * PCH_SIZE)
        blob += struct.pack("<III", s + PCH_SIZE, sz, third)
        last_s, last_sz = s, sz
    blob += struct.pack("<III", last_s + last_sz + PCH_SIZE, len(rec), 0)
    blob += pa[PCH_BASE + nobj * PCH_SIZE:]
    blob += rec

    struct.pack_into("<I", blob, 12, nobj + 1)
    struct.pack_into("<I", blob, 4, len(blob))
    struct.pack_into("<I", blob, 0, page_crc(bytes(blob)))
    return bytes(blob)


def add_nav_hotspot_to_hmi(raw: bytes, page_id: int, target_page: int) -> bytes:
    from scripts.lib.hmi_dir import (BACKUP_DIR_OFFSET, ENTRY_FMT, ENTRY_SIZE)
    from scripts.lib.page_crc import directory_checksum

    entry_idx, old_pa = _get_live_pa(raw, page_id)
    new_pa = add_nav_hotspot_to_pa(old_pa, target_page)

    out = bytearray(raw)
    new_start = len(out)
    out.extend(new_pa)
    entry_off = 4 + entry_idx * ENTRY_SIZE
    struct.pack_into(
        ENTRY_FMT, out, entry_off,
        f"{page_id}.pa".encode().ljust(16, b"\x00"), new_start, len(new_pa), 0,
        out[entry_off + 25], out[entry_off + 26], out[entry_off + 27],
    )
    out[BACKUP_DIR_OFFSET + entry_off:BACKUP_DIR_OFFSET + entry_off + ENTRY_SIZE] = \
        out[entry_off:entry_off + ENTRY_SIZE]
    count = struct.unpack_from("<I", out, 0)[0]
    dir_end = 4 + count * ENTRY_SIZE
    csum = directory_checksum(bytes(out[:dir_end]))
    struct.pack_into("<I", out, dir_end, csum)
    struct.pack_into("<I", out, BACKUP_DIR_OFFSET + dir_end, csum)
    return bytes(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--page", type=int, required=True,
                    help=".pa id of the page to add the hotspot to")
    ap.add_argument("--target-page", type=int, required=True,
                    help="device page id the new hotspot navigates to")
    args = ap.parse_args()

    raw = Path(args.input).read_bytes()
    out = add_nav_hotspot_to_hmi(raw, args.page, args.target_page)
    Path(args.output).write_bytes(out)
    print(f"wrote {args.output} ({len(out)} bytes)")


if __name__ == "__main__":
    main()
