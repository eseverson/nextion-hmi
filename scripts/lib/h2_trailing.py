#!/usr/bin/env python3
"""h2_trailing.py — verify the H2 trailing-120-bytes invariant.

Hypothesis (see `findings/h2-trailing.md`): on F-series TFTs, the
trailing 120 bytes of the H2 plaintext (file offsets `[0x114..0x18c]`,
i.e. plaintext `H2[0x4c..0xc4]`) are constant `0xff` — they carry no
project-specific data despite appearing structured in ciphertext.

This script round-trips the corpus to prove it:

  1. Decrypt the H2 ciphertext of each fixture.
  2. Assert plaintext `[0x4c..0xc4]` is exactly `b"\\xff" * 120`.
  3. Re-encrypt `appinf1 || b"\\xff" * 120` and assert the result
     equals the stored ciphertext byte-for-byte.

If both assertions hold across the corpus, the H2 region is fully
reproducible from the 76-byte `appinf1` struct alone, no external
fingerprint data is needed for writing.

Usage: `python3 scripts/lib/h2_trailing.py [TFT_PATH ...]`. With no
arguments, walks the test corpus and the source project.
"""
from __future__ import annotations
import argparse
import glob
import os
import struct
import sys
from pathlib import Path


# Make `scripts.*` and `nextion.*` imports work from any cwd.
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib.h2_cipher import encrypt as h2_decrypt   # asm-verbatim DecData (decrypts)
from scripts.lib.h2_cipher import decrypt as h2_encrypt   # symmetric inverse (encrypts)
from scripts.lib.tft_format import (
    APPINF0_MODELCRC_OFF,
    APPINF1_SIZE,
    H2_END,
    H2_SIZE,
    H2_START,
)


TRAILING_OFF   = APPINF1_SIZE             # 0x4c in plaintext
TRAILING_SIZE  = H2_SIZE - APPINF1_SIZE   # 0x78 = 120
TRAILING_FILL  = b"\xff" * TRAILING_SIZE


def build_h2_plaintext(appinf1_bytes: bytes) -> bytes:
    """Compose a full 196-byte H2 plaintext from a 76-byte appinf1 struct.

    The trailing region is fixed `0xff` padding per `findings/h2-trailing.md`.
    """
    if len(appinf1_bytes) != APPINF1_SIZE:
        raise ValueError(
            f"appinf1 must be exactly {APPINF1_SIZE} bytes, got {len(appinf1_bytes)}"
        )
    return appinf1_bytes + TRAILING_FILL


def encrypt_h2(appinf1_bytes: bytes, model_crc: int) -> bytes:
    """Encrypt a full H2 plaintext (built from appinf1 + 0xff padding)."""
    return h2_encrypt(build_h2_plaintext(appinf1_bytes), model_crc)


def check_fixture(path: str) -> tuple[bool, bool, str]:
    """Decrypt H2 of one TFT and confirm the two invariants.

    Returns (trailing_is_0xff, recipher_matches_cipher, error_or_blank).
    """
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return (False, False, f"read failed: {e}")
    if len(data) < H2_END + 4:
        return (False, False, "file too short")
    mc = struct.unpack_from("<I", data, APPINF0_MODELCRC_OFF)[0]
    cipher = data[H2_START:H2_END]
    plain = h2_decrypt(cipher, mc)
    trail_is_ff = plain[TRAILING_OFF:] == TRAILING_FILL
    rebuilt = encrypt_h2(plain[:APPINF1_SIZE], mc)
    recipher_match = rebuilt == cipher
    return (trail_is_ff, recipher_match, "")


def default_fixtures() -> list[str]:
    """Walk the repo for every .tft we know about."""
    root = _REPO
    out: list[str] = []
    out.extend(sorted(glob.glob(str(root / "tests" / "editor outputs" / "*" / "*.tft"))))
    # Also include the canonical project tft.
    canonical = root / "source" / "nextion.hmi.tft"
    if canonical.exists():
        out.append(str(canonical))
    return [p for p in out if os.path.isfile(p)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "fixtures",
        nargs="*",
        help="TFT files to check. Defaults to the repo's test corpus.",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="suppress per-file output; only print summary",
    )
    args = parser.parse_args()

    fixtures = args.fixtures or default_fixtures()
    if not fixtures:
        print("no fixtures to check", file=sys.stderr)
        return 1

    print(f"checking {len(fixtures)} fixtures...")
    ok = 0
    failures: list[tuple[str, str]] = []
    for path in fixtures:
        name = os.path.basename(path)
        trail, match, err = check_fixture(path)
        success = (not err) and trail and match
        if success:
            ok += 1
        else:
            failures.append((path, err or f"trail=0xff:{trail}  recipher:{match}"))
        if not args.quiet:
            status = "OK " if success else "FAIL"
            extra = f"  ({err})" if err else ""
            print(f"  [{status}] {name:40s} trail=0xff:{str(trail):5s}  recipher:{match}{extra}")

    print()
    if failures:
        print(f"FAILED — {len(failures)} / {len(fixtures)} did not round-trip:")
        for p, why in failures:
            print(f"  {p}: {why}")
        return 1
    print(f"OK — {ok} / {len(fixtures)} fixtures match the `appinf1 || 0xff*120` invariant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
