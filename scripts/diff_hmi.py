#!/usr/bin/env python3
"""diff_hmi.py — structured diff of two HMI files.

Compares directories (entry-by-entry) and per-entry payloads. Useful for
reverse-engineering the HMI format — drop two saves with one variable
changed and see exactly which fields moved.
"""
from __future__ import annotations
import argparse
import struct
import sys
from pathlib import Path

# Reuse Path A's directory layout (sim/loader.py uses it via Nextion2Text).
ENTRY_STRIDE = 28
HEADER_LEN = 4  # u32 entry count


def _parse_dir(raw: bytes):
    count = struct.unpack_from("<I", raw, 0)[0]
    entries = []
    for i in range(count):
        off = HEADER_LEN + i * ENTRY_STRIDE
        name = raw[off:off + 16].rstrip(b"\x00").decode("latin-1", "replace")
        start, size = struct.unpack_from("<II", raw, off + 16)
        deleted = raw[off + 24]
        tail = (raw[off + 25], raw[off + 26], raw[off + 27])
        entries.append({
            "i": i, "name": name, "start": start, "size": size,
            "deleted": deleted, "tail": tail,
        })
    return entries


def _matched_entries(a, b):
    """Pair entries by (name, deleted-status, size). Loose: also try by index."""
    pairs = []
    used_b = set()
    for ea in a:
        match = None
        for j, eb in enumerate(b):
            if j in used_b:
                continue
            if eb["name"] == ea["name"] and eb["deleted"] == ea["deleted"]:
                match = (j, eb)
                break
        if match is not None:
            used_b.add(match[0])
            pairs.append((ea, match[1]))
        else:
            pairs.append((ea, None))
    for j, eb in enumerate(b):
        if j not in used_b:
            pairs.append((None, eb))
    return pairs


def _bytewise_diff(a: bytes, b: bytes, max_runs: int = 5):
    """Tiny bytewise diff for a single payload pair."""
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
    return runs[:max_runs], len(runs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("a")
    ap.add_argument("b")
    args = ap.parse_args()

    a = Path(args.a).read_bytes()
    b = Path(args.b).read_bytes()
    print(f"a: {args.a} ({len(a)} bytes)")
    print(f"b: {args.b} ({len(b)} bytes)")
    print()

    # Directory comparison
    dir_a = _parse_dir(a)
    dir_b = _parse_dir(b)
    print(f"directory: a={len(dir_a)} entries, b={len(dir_b)} entries")
    pairs = _matched_entries(dir_a, dir_b)
    same = 0
    differ = 0
    for ea, eb in pairs:
        if ea is None:
            print(f"  + only in b: idx={eb['i']:2d} name={eb['name']!r:24} "
                  f"start=0x{eb['start']:08x} size=0x{eb['size']:x} del={eb['deleted']}")
            differ += 1
            continue
        if eb is None:
            print(f"  - only in a: idx={ea['i']:2d} name={ea['name']!r:24} "
                  f"start=0x{ea['start']:08x} size=0x{ea['size']:x} del={ea['deleted']}")
            differ += 1
            continue
        if ea["size"] == eb["size"] and ea["start"] == eb["start"] and ea["tail"] == eb["tail"]:
            # Compare payloads
            pa = a[ea["start"]:ea["start"] + ea["size"]]
            pb = b[eb["start"]:eb["start"] + eb["size"]]
            if pa == pb:
                same += 1
                continue
            runs, total = _bytewise_diff(pa, pb)
            print(f"  ~ {ea['name']!r}: payload differs ({total} runs, "
                  f"first {len(runs)}: {[f'+{s}({l})' for s,l in runs]})")
            differ += 1
        else:
            print(f"  ! {ea['name']!r}: header differs "
                  f"a(start=0x{ea['start']:08x} size=0x{ea['size']:x} tail={ea['tail']}) "
                  f"b(start=0x{eb['start']:08x} size=0x{eb['size']:x} tail={eb['tail']})")
            differ += 1
    print(f"\n{same} entries identical, {differ} differ.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
