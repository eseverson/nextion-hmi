#!/usr/bin/env python3
"""resize_str_hmi.py — change a string attribute's value (and length) in
an HMI page payload.

`patch_hmi.py` can only overwrite same-length byte ranges. String
attribute records are variable-size (typebyte low nibble = value
width), so a different-length value means resizing the page payload.
Fixture `05_text_qqqqqqqq` shows the editor's own algorithm for this
(t0 "kPa" -> "QQQQQQQQ", +5 bytes):

  * the PCH (dataentry) whose [start, start+size) span contains the
    record gets ``size += delta``;
  * every subsequent PCH ``startOffset += delta``;
  * header ``datasize += delta``; page CRC recomputed;
  * everything else byte-identical.

This tool replays that, then journal-appends the new blob (tombstone
via directory rewrite + EOF append + backup-dir mirror + directory
checksum), same as ``add_hotspot.py``.

The offset is the *value* offset of the existing string inside the page
payload (e.g. from ``grep``); the record's typebyte at offset-20 is
validated against the old value length and rewritten for the new one.

Usage:
    resize_str_hmi.py in.HMI --page 0 --offset 0x337b --str "Duty Cycle %" -o out.HMI
"""
from __future__ import annotations
import argparse
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.page_crc import page_crc, directory_checksum  # noqa: E402
from scripts.lib.hmi_dir import (BACKUP_DIR_OFFSET, ENTRY_FMT,  # noqa: E402
                                  ENTRY_SIZE, parse_directory)

PCH_BASE = 0x38
PCH_SIZE = 12


def _get_live_pa(raw: bytes, page_id: int) -> tuple[int, bytes]:
    target = f"{page_id}.pa"
    for i, name, start, size, deleted, *_ in parse_directory(raw):
        if not deleted and name == target:
            return i, raw[start:start + size]
    raise RuntimeError(f"no live {target} in directory")


def _apply_resize(blob: bytearray, anchor: int, delta: int,
                  label: str) -> bytes:
    """Shared PCH/header bookkeeping after a splice: grow the span
    containing ``anchor``, shift later spans, fix datasize and CRC."""
    nobj = struct.unpack_from("<I", blob, 12)[0]
    containing = None
    for i in range(nobj):
        off = PCH_BASE + i * PCH_SIZE
        s, sz, third = struct.unpack_from("<III", blob, off)
        if containing is None and s <= anchor < s + sz:
            containing = i
            struct.pack_into("<I", blob, off + 4, sz + delta)
        elif containing is not None:
            struct.pack_into("<I", blob, off, s + delta)
    if containing is None:
        raise ValueError(f"offset {anchor:#x} not inside any PCH span")

    struct.pack_into("<I", blob, 4, len(blob))  # datasize
    struct.pack_into("<I", blob, 0, page_crc(bytes(blob)))
    print(f"{label} in PCH #{containing} (delta {delta:+d})")
    return bytes(blob)


def resize_str_in_pa(pa: bytes, offset: int, new_value: bytes) -> bytes:
    """Return a new .pa blob with the string value at ``offset`` replaced."""
    tb = pa[offset - 20]
    old_len = tb & 0x0F
    if not (0x11 <= tb <= 0x1E):
        raise ValueError(
            f"byte at offset-20 ({tb:#04x}) is not a string-record typebyte; "
            f"is --offset the value offset of a string attribute?")
    if not 1 <= len(new_value) <= 14:
        raise ValueError("new value must be 1..14 bytes (typebyte limit)")
    name = pa[offset - 16:offset - 8].split(b"\x00", 1)[0]

    blob = bytearray(pa)
    blob[offset - 20] = 0x10 | len(new_value)
    blob[offset:offset + old_len] = new_value
    return _apply_resize(blob, offset - 20,
                         len(new_value) - old_len,
                         f"record '{name.decode(errors='replace')}' "
                         f"{old_len} -> {len(new_value)} bytes")


def resize_script_line_in_pa(pa: bytes, comp_objname: bytes, event: bytes,
                             old_line: bytes, new_line: bytes) -> bytes:
    """Replace one source line of an event-handler script.

    Event scripts are stored inside the component record as
    ``u32 len "<event>-<N>"`` (N = line count) followed by N
    length-prefixed line records (e.g. main's tm0 carries
    ``codestimer-66``). Replacing a line keeps N constant, so only the
    one line record resizes; span/size bookkeeping is shared with
    string-attr resizes (PCH sizes include script bytes)."""
    o = pa.find(bytes([0x10 | len(comp_objname), 0, 0, 0])
                + b"objname\x00" + b"\x00" * 8 + comp_objname)
    if o < 0:
        raise ValueError(f"no component named {comp_objname!r} on this page")
    e = pa.find(b"\x00\x00\x00" + event + b"-", o)
    if e < 0:
        raise ValueError(f"{comp_objname!r} has no {event!r} script")
    rec = pa.find(struct.pack("<I", len(old_line)) + old_line, e)
    if rec < 0:
        raise ValueError(f"line {old_line!r} not found in {event!r} script")

    blob = bytearray(pa)
    struct.pack_into("<I", blob, rec, len(new_line))
    blob[rec + 4:rec + 4 + len(old_line)] = new_line
    return _apply_resize(blob, rec, len(new_line) - len(old_line),
                         f"{comp_objname.decode()}.{event.decode()} line "
                         f"{old_line!r} -> {new_line!r}")


def resize_str_in_hmi(raw: bytes, page_id: int, offset: int,
                      new_value: bytes) -> bytes:
    entry_idx, old_pa = _get_live_pa(raw, page_id)
    new_pa = resize_str_in_pa(old_pa, offset, new_value)

    out = bytearray(raw)
    new_start = len(out)
    out.extend(new_pa)

    # Rewrite the live directory entry to point at the EOF copy,
    # preserving the undecoded tail bytes.
    entry_off = 4 + entry_idx * ENTRY_SIZE
    name_padded = f"{page_id}.pa".encode().ljust(16, b"\x00")
    struct.pack_into(
        ENTRY_FMT, out, entry_off,
        name_padded, new_start, len(new_pa), 0,
        out[entry_off + 25], out[entry_off + 26], out[entry_off + 27],
    )
    # Mirror to the backup directory and recompute both checksums.
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
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--offset", type=lambda v: int(v, 0), required=True,
                    help="value offset of the existing string in the page payload")
    ap.add_argument("--str", dest="value", required=True)
    args = ap.parse_args()

    raw = Path(args.input).read_bytes()
    out = resize_str_in_hmi(raw, args.page, args.offset,
                            args.value.encode("ascii"))
    Path(args.output).write_bytes(out)
    print(f"wrote {args.output} ({len(out)} bytes)")


if __name__ == "__main__":
    main()
