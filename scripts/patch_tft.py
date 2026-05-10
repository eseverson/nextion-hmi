#!/usr/bin/env python3
"""patch_tft.py — modify a byte range in an F-series TFT and reseal it.

Mirror of `patch_hmi.py` for the TFT side. Now that the H2 cipher is
cracked (see findings/R), every layer of TFT integrity can be recomputed:

    * H1 CRC (plaintext, bytewise CRC32-MPEG2 over file[0..0xc4])
    * H2 CRC (over the *ciphertext*, bytewise CRC32-MPEG2 over file[0xc8..0x18c])
    * trailing 4-byte CRC (over file[:-4], XOR'd with a 3-byte mask we
      preserve from the original file — see `tft_format.trailing_crc_mask`)

Supported edit regions:

    --h1-offset / --h1-bytes   modify a byte range in the H1 plaintext
    --h2-offset / --h2-bytes   modify a byte range in the *plaintext* H2
                               (the tool re-encrypts it before writing back)
    --raw-offset / --bytes     modify any byte range past the header
                               (resources / pages / fonts / strings).
                               Useful for tweaking page payloads without
                               touching the encrypted header.

Always recomputes all three CRCs after editing.

Examples:

    # Flip the orientation byte in H1 (offset 0x14 = guidire) to 0:
    scripts/patch_tft.py source.tft --h1-offset 0x14 --u8 0 -o flat.tft

    # Bump pageqyt by 1 in the encrypted H2 (struct offset 0x38):
    scripts/patch_tft.py source.tft --h2-offset 0x38 --u16 5 -o more_pages.tft

    # Verify CRCs round-trip on a sample TFT (no edit):
    scripts/patch_tft.py source.tft --verify
"""
from __future__ import annotations
import argparse
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.h2_cipher import encrypt as h2_decrypt, decrypt as h2_encrypt  # noqa: E402
from scripts.page_crc import crc32_bytewise                                  # noqa: E402
from scripts import tft_format                                                # noqa: E402


def _u32_le(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def parse_value_args(args: argparse.Namespace) -> bytes:
    """Convert the user's --u32/--u16/--u8/--bytes arg into the byte payload."""
    if args.u32 is not None:
        return struct.pack("<I", args.u32 & 0xFFFFFFFF)
    if args.u16 is not None:
        return struct.pack("<H", args.u16 & 0xFFFF)
    if args.u8 is not None:
        return struct.pack("<B", args.u8 & 0xFF)
    if args.bytes:
        return bytes(int(b, 16) for b in args.bytes.split())
    return b""


def reseal(raw: bytes) -> bytes:
    """Recompute every CRC layer in `raw` and return the resealed bytes.
    `raw` must already have the desired header/body content; this only
    fixes the integrity stamps."""
    out = bytearray(raw)
    # H1 CRC over bytes 0..0xc4 → store at 0xc4
    h1_crc = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H1_START:tft_format.H1_END]))
    out[tft_format.H1_CRC_OFF:tft_format.H1_CRC_OFF + 4] = _u32_le(h1_crc)
    # H2 CRC over the ciphertext at 0xc8..0x18c
    h2_crc = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H2_START:tft_format.H2_END]))
    out[tft_format.H2_CRC_OFF:tft_format.H2_CRC_OFF + 4] = _u32_le(h2_crc)
    return bytes(out)


