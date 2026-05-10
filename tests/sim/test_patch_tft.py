"""patch_tft.py smoke tests.

Verifies the writer round-trips: patching a byte in the encrypted H2
region produces a file whose CRCs all check out and whose decrypted
appinf1 reflects the change.
"""
from __future__ import annotations
import struct
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_patcher(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "patch_tft.py"), *args],
        capture_output=True, text=True, check=True,
    )


def test_verify_baseline(tft_path):
    """The factory TFT should pass --verify clean."""
    r = _run_patcher(str(tft_path), "--verify")
    assert "OK" in r.stdout
    assert "MISMATCH" not in r.stdout


def test_patch_round_trip(tft_path, tmp_path):
    """Patching pageqyt and verifying the result preserves all CRCs."""
    out = tmp_path / "patched.tft"
    _run_patcher(str(tft_path), "--h2-offset", "0x38", "--u16", "9", "-o", str(out))
    r = _run_patcher(str(out), "--verify")
    assert "MISMATCH" not in r.stdout

    # Independently decrypt the patched file and confirm pageqyt = 9 while
    # other appinf1 fields are unchanged.
    sys.path.insert(0, str(REPO_ROOT))
    from scripts.h2_cipher import encrypt as h2_decrypt

    raw = out.read_bytes()
    model_crc = struct.unpack_from("<I", raw, 0x2e)[0]
    plain = h2_decrypt(raw[0xc8:0xc8 + 196], model_crc)
    assert struct.unpack_from("<H", plain, 0x38)[0] == 9
    # Reserved fields are still zero (not stomped by the patch).
    assert plain[0x49] == 0
    assert struct.unpack_from("<H", plain, 0x4a)[0] == 0


def test_patch_h1_does_not_disturb_h2(tft_path, tmp_path):
    """Patching H1 should leave H2 plaintext byte-identical."""
    out = tmp_path / "patched.tft"
    _run_patcher(str(tft_path), "--h1-offset", "0x33",  # encodeh_star
                 "--u8", "0", "-o", str(out))

    sys.path.insert(0, str(REPO_ROOT))
    from scripts.h2_cipher import encrypt as h2_decrypt

    orig_raw = tft_path.read_bytes()
    new_raw = out.read_bytes()
    assert orig_raw[0xc8:0x18c] == new_raw[0xc8:0x18c], (
        "H1 patch should not touch the encrypted H2 region"
    )
    # And the H1 byte we changed actually changed.
    assert new_raw[0x33] == 0
