#!/usr/bin/env python3
"""nxt — unified Nextion project CLI.

Subcommands
-----------
  inspect-hmi   Structured dump of an HMI file (pages, components, fonts).
  inspect-tft   Structured dump of a TFT file (H1/H2 header fields).
  diff-hmi      Structured diff of two HMI files (directory + per-entry payloads).
  diff-tft      Structured diff of two TFT files (H1, H2, resources, usercode).
  diff-bytes    Generic per-byte diff of any two binary files.
  patch-hmi     Same-length byte patch inside a .pa page payload; reseals page CRC.
  patch-tft     Byte patch in H1, H2 (plaintext; re-encrypted), or raw body;
                reseals all three TFT CRCs.  The ONLY hardware-proven direct-TFT edit.
  resize-str    Resize a string attribute in an HMI page payload (HMI-first).
  hotspot       Add a page-navigation Hotspot to an HMI page by cloning m0
                (HMI-first; the safe structural-change path).
  list-tft      List every component in a TFT with inferred names.
  extract-pages Extract resource entries / RGB565 images from a TFT.
  preview       Render HMI pages to PNG previews.
  regen-schemas Regenerate lib/tft_attrs_schemas.py from findings/attrs-raw.txt.

Unsafe / experimental subcommands (hardware-invalid TFT splices; see me/nextion#1 #3):
  --unsafe add-hotspot-hmi   Add a Hotspot to an HMI page (old template approach;
                              superseded by `hotspot`).
  --unsafe add-hotspot-tft   Add a Hotspot directly to a TFT (structural splice;
                              hardware-rejected).
  --unsafe add-xfloat-tft    Add an XFloat directly to a TFT (structural splice;
                              hardware-rejected).

Safe path for structural display changes: edit the HMI source, then use the
Nextion Editor to compile to TFT (or a future `nxt compile` subcommand once
the from-scratch compiler is complete).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Subcommand implementations — thin dispatchers into the lib/tools modules.
# ---------------------------------------------------------------------------

def _cmd_inspect_hmi(args: argparse.Namespace) -> int:
    from scripts.tools.inspect_hmi import main as _main
    sys.argv = ["nxt inspect-hmi", args.hmi]
    if args.json:
        sys.argv.append("--json")
    return _main()


def _cmd_inspect_tft(args: argparse.Namespace) -> int:
    from scripts.tools.inspect_tft import main as _main
    sys.argv = ["nxt inspect-tft", args.tft]
    if args.json:
        sys.argv.append("--json")
    return _main()


def _cmd_diff_hmi(args: argparse.Namespace) -> int:
    from scripts.tools.diff_hmi import main as _main
    sys.argv = ["nxt diff-hmi", args.a, args.b]
    return _main()


def _cmd_diff_tft(args: argparse.Namespace) -> int:
    from scripts.tools.diff_tft import main as _main
    sys.argv = ["nxt diff-tft", args.a, args.b]
    if args.xor_h2:
        sys.argv.append("--xor-h2")
    return _main()


def _cmd_diff_bytes(args: argparse.Namespace) -> int:
    from scripts.tools.diff_bytes import main as _main
    sys.argv = ["nxt diff-bytes", args.a, args.b,
                "--window", str(args.window),
                "--max-runs", str(args.max_runs),
                "--coalesce", str(args.coalesce)]
    return _main()


def _cmd_patch_hmi(args: argparse.Namespace) -> int:
    from scripts.tools.patch_hmi import main as _main
    argv = ["nxt patch-hmi", args.input, "-o", args.output,
            "--page", str(args.page),
            "--offset", hex(args.offset)]
    if args.u32 is not None:
        argv += ["--u32", hex(args.u32)]
    elif args.u16 is not None:
        argv += ["--u16", hex(args.u16)]
    elif args.u8 is not None:
        argv += ["--u8", hex(args.u8)]
    elif args.bytes:
        argv += ["--bytes", args.bytes]
    elif args.str is not None:
        argv += ["--str", args.str]
    if args.verify:
        argv.append("--verify")
    sys.argv = argv
    return _main()


def _cmd_patch_tft(args: argparse.Namespace) -> int:
    from scripts.tools.patch_tft import main as _main
    argv = ["nxt patch-tft", args.input]
    if args.output:
        argv += ["-o", args.output]
    if args.h1_offset is not None:
        argv += ["--h1-offset", hex(args.h1_offset)]
    elif args.h2_offset is not None:
        argv += ["--h2-offset", hex(args.h2_offset)]
    elif args.raw_offset is not None:
        argv += ["--raw-offset", hex(args.raw_offset)]
    if args.u32 is not None:
        argv += ["--u32", hex(args.u32)]
    elif args.u16 is not None:
        argv += ["--u16", hex(args.u16)]
    elif args.u8 is not None:
        argv += ["--u8", hex(args.u8)]
    elif args.bytes:
        argv += ["--bytes", args.bytes]
    if args.verify:
        argv.append("--verify")
    sys.argv = argv
    return _main()


def _cmd_resize_str(args: argparse.Namespace) -> int:
    from scripts.tools.resize_str_hmi import main as _main
    sys.argv = ["nxt resize-str", args.input, "-o", args.output,
                "--page", str(args.page),
                "--offset", hex(args.offset),
                "--str", args.value]
    return _main()


def _cmd_hotspot(args: argparse.Namespace) -> int:
    from scripts.tools.add_nav_hotspot_hmi import main as _main
    sys.argv = ["nxt hotspot", args.input, "-o", args.output,
                "--page", str(args.page),
                "--target-page", str(args.target_page)]
    return _main()


def _cmd_list_tft(args: argparse.Namespace) -> int:
    from scripts.tools.list_components_tft import main as _main
    sys.argv = ["nxt list-tft", args.input]
    if args.page is not None:
        sys.argv += ["--page", str(args.page)]
    return _main()


def _cmd_extract_pages(args: argparse.Namespace) -> int:
    from scripts.tools.extract_pages import main as _main
    sys.argv = ["nxt extract-pages", "-i", args.input, "-o", args.output]
    if args.dump_bins:
        sys.argv.append("--dump-bins")
    return _main()


def _cmd_preview(args: argparse.Namespace) -> int:
    from scripts.tools.preview_page import main as _main
    sys.argv = ["nxt preview", "--hmi", args.hmi, "--out", args.out,
                "--scale", str(args.scale)]
    return _main()


def _cmd_regen_schemas(args: argparse.Namespace) -> int:
    from scripts.tools.regen_attrs_schemas import main as _main
    return _main()


# ---------------------------------------------------------------------------
# Unsafe subcommands (structural TFT splices — hardware-invalid).
# ---------------------------------------------------------------------------

def _unsafe_add_hotspot_hmi(args: argparse.Namespace) -> int:
    from scripts.tools.add_hotspot import main as _main
    sys.argv = ["nxt --unsafe add-hotspot-hmi", args.input, "-o", args.output,
                "--page", str(args.page),
                "--x", str(args.x), "--y", str(args.y),
                "--w", str(args.w), "--h", str(args.h)]
    if args.name:
        sys.argv += ["--name", args.name]
    return _main()


def _unsafe_add_hotspot_tft(args: argparse.Namespace) -> int:
    from scripts.tools.add_hotspot_tft import main as _main
    sys.argv = ["nxt --unsafe add-hotspot-tft", args.input, "-o", args.output,
                "--page", str(args.page),
                "--x", str(args.x), "--y", str(args.y),
                "--w", str(args.w), "--h", str(args.h)]
    return _main()


def _unsafe_add_xfloat_tft(args: argparse.Namespace) -> int:
    from scripts.tools.add_xfloat_tft import main as _main
    argv = ["nxt --unsafe add-xfloat-tft", args.input, "-o", args.output,
            "--page", str(args.page),
            "--x", str(args.x), "--y", str(args.y),
            "--w", str(args.w), "--h", str(args.h),
            "--bco", hex(args.bco), "--pco", hex(args.pco),
            "--font", str(args.font), "--val", str(args.val),
            "--sta", str(args.sta), "--style", str(args.style)]
    sys.argv = argv
    return _main()


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="nxt",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--unsafe", action="store_true",
                    help="Enable unsafe/experimental subcommands that produce "
                         "hardware-invalid TFT files (structural splices). "
                         "These are rejected by real NX4832F035 hardware. "
                         "Use `patch-tft --raw-offset` for proven in-place edits, "
                         "or edit HMI + recompile for structural changes.")
    sub = ap.add_subparsers(dest="command", metavar="SUBCOMMAND")

    # ---- inspect-hmi -------------------------------------------------------
    p = sub.add_parser("inspect-hmi", help="Dump HMI structure (pages, components, fonts).")
    p.add_argument("hmi", help="path to .HMI file")
    p.add_argument("--json", action="store_true", help="Emit JSON")

    # ---- inspect-tft -------------------------------------------------------
    p = sub.add_parser("inspect-tft", help="Dump TFT H1/H2 header fields.")
    p.add_argument("tft", help="path to .tft file")
    p.add_argument("--json", action="store_true", help="Emit JSON")

    # ---- diff-hmi ----------------------------------------------------------
    p = sub.add_parser("diff-hmi", help="Structured diff of two HMI files.")
    p.add_argument("a")
    p.add_argument("b")

    # ---- diff-tft ----------------------------------------------------------
    p = sub.add_parser("diff-tft", help="Structured diff of two TFT files.")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--xor-h2", action="store_true",
                   help="Print full XOR(a_h2, b_h2) for known-plaintext analysis.")

    # ---- diff-bytes --------------------------------------------------------
    p = sub.add_parser("diff-bytes", help="Generic per-byte diff of two binary files.")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--window", type=int, default=8, help="Context bytes (default: 8)")
    p.add_argument("--max-runs", type=int, default=1000)
    p.add_argument("--coalesce", type=int, default=4,
                   help="Merge runs separated by <=N matching bytes (default: 4)")

    # ---- patch-hmi ---------------------------------------------------------
    p = sub.add_parser("patch-hmi",
                       help="Same-length byte patch inside a .pa page payload.")
    p.add_argument("input", help="source .HMI")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--page", type=int, required=True, help="page id (e.g. 0 for 0.pa)")
    p.add_argument("--offset", type=lambda s: int(s, 0), required=True,
                   help="byte offset within the page payload")
    val = p.add_mutually_exclusive_group(required=True)
    val.add_argument("--u32", type=lambda s: int(s, 0))
    val.add_argument("--u16", type=lambda s: int(s, 0))
    val.add_argument("--u8", type=lambda s: int(s, 0))
    val.add_argument("--bytes", help="space-separated hex bytes")
    val.add_argument("--str", dest="str", help="ASCII string (no terminator added)")
    p.add_argument("--verify", action="store_true",
                   help="Re-verify all live page CRCs after patching.")

    # ---- patch-tft ---------------------------------------------------------
    p = sub.add_parser("patch-tft",
                       help="Byte patch in H1, H2 (plaintext), or raw TFT body; "
                            "reseals all CRCs.  --raw-offset is hardware-proven safe.")
    p.add_argument("input", help="source .tft")
    p.add_argument("-o", "--output", help="output .tft (omit to dry-run / --verify only)")
    region = p.add_mutually_exclusive_group()
    region.add_argument("--h1-offset", type=lambda s: int(s, 0),
                        help="byte offset within H1 plaintext (0..0xc3)")
    region.add_argument("--h2-offset", type=lambda s: int(s, 0),
                        help="byte offset within decrypted H2; re-encrypted after edit")
    region.add_argument("--raw-offset", type=lambda s: int(s, 0),
                        help="byte offset in the raw file past the header (>= 0x190). "
                             "Same-length in-place edits here are hardware-proven safe.")
    val = p.add_mutually_exclusive_group()
    val.add_argument("--u32", type=lambda s: int(s, 0))
    val.add_argument("--u16", type=lambda s: int(s, 0))
    val.add_argument("--u8", type=lambda s: int(s, 0))
    val.add_argument("--bytes", help="space-separated hex bytes")
    p.add_argument("--verify", action="store_true",
                   help="Verify CRCs of the input and exit (no edit needed).")

    # ---- resize-str --------------------------------------------------------
    p = sub.add_parser("resize-str",
                       help="Resize a string attribute in an HMI page payload "
                            "(HMI-first; reseals CRC and directory).")
    p.add_argument("input", help="source .HMI")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--page", type=int, required=True)
    p.add_argument("--offset", type=lambda v: int(v, 0), required=True,
                   help="value offset of the existing string in the page payload")
    p.add_argument("--str", dest="value", required=True, help="new string value")

    # ---- hotspot -----------------------------------------------------------
    p = sub.add_parser("hotspot",
                       help="Add a page-navigation Hotspot to an HMI page by "
                            "cloning m0 (HMI-first; the safe structural-change path).")
    p.add_argument("input", help="source .HMI")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--page", type=int, required=True,
                   help=".pa id of the page to add the hotspot to")
    p.add_argument("--target-page", type=int, required=True,
                   help="device page id the new hotspot navigates to")

    # ---- list-tft ----------------------------------------------------------
    p = sub.add_parser("list-tft",
                       help="List every component in a TFT with inferred names.")
    p.add_argument("input", help="source .tft")
    p.add_argument("--page", type=int, default=None,
                   help="restrict listing to this page index")

    # ---- extract-pages -----------------------------------------------------
    p = sub.add_parser("extract-pages",
                       help="Extract resource entries / RGB565 images from a TFT.")
    p.add_argument("-i", "--input",
                   default=str(REPO_ROOT / "source" / "nextion.hmi.tft"),
                   help="path to .tft file")
    p.add_argument("-o", "--output", default=str(REPO_ROOT / "work"),
                   help="output directory for PNGs")
    p.add_argument("--dump-bins", action="store_true",
                   help="also dump each resource entry as a raw .bin")

    # ---- preview -----------------------------------------------------------
    p = sub.add_parser("preview", help="Render HMI pages to PNG previews.")
    p.add_argument("--hmi", default=str(REPO_ROOT / "source" / "nextion.hmi.HMI"))
    p.add_argument("--out", default=str(REPO_ROOT / "work"))
    p.add_argument("--scale", type=int, default=1, help="Integer upscale factor")

    # ---- regen-schemas -----------------------------------------------------
    sub.add_parser("regen-schemas",
                   help="Regenerate lib/tft_attrs_schemas.py from "
                        "findings/attrs-raw.txt.")

    # ---- unsafe subcommands ------------------------------------------------
    p = sub.add_parser("add-hotspot-hmi",
                       help="[UNSAFE] Add a Hotspot to an HMI page using the old "
                            "template approach.  Use `hotspot` (clone-m0) instead. "
                            "Requires --unsafe.")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--page", type=int, default=0)
    p.add_argument("--x", type=int, default=0)
    p.add_argument("--y", type=int, default=0)
    p.add_argument("--w", type=int, default=60)
    p.add_argument("--h", type=int, default=60)
    p.add_argument("--name", default=None)

    p = sub.add_parser("add-hotspot-tft",
                       help="[UNSAFE] Add a Hotspot directly to a TFT (structural "
                            "splice; hardware-rejected by NX4832F035).  Requires --unsafe.")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--page", type=int, default=0)
    p.add_argument("--x", type=int, default=0)
    p.add_argument("--y", type=int, default=0)
    p.add_argument("--w", type=int, default=60)
    p.add_argument("--h", type=int, default=60)

    p = sub.add_parser("add-xfloat-tft",
                       help="[UNSAFE] Add an XFloat directly to a TFT (structural "
                            "splice; hardware-rejected by NX4832F035).  Requires --unsafe.")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--page", type=int, default=0)
    p.add_argument("--x", type=int, default=400)
    p.add_argument("--y", type=int, default=240)
    p.add_argument("--w", type=int, default=76)
    p.add_argument("--h", type=int, default=32)
    p.add_argument("--bco", type=lambda s: int(s, 0), default=0x2946)
    p.add_argument("--pco", type=lambda s: int(s, 0), default=0xFFFF)
    p.add_argument("--font", type=int, default=1)
    p.add_argument("--val", type=int, default=0)
    p.add_argument("--sta", type=int, default=1)
    p.add_argument("--style", type=int, default=4)

    return ap


_UNSAFE_CMDS = {"add-hotspot-hmi", "add-hotspot-tft", "add-xfloat-tft"}

_DISPATCH = {
    "inspect-hmi":    _cmd_inspect_hmi,
    "inspect-tft":    _cmd_inspect_tft,
    "diff-hmi":       _cmd_diff_hmi,
    "diff-tft":       _cmd_diff_tft,
    "diff-bytes":     _cmd_diff_bytes,
    "patch-hmi":      _cmd_patch_hmi,
    "patch-tft":      _cmd_patch_tft,
    "resize-str":     _cmd_resize_str,
    "hotspot":        _cmd_hotspot,
    "list-tft":       _cmd_list_tft,
    "extract-pages":  _cmd_extract_pages,
    "preview":        _cmd_preview,
    "regen-schemas":  _cmd_regen_schemas,
    # unsafe:
    "add-hotspot-hmi": _unsafe_add_hotspot_hmi,
    "add-hotspot-tft": _unsafe_add_hotspot_tft,
    "add-xfloat-tft":  _unsafe_add_xfloat_tft,
}


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()

    if args.command is None:
        ap.print_help()
        return 0

    if args.command in _UNSAFE_CMDS and not args.unsafe:
        print(
            f"ERROR: `{args.command}` produces hardware-invalid TFT files (structural\n"
            f"splice — rejected by real NX4832F035 hardware; see me/nextion#1 #3).\n"
            f"\n"
            f"  * For structural display changes: edit the HMI source and recompile\n"
            f"    via the Nextion Editor (or a future `nxt compile` once the from-\n"
            f"    scratch compiler covers your page).\n"
            f"  * For nav hotspot adds:  nxt hotspot  (HMI-first, hardware-safe).\n"
            f"  * For proven in-place TFT edits:  nxt patch-tft --raw-offset ...\n"
            f"\n"
            f"If you understand the risk and only need a simulator-valid file, pass\n"
            f"--unsafe before the subcommand name:\n"
            f"    nxt --unsafe {args.command} ...",
            file=sys.stderr,
        )
        return 2

    handler = _DISPATCH.get(args.command)
    if handler is None:
        ap.error(f"unknown command: {args.command!r}")
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
