#!/usr/bin/env python3
"""inspect_tft.py — structured dump of an F-series TFT, including
partial H2 decryption using the half-key recovered in finding L.

Usage:
    scripts/inspect_tft.py source/nextion.hmi.tft
    scripts/inspect_tft.py path.tft --json > dump.json
"""
from __future__ import annotations
import argparse
import json
import struct
import sys
from pathlib import Path

# Recovered F-series 32-byte H2 cipher key (positions 0x00..0x0f).
# Per finding L. Positions 0x10..0x1f remain unknown.
H2_KEY_KNOWN = {
    0x00: 0x84, 0x01: 0xc6, 0x02: 0xf9, 0x03: 0x9d,
    0x04: 0x8e, 0x05: 0x32, 0x06: 0x0f, 0x07: 0x66,
    0x08: 0xde, 0x09: 0x95, 0x0a: 0x4c, 0x0b: 0x03,
    0x0c: 0xb9, 0x0d: 0x5b, 0x0e: 0x26, 0x0f: 0xd8,
}
H1_END = 0xC8
H2_END = 0x190


def decrypt_h2(h2_cipher: bytes) -> tuple[bytes, list[bool]]:
    """Decrypt H2 with the known 16/32 key bytes. Unknown positions
    return as 0xFF and have known=False."""
    out = bytearray()
    known = []
    for i, b in enumerate(h2_cipher):
        cycle = i % 32
        if cycle in H2_KEY_KNOWN:
            out.append(b ^ H2_KEY_KNOWN[cycle])
            known.append(True)
        else:
            out.append(0xFF)
            known.append(False)
    return bytes(out), known


def try_u32_le(data: bytes, known: list[bool], off: int) -> str:
    """Format a u32 if all 4 bytes are known, else show '??' for unknowns."""
    bs = []
    for i in range(4):
        if known[off + i]:
            bs.append(f"{data[off + i]:02x}")
        else:
            bs.append("??")
    if all(known[off:off + 4]):
        v = struct.unpack_from("<I", data, off)[0]
        return f"0x{v:08x}  ({' '.join(bs)})"
    return f"???????? ({' '.join(bs)})"


def try_u16_le(data: bytes, known: list[bool], off: int) -> str:
    bs = []
    for i in range(2):
        if known[off + i]:
            bs.append(f"{data[off + i]:02x}")
        else:
            bs.append("??")
    if all(known[off:off + 2]):
        v = struct.unpack_from("<H", data, off)[0]
        return f"0x{v:04x}  ({' '.join(bs)})"
    return f"???? ({' '.join(bs)})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tft")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    raw = Path(args.tft).read_bytes()
    if len(raw) < H2_END:
        print("file too short", file=sys.stderr)
        return 1

    h1 = raw[:H1_END]
    h2_cipher = raw[H1_END:H2_END]
    h2_plain, known = decrypt_h2(h2_cipher)

    # H1 fields (TFTTool schema is reliable for H1)
    magic = h1[:6]
    h1_fields = {
        "magic":               magic.hex(),
        "old_lcd_orientation": h1[0x00],
        "editor_vendor":       chr(h1[0x03]),
        "lcd_resolution_x":    struct.unpack_from("<H", h1, 0x10)[0],
        "lcd_resolution_y":    struct.unpack_from("<H", h1, 0x12)[0],
        "ui_orientation":      h1[0x14],
        "model_series":        h1[0x16],
        "model_crc":           struct.unpack_from("<I", h1, 0x2e)[0],
        "file_version":        h1[0x32],
        "file_size":           struct.unpack_from("<I", h1, 0x3c)[0],
        "h1_crc":              struct.unpack_from("<I", h1, 0xc4)[0],
    }

    # H2 partial decryption
    h2_partial = {
        "static_usercode_address?":     try_u32_le(h2_plain, known, 0x00),
        "app_attributes_data_address?": try_u32_le(h2_plain, known, 0x04),
        "ressources_files_address":     try_u32_le(h2_plain, known, 0x08),
        "usercode_address":             try_u32_le(h2_plain, known, 0x0c),
        "[unknown @ 0x10..0x1f]":       "?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ??",
        "videos_address":               try_u32_le(h2_plain, known, 0x20),
        "audios_address":               try_u32_le(h2_plain, known, 0x24),
        "fonts_address":                try_u32_le(h2_plain, known, 0x28),
        "unknown_maincode_binary":      try_u32_le(h2_plain, known, 0x2c),
    }

    if args.json:
        print(json.dumps({"h1": h1_fields, "h2_partial": h2_partial}, indent=2))
    else:
        print(f"TFT: {args.tft}  ({len(raw)} bytes)")
        print(f"  magic         : {h1_fields['magic']} (CN2E expected for Nextion)")
        print(f"  vendor        : {h1_fields['editor_vendor']!r}")
        print(f"  resolution    : {h1_fields['lcd_resolution_x']} x {h1_fields['lcd_resolution_y']}")
        print(f"  ui_orientation: 0x{h1_fields['ui_orientation']:02x}  "
              f"(0x00=90°, 0x01=0°/default, 0x03=180°)")
        print(f"  model_series  : {h1_fields['model_series']} "
              f"(0=T0, 1=K0, 2=X3, 3=X5, 100=T1/F)")
        print(f"  model_crc     : 0x{h1_fields['model_crc']:08x}")
        print(f"  file_version  : {h1_fields['file_version']}")
        print(f"  file_size     : {h1_fields['file_size']}  ({h1_fields['file_size']:#x})")
        print(f"  h1_crc        : 0x{h1_fields['h1_crc']:08x}")
        print()
        print(f"H2 (partial decrypt; positions 0x10..0x1f unknown):")
        for k, v in h2_partial.items():
            print(f"  {k:<28} {v}")
        # Sanity check
        if struct.unpack_from("<I", h2_plain, 0x08)[0] == 0x10000:
            print()
            print(f"  ✓ ressources_files_address decrypts to 0x10000 — F-series key matches")
        else:
            print()
            print(f"  ⚠ ressources_files_address didn't decrypt to 0x10000 — different cipher?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
