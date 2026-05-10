#!/usr/bin/env python3
"""patch_hmi.py — modify a value in a `*.pa` page payload of an HMI file
and rewrite the page CRC so the file remains structurally valid.

Now that finding Q has cracked the page CRC algorithm
(`scripts/page_crc.py`), this is the first concrete write capability for
HMI files: edit a single Variable val (or any byte range inside a page
payload), recompute the page's leading CRC, write the file back.

This is the MVP HMI patch path. The TFT side still needs T1 to round-trip
for any field that lives in H2, but pure HMI patches are unblocked today.

Examples:

    # Set page main's `red` Variable val to 0xCAFEBABE (offset 21067 in 0.pa
    # per finding G2) and write to a new file:
    scripts/patch_hmi.py source.HMI --page 0 --offset 21067 \\
                         --u32 0xCAFEBABE -o patched.HMI

    # Replace 8 bytes verbatim:
    scripts/patch_hmi.py source.HMI --page 0 --offset 21067 \\
                         --bytes "ce fa ed fe ad de ad c0" -o patched.HMI

The directory metadata + page CRC are recomputed automatically. Other
entries (fonts, Program.s, main.HMI) are passed through unchanged.
"""
from __future__ import annotations
import argparse
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.page_crc import page_crc, crc32_bytewise  # noqa: E402


def find_live_page(raw: bytes, page_id: int) -> tuple[int, int, int, str]:
    """Return (dir_off, start, size, name) for the live `<page_id>.pa` entry."""
    count = struct.unpack_from("<I", raw, 0)[0]
    target = f"{page_id}.pa"
    for i in range(count):
        off = 4 + i * 28
        name = raw[off:off + 16].rstrip(b"\x00").decode("ascii", errors="replace")
        if name != target:
            continue
        if raw[off + 24] != 0:  # tombstoned
            continue
        start = struct.unpack_from("<I", raw, off + 16)[0]
        size = struct.unpack_from("<I", raw, off + 20)[0]
        return off, start, size, name
    raise KeyError(f"no live {target} entry found")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="source .HMI file")
    ap.add_argument("-o", "--output", required=True, help="output .HMI path")
    ap.add_argument("--page", type=int, required=True,
                    help="page id to patch (e.g. 0 for 0.pa)")
    ap.add_argument("--offset", type=lambda s: int(s, 0), required=True,
                    help="byte offset within the page payload (decimal or 0xHEX)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--u32", type=lambda s: int(s, 0),
                   help="write a u32 LE at the offset")
    g.add_argument("--u16", type=lambda s: int(s, 0))
    g.add_argument("--u8", type=lambda s: int(s, 0))
    g.add_argument("--bytes", help="space-separated hex bytes")
    g.add_argument("--str", help="ASCII string (no terminator added)")
    ap.add_argument("--verify", action="store_true",
                    help="Also re-verify all live page CRCs after patching.")
    args = ap.parse_args()

    raw = Path(args.input).read_bytes()
    dir_off, start, size, name = find_live_page(raw, args.page)
    print(f"page {name}: dir #{(dir_off-4)//28}  start=0x{start:08x}  size=0x{size:x}")

    blob = bytearray(raw[start:start + size])
    if args.offset >= len(blob):
        ap.error(f"offset 0x{args.offset:x} is beyond page size 0x{size:x}")

    # Apply the patch
    if args.u32 is not None:
        new_bytes = struct.pack("<I", args.u32 & 0xFFFFFFFF)
    elif args.u16 is not None:
        new_bytes = struct.pack("<H", args.u16 & 0xFFFF)
    elif args.u8 is not None:
        new_bytes = struct.pack("<B", args.u8 & 0xFF)
    elif args.bytes is not None:
        new_bytes = bytes(int(b, 16) for b in args.bytes.split())
    else:  # args.str
        new_bytes = args.str.encode("latin-1")

    end = args.offset + len(new_bytes)
    if end > len(blob):
        ap.error(f"patch (+{len(new_bytes)} bytes at 0x{args.offset:x}) "
                 f"runs past page end (size 0x{size:x})")
    print(f"patching: page[0x{args.offset:x}..0x{end:x}] = {new_bytes.hex()}")

    blob[args.offset:end] = new_bytes

    # Recompute the page CRC
    new_crc = page_crc(bytes(blob))
    blob[0:4] = struct.pack("<I", new_crc)
    print(f"new page CRC: 0x{new_crc:08x}")

    # Splice patched blob back into the file
    out = bytearray(raw)
    out[start:start + size] = bytes(blob)

    Path(args.output).write_bytes(bytes(out))
    print(f"wrote {args.output} ({len(out)} bytes)")

    if args.verify:
        # Re-verify all live page CRCs in the output
        from scripts.page_crc import verify
        result = bytes(out)
        count = struct.unpack_from("<I", result, 0)[0]
        for i in range(count):
            off = 4 + i * 28
            n = result[off:off + 16].rstrip(b"\x00").decode("ascii", errors="replace")
            s = struct.unpack_from("<I", result, off + 16)[0]
            sz = struct.unpack_from("<I", result, off + 20)[0]
            if result[off + 24] or not n.endswith(".pa"):
                continue
            ok = verify(result[s:s + sz])
            print(f"  [{'OK' if ok else 'FAIL'}] {n}: CRC {'matches' if ok else 'MISMATCH'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
