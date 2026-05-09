#!/usr/bin/env python3
"""diff_tft.py — structured diff of two TFT files.

Splits each file into the standard regions per Path B's findings and
diffs them separately:

  - File header 1 (offsets 0x000..0x0c8)
  - File header 2 (offsets 0x0c8..0x190; 'encrypted' on F-series)
  - Resources (0x10000..0x70000 by default — read from H1)
  - User code (0x70000..end-4)
  - Tail CRC (last 4 bytes)

Special focus on H2: the goal is F-series XOR-key recovery, so this script
also XOR's the H2 regions against each other and prints the result. If the
key is a fixed-pad XOR, this difference equals the unencrypted XOR, which
should match the unencrypted differences in H1 / known-plaintext fields.
"""
from __future__ import annotations
import argparse
import struct
from pathlib import Path

H1_END = 0xC8
H2_END = 0x190


def _diff_runs(a: bytes, b: bytes):
    n = min(len(a), len(b))
    runs = []
    i = 0
    while i < n:
        if a[i] == b[i]:
            i += 1
            continue
        start = i
        while i < n and a[i] != b[i]:
            i += 1
        runs.append((start, i - start))
    if len(a) != len(b):
        runs.append((n, max(len(a), len(b)) - n))
    return runs


def _print_region(name: str, a: bytes, b: bytes, start: int):
    runs = _diff_runs(a, b)
    if not runs and len(a) == len(b):
        print(f"  {name}: identical ({len(a)} bytes)")
        return
    total = sum(l for _, l in runs)
    print(f"  {name}: differs in {len(runs)} run(s), {total} bytes total"
          f" (size a={len(a)} b={len(b)})")
    for s, l in runs[:8]:
        ws = max(0, s - 4)
        we = min(len(a), s + l + 4)
        print(f"    @file 0x{start+s:08x} (+{l}): "
              f"a={a[ws:we].hex()} b={b[ws:we].hex()}")
    if len(runs) > 8:
        print(f"    ... and {len(runs)-8} more")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--xor-h2", action="store_true",
                    help="Print full XOR(a_h2, b_h2) for known-plaintext analysis.")
    args = ap.parse_args()

    a = Path(args.a).read_bytes()
    b = Path(args.b).read_bytes()
    print(f"a: {args.a} ({len(a)} bytes)")
    print(f"b: {args.b} ({len(b)} bytes)")
    print()

    if len(a) < H2_END or len(b) < H2_END:
        print("file too short to be a TFT")
        return 1

    # Resource region. For F-series we know the layout (Path D): the
    # resources sit at 0x10000..0x70000 by convention. If the file is
    # shorter than 0x70000, fall back to file end - 4 (tail CRC).
    res_addr = 0x10000
    res_end = min(0x70000, len(a) - 4)
    if len(a) < res_addr or len(b) < res_addr:
        # Tiny file — skip resources/usercode breakdown
        res_addr = res_end = H2_END

    print("regions:")
    _print_region("h1       ", a[:H1_END], b[:H1_END], 0)
    _print_region("h2       ", a[H1_END:H2_END], b[H1_END:H2_END], H1_END)
    _print_region("h2-gap   ", a[H2_END:res_addr], b[H2_END:res_addr], H2_END)
    _print_region("resources", a[res_addr:res_end], b[res_addr:res_end], res_addr)
    _print_region("usercode ", a[res_end:-4], b[res_end:-4], res_end)
    _print_region("tail crc ", a[-4:], b[-4:], len(a) - 4)

    if args.xor_h2:
        ah2 = a[H1_END:H2_END]
        bh2 = b[H1_END:H2_END]
        n = min(len(ah2), len(bh2))
        x = bytes(ah2[i] ^ bh2[i] for i in range(n))
        print()
        print("XOR(a_h2, b_h2):")
        for off in range(0, len(x), 32):
            print(f"  +0x{off:03x}: {x[off:off+32].hex()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