def apply_trailing(raw: bytes, mask: int) -> bytes:
    """Apply the trailing 4-byte CRC (computed over file[:-4]) XORed
    with the preserved 3-byte mask."""
    body_crc = crc32_bytewise(0xFFFFFFFF, raw[:-4])
    out = bytearray(raw)
    out[-4:] = _u32_le(body_crc ^ mask)
    return bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="source .tft file")
    ap.add_argument("-o", "--output", help="output .tft path (omit to dry-run)")
    region = ap.add_mutually_exclusive_group()
    region.add_argument("--h1-offset", type=lambda s: int(s, 0),
                        help="byte offset within H1 plaintext (0..0xc3)")
    region.add_argument("--h2-offset", type=lambda s: int(s, 0),
                        help="byte offset within decrypted H2 (0..0xc3); "
                             "the tool re-encrypts after the edit")
    region.add_argument("--raw-offset", type=lambda s: int(s, 0),
                        help="byte offset in the raw file past the header (>= 0x190)")
    val = ap.add_mutually_exclusive_group()
    val.add_argument("--u32", type=lambda s: int(s, 0))
    val.add_argument("--u16", type=lambda s: int(s, 0))
    val.add_argument("--u8",  type=lambda s: int(s, 0))
    val.add_argument("--bytes", help="space-separated hex bytes, e.g. 'ce fa ed fe'")
    ap.add_argument("--verify", action="store_true",
                    help="just verify the CRCs of the input and exit")
    args = ap.parse_args()

    raw = Path(args.input).read_bytes()
    print(f"input: {args.input}  ({len(raw)} bytes)")

    # Verify-only mode: report all three CRCs and exit.
    if args.verify and args.h1_offset is None and args.h2_offset is None and args.raw_offset is None:
        h1_stored = struct.unpack_from("<I", raw, tft_format.H1_CRC_OFF)[0]
        h1_calc = crc32_bytewise(0xFFFFFFFF, raw[tft_format.H1_START:tft_format.H1_END])
        h2_stored = struct.unpack_from("<I", raw, tft_format.H2_CRC_OFF)[0]
        h2_calc = crc32_bytewise(0xFFFFFFFF, raw[tft_format.H2_START:tft_format.H2_END])
        info = tft_format.trailing_crc_mask(raw)
        body_calc = crc32_bytewise(0xFFFFFFFF, raw[:-4])
        print(f"  H1  CRC: stored=0x{h1_stored:08x}  calc=0x{h1_calc:08x}  {'OK' if h1_stored==h1_calc else 'MISMATCH'}")
        print(f"  H2  CRC: stored=0x{h2_stored:08x}  calc=0x{h2_calc:08x}  {'OK' if h2_stored==h2_calc else 'MISMATCH'}")
        print(f"  TAIL   : stored=0x{info.stored:08x}  body=0x{body_calc:08x}  mask=0x{info.mask:08x}")
        # Sanity: H2 round-trips through the cipher
        plain = h2_decrypt(raw[tft_format.H2_START:tft_format.H2_END], tft_format.read_model_crc(raw))
        cipher = h2_encrypt(plain, tft_format.read_model_crc(raw))
        rt = cipher == raw[tft_format.H2_START:tft_format.H2_END]
        print(f"  H2 round-trip: {'OK' if rt else 'MISMATCH'}")
        return 0 if h1_stored == h1_calc and h2_stored == h2_calc else 1

    if args.h1_offset is None and args.h2_offset is None and args.raw_offset is None:
        ap.error("specify --h1-offset, --h2-offset, or --raw-offset (or --verify alone)")
    if not args.output:
        ap.error("--output required when patching")

    payload = parse_value_args(args)
    if not payload:
        ap.error("specify --u32 / --u16 / --u8 / --bytes")

    # Preserve the trailing-CRC mask from the original.
    info = tft_format.trailing_crc_mask(raw)
    print(f"trailing-CRC mask preserved from input: 0x{info.mask:08x}")

    out = bytearray(raw)
    model_crc = tft_format.read_model_crc(raw)

    if args.h1_offset is not None:
        end = args.h1_offset + len(payload)
        if end > tft_format.H1_END:
            ap.error(f"H1 patch overflows: 0x{args.h1_offset:x}+{len(payload)} > 0x{tft_format.H1_END:x}")
        print(f"patching H1[0x{args.h1_offset:x}..0x{end:x}] = {payload.hex()}")
        out[args.h1_offset:end] = payload

    elif args.h2_offset is not None:
        # Decrypt, patch in plaintext, re-encrypt the whole H2 region.
        h2_plain = bytearray(h2_decrypt(bytes(out[tft_format.H2_START:tft_format.H2_END]), model_crc))
        end = args.h2_offset + len(payload)
        if end > tft_format.H2_SIZE:
            ap.error(f"H2 patch overflows: 0x{args.h2_offset:x}+{len(payload)} > 0x{tft_format.H2_SIZE:x}")
        print(f"patching H2-plain[0x{args.h2_offset:x}..0x{end:x}] = {payload.hex()}")
        h2_plain[args.h2_offset:end] = payload
        new_cipher = h2_encrypt(bytes(h2_plain), model_crc)
        out[tft_format.H2_START:tft_format.H2_END] = new_cipher

    else:  # --raw-offset
        if args.raw_offset < tft_format.RESOURCES_START:
            ap.error(f"--raw-offset must be >= 0x{tft_format.RESOURCES_START:x} "
                     f"(use --h1-offset / --h2-offset for header edits)")
        end = args.raw_offset + len(payload)
        if end > len(out) - 4:  # don't overwrite the trailing CRC
            ap.error(f"raw patch overflows file body")
        print(f"patching raw[0x{args.raw_offset:x}..0x{end:x}] = {payload.hex()}")
        out[args.raw_offset:end] = payload

    # Reseal H1+H2 CRCs, then trailing CRC.
    sealed = reseal(bytes(out))
    final = apply_trailing(sealed, info.mask)
    Path(args.output).write_bytes(final)
    print(f"wrote {args.output} ({len(final)} bytes)")

    # Self-verify after writing.
    h1_now = struct.unpack_from("<I", final, tft_format.H1_CRC_OFF)[0]
    h2_now = struct.unpack_from("<I", final, tft_format.H2_CRC_OFF)[0]
    body_now = crc32_bytewise(0xFFFFFFFF, final[:-4])
    tail_now = struct.unpack_from("<I", final, len(final) - 4)[0]
    print(f"  H1 CRC = 0x{h1_now:08x}")
    print(f"  H2 CRC = 0x{h2_now:08x}")
    print(f"  trailing = 0x{tail_now:08x}  (body 0x{body_now:08x} ^ mask 0x{info.mask:08x})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
