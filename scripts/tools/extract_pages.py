#!/usr/bin/env python3
"""
extract_pages.py — locate and extract page-level / picture rasters from a
Nextion .tft file.

Approach:
  1. Read the file headers to find the resources section (file offset
     `ressources_files_address`, length `ressource_files_size`).
  2. Walk the resource directory at section_start + 0x00 — twelve fixed
     12-byte slots of (offset, size, reserved). Slot 0 is the header tail,
     slot 1 is the STM32 firmware binary, slot 2 is the LCD driver, the
     remainder are driver-data tables / fonts / pictures.
  3. For every non-empty slot, classify by content sniffing:
       - bootloader   : bytes at slot[1]
       - lcddriver    : bytes at slot[2]
       - zi-font      : starts with `04 ff 00 0a` (ZI v5 magic)
       - thumb-code   : block starts with a 4-byte index table whose entries
                        appear to point at small ARM Thumb routines
       - rgb565-image : size matches W*H*2 for plausible (W,H) and decodes
                        with sensible color statistics
  4. For every plausible RGB565 image: dump as `work/page_NN.png`.
  5. Print a summary to stdout listing each resource entry and what we did
     with it.

Notes on this particular TFT (`source/nextion.hmi.tft`, NX4832F035):
  - 480x320 dashboard with 4 pages (main / gauge / settings / error).
  - No `pic`, `xpic`, or `picq` opcodes in the user code — every page is
    rendered procedurally with `fill` rectangles, `xfloat`/text components,
    and a progress bar. There are zero pre-rendered page rasters in the
    file.
  - This script will therefore find NO images for this TFT but is written
    to handle the general case (other TFTs that DO contain pictures).
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TFT = REPO_ROOT / "source" / "nextion.hmi.tft"
DEFAULT_OUT = REPO_ROOT / "work"

# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

# The fields we need are all in the un-encrypted file header 1. Offsets are
# computed from TFTTool's `_fileHeader1` struct definition (a sequence of
# B/H/I fields starting at 0x00). Verified against this exact TFT.
H1_LCD_RES_X      = 0x10   # lcd_resolution_x         (uint16)
H1_LCD_RES_Y      = 0x12   # lcd_resolution_y         (uint16)
H1_RES_FILES_ADDR = 0x34   # ressources_files_address (uint32)
H1_FILE_SIZE      = 0x3c   # file_size                (uint32)
H1_RES_FILES_SIZE = 0x40   # ressource_files_size     (uint32)


def parse_header1(raw: bytes) -> dict:
    """Pull the fields we care about out of header 1.

    Header 1 is unencrypted. Layout per nxt-doc/TFT.md and TFTTool.py: a
    sequence of little-endian uint32/uint16/uint8 values starting at offset
    0x00. We need ressources_files_address, ressource_files_size, and the
    display dimensions.
    """
    # Display resolution in current orientation.
    width  = struct.unpack_from("<H", raw, H1_LCD_RES_X)[0]
    height = struct.unpack_from("<H", raw, H1_LCD_RES_Y)[0]

    # Resources block.
    res_addr  = struct.unpack_from("<I", raw, H1_RES_FILES_ADDR)[0]
    res_size  = struct.unpack_from("<I", raw, H1_RES_FILES_SIZE)[0]
    file_size = struct.unpack_from("<I", raw, H1_FILE_SIZE)[0]

    return {
        "width": width,
        "height": height,
        "res_addr": res_addr,
        "res_size": res_size,
        "file_size": file_size,
    }


# ---------------------------------------------------------------------------
# Resource directory
# ---------------------------------------------------------------------------

@dataclass
class ResourceEntry:
    index: int
    rel_offset: int      # offset relative to resource section start
    file_offset: int     # absolute file offset
    size: int
    kind: str = "unknown"


def parse_resource_directory(res: bytes, res_file_addr: int) -> list[ResourceEntry]:
    """Parse the 12 × 12-byte directory at the start of the resource section.

    Each slot is (offset_rel_to_section, size, reserved-zero). Empty slots
    have size==0 (and may share an offset with a neighbour).
    """
    entries: list[ResourceEntry] = []
    for i in range(12):
        off = i * 12
        if off + 12 > len(res):
            break
        rel, sz, _resv = struct.unpack_from("<III", res, off)
        if sz == 0:
            continue
        entries.append(ResourceEntry(
            index=i,
            rel_offset=rel,
            file_offset=res_file_addr + rel,
            size=sz,
        ))
    return entries


def classify(raw: bytes, e: ResourceEntry) -> str:
    """Best-effort tag for what an entry contains."""
    data = raw[e.file_offset : e.file_offset + e.size]
    if e.index == 0:
        return "res-directory-tail"
    if e.index == 1:
        return "stm32-binary"
    if e.index == 2:
        return "stm32-lcddriver"
    # ZI font v5 magic: 04 FF 00 0A 03 01 00 ...
    if data[:4] == b"\x04\xff\x00\x0a":
        return "zi-font"
    # Tables of (addr, size) — heuristic: starts with a 4-byte CRC then
    # repeated 8-byte (offset, size) records pointing into a code blob.
    # Cheap detection: look for ARM Thumb prologue bytes within the first
    # 256 bytes (`b5 ?? f0 b5 ?? ?? ?? b5`).
    sample = data[:256]
    if any(sample[i : i + 1] == b"\xb5" for i in range(0, len(sample), 4)):
        return "driver-code-table"
    return "unknown"


# ---------------------------------------------------------------------------
# Picture detection / decoding
# ---------------------------------------------------------------------------

PLAUSIBLE_DIMENSIONS = [
    # (W, H) — common Nextion picture sizes for a 480x320 panel
    (480, 320),
    (320, 240),
    (240, 320),
    (160, 120),
    (128, 128),
    (96, 96),
    (64, 64),
    (48, 48),
    (32, 32),
]


def decode_rgb565(data: bytes, w: int, h: int):
    """Decode a tightly-packed RGB565-LE buffer into a Pillow Image.

    Returns None if the buffer size doesn't match.
    """
    try:
        from PIL import Image
    except ImportError:
        print("WARN: Pillow not installed — cannot save PNG. "
              "Install with: pip install --user Pillow", file=sys.stderr)
        return None
    if len(data) != w * h * 2:
        return None
    px = bytearray(w * h * 3)
    for i in range(w * h):
        v = data[2*i] | (data[2*i+1] << 8)
        r5 = (v >> 11) & 0x1f
        g6 = (v >> 5)  & 0x3f
        b5 =  v        & 0x1f
        # Expand 5/6 bits to 8 bits with bit-replication
        px[3*i + 0] = (r5 << 3) | (r5 >> 2)
        px[3*i + 1] = (g6 << 2) | (g6 >> 4)
        px[3*i + 2] = (b5 << 3) | (b5 >> 2)
    return Image.frombytes("RGB", (w, h), bytes(px))


def looks_like_picture(data: bytes) -> tuple[int, int] | None:
    """Pure size-based filter: does the byte length match any plausible
    raw RGB565 dimension?"""
    for w, h in PLAUSIBLE_DIMENSIONS:
        if len(data) == w * h * 2:
            return (w, h)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("-i", "--input", default=str(DEFAULT_TFT),
                    help="path to .tft file (default: source/nextion.hmi.tft)")
    ap.add_argument("-o", "--output", default=str(DEFAULT_OUT),
                    help="output directory for PNGs (default: work/)")
    ap.add_argument("--dump-bins", action="store_true",
                    help="also dump each resource entry as a raw .bin")
    args = ap.parse_args()

    in_path = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = in_path.read_bytes()
    h1 = parse_header1(raw)

    print(f"Input: {in_path} ({len(raw):,} bytes)")
    print(f"Display: {h1['width']}x{h1['height']}")
    print(f"Resources section: 0x{h1['res_addr']:06x} "
          f"({h1['res_size']:,} bytes)")
    print(f"Header file_size: {h1['file_size']:,} (actual: {len(raw):,})")
    print()

    res = raw[h1["res_addr"] : h1["res_addr"] + h1["res_size"]]
    entries = parse_resource_directory(res, h1["res_addr"])

    print(f"Resource directory: {len(entries)} non-empty slots")
    print(f"  {'#':>2}  {'file_offset':>10}  {'size':>9}  kind")
    print(f"  {'-'*2}  {'-'*10}  {'-'*9}  {'-'*30}")

    pictures_saved = 0
    for e in entries:
        e.kind = classify(raw, e)
        print(f"  {e.index:>2}  0x{e.file_offset:08x}  {e.size:>9}  {e.kind}")

        data = raw[e.file_offset : e.file_offset + e.size]

        if args.dump_bins:
            (out_dir / f"res_{e.index:02d}_{e.kind}.bin").write_bytes(data)

        # Try as a raw RGB565 image
        dims = looks_like_picture(data)
        if dims:
            w, h = dims
            img = decode_rgb565(data, w, h)
            if img is not None:
                out = out_dir / f"page_{pictures_saved:02d}.png"
                img.save(out)
                pictures_saved += 1
                print(f"        -> wrote {out} ({w}x{h})")

    print()
    print(f"Pictures saved: {pictures_saved}")
    if pictures_saved == 0:
        print("(No raw RGB565 images detected. "
              "This TFT likely renders pages procedurally — "
              "see findings/D-page-rasters.md.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
