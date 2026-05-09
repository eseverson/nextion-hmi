#!/usr/bin/env python3
"""diff_bytes.py — generic per-byte diff of two binary files.

Reports the byte ranges that differ, with hex windows around each. Designed
for reverse-engineering — pinpoints exactly which bytes a single editor
change moved.

Usage:
    scripts/diff_bytes.py a.bin b.bin
    scripts/diff_bytes.py a.bin b.bin --window 16 --max-runs 50
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path


def _diff_runs(a: bytes, b: bytes):
    """Yield (start, length) for each maximal differing run."""
    n = min(len(a), len(b))
    i = 0
    while i < n:
        if a[i] == b[i]:
            i += 1
            continue
        start = i
        while i < n and a[i] != b[i]:
            i += 1
        yield start, i - start
    if len(a) != len(b):
        # Tail mismatch (one file longer than the other)
        yield n, max(len(a), len(b)) - n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--window", type=int, default=8,
                    help="Bytes of context to print around each run.")
    ap.add_argument("--max-runs", type=int, default=1000)
    ap.add_argument("--coalesce", type=int, default=4,
                    help="Merge runs separated by <=N matching bytes.")
    args = ap.parse_args()

    a = Path(args.a).read_bytes()
    b = Path(args.b).read_bytes()

    runs = list(_diff_runs(a, b))
    # Coalesce nearby runs
    coalesced: list[tuple[int, int]] = []
    for start, length in runs:
        if coalesced and start - (coalesced[-1][0] + coalesced[-1][1]) <= args.coalesce:
            ps, pl = coalesced[-1]
            coalesced[-1] = (ps, start + length - ps)
        else:
            coalesced.append((start, length))

    print(f"len(a)={len(a)} len(b)={len(b)}; {len(coalesced)} differing run(s)")
    if not coalesced:
        print("  (identical)")
        return 0
    total_changed = sum(l for _, l in coalesced)
    print(f"  total changed bytes: {total_changed}")
    print()
    for start, length in coalesced[: args.max_runs]:
        ws = max(0, start - args.window)
        we = min(max(len(a), len(b)), start + length + args.window)
        a_hex = " ".join(f"{x:02x}" for x in a[ws:we])
        b_hex = " ".join(f"{x:02x}" for x in b[ws:we])
        # Mark the differing region with a caret line
        marker = []
        for i in range(ws, we):
            if start <= i < start + length:
                marker.append("^^")
            else:
                marker.append("  ")
        marker_s = " ".join(marker)
        print(f"@ 0x{start:08x} ({length} bytes)")
        print(f"  a: {a_hex}")
        print(f"  b: {b_hex}")
        print(f"     {marker_s}")
        print()
    if len(coalesced) > args.max_runs:
        print(f"... and {len(coalesced) - args.max_runs} more run(s) elided")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
