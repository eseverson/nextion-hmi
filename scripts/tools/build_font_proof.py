#!/usr/bin/env python3
"""build_font_proof — produce a FLASHABLE proof TFT that embeds a
from-scratch-generated ``.zi`` font (me/miata-dash#39).

The goal of #39 (refined) is a ``.zi`` the DISPLAY renders correctly,
built from a TTF + pixel height + charset on Linux — NOT a byte-match of
the Nextion Editor's rasterizer.  This script is the hardware-verification
artifact: it swaps one (or more) of a reference TFT's fonts for a
:func:`resources_generator.build_zi_font` font and recompiles a complete,
sealed TFT via the byte-exact HMI→TFT compiler.  Flash the output on the
dash and eyeball the rendered text — that is the only way to confirm final
visual correctness (you cannot verify pixels headlessly, by design).

Usage:

    python3 scripts/tools/build_font_proof.py \
        --hmi source/nextion.hmi.HMI \
        --ref source/nextion.hmi.tft \
        --ttf /usr/share/fonts/liberation-serif-fonts/LiberationSerif-Regular.ttf \
        --font 0 \
        -o /tmp/font_proof.tft

Defaults reproduce the in-repo source project: it replaces font 0 (the
24-px ``liberiso-8859-1`` body font, used by most Text components) with a
generated Liberation-Serif font, keeping font 1 (48-px) untouched, so the
on-device difference is obvious where body text renders.

Flashing: the output ``.tft`` is a standard Nextion TFT.  Copy it to a
FAT32 SD card as a single ``*.tft`` and power-cycle the display, or push it
over the dash's WebUSB flasher (see me/miata-dash project_webusb_flasher —
flash the UF2 firmware first, then the TFT).  After flashing, navigate to a
page with body text; the glyphs are the generated font (visibly Liberation
Serif but with this generator's own anti-aliasing, not the editor's).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

_DEFAULT_TTF = ("/usr/share/fonts/liberation-serif-fonts/"
                "LiberationSerif-Regular.ttf")


def build_proof_tft(hmi_path: Path, ref_tft_path: Path, ttf_path: Path,
                    font_indices: list[int], charset="latin-1") -> bytes:
    """Build the complete sealed proof TFT (no file I/O)."""
    import tempfile
    import os
    from scripts.lib.resources_generator import (
        replace_fonts_in_resources_source, FontSwap,
    )
    from scripts.lib.compile_hmi import compile_hmi

    ref = ref_tft_path.read_bytes()
    swaps = [FontSwap(index=i, ttf_path=str(ttf_path)) for i in font_indices]
    patched_ref = replace_fonts_in_resources_source(ref, swaps)

    # compile_hmi copies the resources prefix (incl. our patched font
    # region) verbatim, then regenerates + seals everything downstream.
    tmp = tempfile.NamedTemporaryFile(suffix=".tft", delete=False)
    try:
        tmp.write(patched_ref)
        tmp.close()
        return compile_hmi(hmi_path, tmp.name, generate_resources=False)
    finally:
        os.unlink(tmp.name)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hmi", type=Path,
                    default=REPO_ROOT / "source" / "nextion.hmi.HMI",
                    help="source HMI project (default: in-repo source)")
    ap.add_argument("--ref", type=Path,
                    default=REPO_ROOT / "source" / "nextion.hmi.tft",
                    help="reference TFT supplying the resources prefix "
                         "(default: in-repo source)")
    ap.add_argument("--ttf", type=Path, default=Path(_DEFAULT_TTF),
                    help="TTF to rasterize the generated font from")
    ap.add_argument("--font", type=int, action="append", dest="fonts",
                    help="font index to replace (repeatable; default: 0)")
    ap.add_argument("-o", "--out", type=Path, default=Path("/tmp/font_proof.tft"),
                    help="output TFT path")
    args = ap.parse_args(argv)

    font_indices = args.fonts if args.fonts else [0]

    for p, what in [(args.hmi, "HMI"), (args.ref, "reference TFT"),
                    (args.ttf, "TTF")]:
        if not p.exists():
            print(f"error: {what} not found: {p}", file=sys.stderr)
            return 2

    out = build_proof_tft(args.hmi, args.ref, args.ttf, font_indices)
    args.out.write_bytes(out)

    # Re-extract the generated font from the sealed output as a sanity proof.
    from scripts.lib.tft_format import extract_zi_fonts
    fonts = extract_zi_fonts(out)
    print(f"wrote {args.out}  ({len(out)} bytes)")
    print(f"replaced font(s): {font_indices}  from {args.ttf.name}")
    for i in font_indices:
        f = fonts.get(i)
        if f is not None:
            print(f"  font {i}: height={f.height} name={f.name!r} "
                  f"glyphs={len(f._glyphs)}  (renders 'A': "
                  f"{f.has_glyph(ord('A'))})")
    print()
    print("FLASH THIS FILE:", args.out)
    print("  - SD card: copy as the only *.tft on a FAT32 card, power-cycle the display.")
    print("  - WebUSB:  flash the dash UF2 firmware first, then push this TFT")
    print("             (see project_webusb_flasher).")
    print("Final visual correctness is a HARDWARE check — headless rendering")
    print("cannot prove on-device pixels (that is by design for #39).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
