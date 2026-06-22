"""Round-trip tests for nextion/scripts/lib/main_hmi.py.

Loads known editor-saved main.HMI blobs, extracts their header fields and
resource lists, reconstructs each blob via ``write_main_hmi``, and asserts
byte-exact equality.  Four distinct stored CRCs are covered:

    0xe04ea294  source fixture (qty=6, guidire=1, upver1=0x43)
    0x018e08f0  live fixture   (qty=7, guidire=1, upver1=0x44)
    0x78639648  save_D fixture (qty=7, guidire=0, upver1=0x44)
    0x00f11c1c  minimal (23)   (qty=1, guidire=1, upver1=0x44)
"""
from __future__ import annotations
import struct
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Lazy import so the test can be collected without importing the module
# before sys.path is configured by the test runner.
def _import_lib():
    import sys
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.lib.main_hmi import write_main_hmi, verify, main_hmi_crc
    return write_main_hmi, verify, main_hmi_crc


def _get_main_hmi_blob(hmi_path: Path) -> bytes | None:
    """Extract the live main.HMI blob from an HMI file."""
    import sys
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.lib.hmi_dir import parse_directory
    data = hmi_path.read_bytes()
    for row in parse_directory(data):
        idx, name, start, size, deleted = row[0], row[1], row[2], row[3], row[4]
        if name == "main.HMI" and not deleted:
            return data[start : start + size]
    return None


def _extract_fields(blob: bytes) -> dict:
    """Pull the variable fields from a raw main.HMI blob."""
    qty = struct.unpack_from("<I", blob, 0x1C)[0]
    resources = []
    for i in range(qty):
        off = 0x60 + i * 16
        ext = blob[off : off + 8].rstrip(b"\x00").decode("ascii")
        name = blob[off + 8 : off + 16].rstrip(b"\x00").decode("ascii")
        resources.append((ext, name))
    return {
        "model_crc":      struct.unpack_from("<I", blob, 0x10)[0],
        "resources":      resources,
        "guidire":        blob[0x0C],
        "encode":         blob[0x0D],
        "upver0":         blob[0x08],
        "upver1":         blob[0x09],
        "picencodever":   blob[0x41],
    }


# ---------------------------------------------------------------------------
# Fixtures under test
# ---------------------------------------------------------------------------

_CASES = [
    # (label, path-relative-to-REPO_ROOT, expected-stored-CRC)
    ("source",  "source/nextion.hmi.HMI",                                0xE04EA294),
    ("live",    "../nextion.hmi.HMI",                                     0x018E08F0),
    ("save_D",  "tests/editor outputs/_old/save D.HMI",                  0x78639648),
    ("23min",   "tests/editor outputs/23_minimal_project/23.HMI",         0x00F11C1C),
]


@pytest.mark.parametrize("label,relpath,expected_crc", _CASES)
def test_write_main_hmi_roundtrip(label, relpath, expected_crc):
    """write_main_hmi reproduces the editor-saved blob byte-for-byte."""
    write_main_hmi, verify, main_hmi_crc = _import_lib()

    hmi_path = (REPO_ROOT / relpath).resolve()
    if not hmi_path.exists():
        pytest.skip(f"{label}: {hmi_path} not found")

    original = _get_main_hmi_blob(hmi_path)
    assert original is not None, f"no main.HMI blob found in {hmi_path}"

    stored = struct.unpack_from("<I", original, 0)[0]
    assert stored == expected_crc, (
        f"{label}: unexpected stored CRC 0x{stored:08x} (expected 0x{expected_crc:08x})"
    )

    fields = _extract_fields(original)
    reconstructed = write_main_hmi(**fields)

    assert reconstructed == original, (
        f"{label}: round-trip mismatch\n"
        f"  stored CRC:       0x{stored:08x}\n"
        f"  reconstructed CRC:0x{struct.unpack_from('<I', reconstructed, 0)[0]:08x}\n"
        f"  first diff byte:  {next((i for i in range(min(len(original),len(reconstructed))) if original[i]!=reconstructed[i]), 'lengths differ')}"
    )


def test_main_hmi_crc_verify():
    """verify() returns True for all known good blobs, False after corruption."""
    write_main_hmi, verify, main_hmi_crc = _import_lib()

    for label, relpath, expected_crc in _CASES:
        hmi_path = (REPO_ROOT / relpath).resolve()
        if not hmi_path.exists():
            continue
        blob = _get_main_hmi_blob(hmi_path)
        if blob is None:
            continue
        assert verify(blob), f"{label}: verify() failed on known-good blob"
        # Flip a body byte and confirm it fails
        corrupted = bytearray(blob)
        corrupted[0x20] ^= 0xFF
        assert not verify(bytes(corrupted)), f"{label}: verify() should fail on corrupted blob"
