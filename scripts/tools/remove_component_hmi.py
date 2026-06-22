#!/usr/bin/env python3
"""remove_component_hmi.py — remove a component by name from an HMI page.

Mirrors the inverse of add_nav_hotspot_hmi.py: removes one PCH entry
from the page directory and its corresponding record bytes from the
data region, then reseals the CRC and directory.

Usage:
    remove_component_hmi.py in.HMI --page 2 --name b0 -o out.HMI
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


def _find_component_pch_index(pa: bytes, comp_name: str) -> int:
    """Return the 0-based PCH index for the component named comp_name."""
    # objname attribute: typebyte 0x12, then literal "objname\x00", then 8 pad bytes, then value
    name_bytes = comp_name.encode("ascii")
    pat = bytes([0x12, 0, 0, 0]) + b"objname\x00" + b"\x00" * 8 + name_bytes
    o = pa.find(pat)
    if o < 0:
        raise ValueError(f"no component named {comp_name!r} on this page")

    nobj = struct.unpack_from("<I", pa, 12)[0]
    for i in range(nobj):
        s, sz, _ = struct.unpack_from("<III", pa, PCH_BASE + i * PCH_SIZE)
        if s <= o < s + sz:
            return i
    raise ValueError(f"objname offset {o:#x} not inside any PCH span")


def remove_component_from_pa(pa: bytes, comp_name: str) -> bytes:
    """Return a new .pa blob with the named component removed."""
    nobj = struct.unpack_from("<I", pa, 12)[0]
    k = _find_component_pch_index(pa, comp_name)

    # Read all PCH entries
    pchs = []
    for i in range(nobj):
        s, sz, third = struct.unpack_from("<III", pa, PCH_BASE + i * PCH_SIZE)
        pchs.append((s, sz, third))

    k_s, k_sz, _ = pchs[k]
    print(f"removing component {comp_name!r}: PCH index {k}, "
          f"record at {k_s:#x}, size {k_sz}")

    # Build new PCH array (without entry k), adjusting startOffsets.
    # Removing the PCH entry shrinks the PCH array by PCH_SIZE bytes, so
    # all remaining startOffsets decrease by PCH_SIZE.  Removing the
    # record data at k_s shrinks the data region by k_sz bytes, so entries
    # whose records come AFTER the removed one decrease by k_sz additionally.
    new_pchs = []
    for i, (s, sz, third) in enumerate(pchs):
        if i == k:
            continue
        new_s = s - PCH_SIZE
        if s > k_s:
            new_s -= k_sz
        new_pchs.append((new_s, sz, third))

    # Data region starts right after the original PCH array.
    data_start = PCH_BASE + nobj * PCH_SIZE
    data = bytearray(pa[data_start:])
    k_data_off = k_s - data_start
    del data[k_data_off:k_data_off + k_sz]

    blob = bytearray(pa[:PCH_BASE])
    for s, sz, third in new_pchs:
        blob += struct.pack("<III", s, sz, third)
    blob += bytes(data)

    struct.pack_into("<I", blob, 12, nobj - 1)
    struct.pack_into("<I", blob, 4, len(blob))
    struct.pack_into("<I", blob, 0, page_crc(bytes(blob)))
    return bytes(blob)


def remove_component_from_hmi(raw: bytes, page_id: int,
                               comp_name: str) -> bytes:
    entry_idx, old_pa = _get_live_pa(raw, page_id)
    new_pa = remove_component_from_pa(old_pa, comp_name)

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
    ap = argparse.ArgumentParser(
        description="Remove a component by name from an HMI page.")
    ap.add_argument("input")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--page", type=int, required=True,
                    help="device page id (e.g. 2 for 'settings')")
    ap.add_argument("--name", required=True,
                    help="component objname to remove (e.g. 'b0')")
    args = ap.parse_args()

    raw = Path(args.input).read_bytes()
    out = remove_component_from_hmi(raw, args.page, args.name)
    Path(args.output).write_bytes(out)
    print(f"wrote {args.output} ({len(out)} bytes)")


if __name__ == "__main__":
    main()
