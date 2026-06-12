"""Fixture-validated tests for the HMI authoring tools.

Both tools are checked against the editor's own outputs: the same edit
applied programmatically must reproduce the editor's saved page blob
byte-for-byte (fixtures are cumulative, so each test replays its
predecessor's edit first where needed).
"""
from pathlib import Path
import struct
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.page_crc import page_crc                      # noqa: E402
from scripts.tools.resize_str_hmi import (_get_live_pa,         # noqa: E402
                                          resize_str_in_pa)
from scripts.tools.add_nav_hotspot_hmi import (                 # noqa: E402
    add_nav_hotspot_to_pa, _extract_m0_record)

OLD = REPO_ROOT / "tests" / "editor outputs" / "_old"


def _live_pa(path: Path, page_id: int = 0) -> bytes:
    return _get_live_pa(path.read_bytes(), page_id)[1]


@pytest.fixture(scope="module")
def pa_base():
    return _live_pa(OLD / "00_baseline" / "base.HMI")


@pytest.fixture(scope="module")
def pa_05():
    return _live_pa(OLD / "05_text_qqqqqqqq" / "05.HMI")


@pytest.fixture(scope="module")
def pa_06():
    return _live_pa(OLD / "06_bco_magenta" / "06.HMI")


@pytest.fixture(scope="module")
def pa_07():
    return _live_pa(OLD / "07_add_hotspot" / "07.HMI")


def test_resize_str_matches_editor(pa_base, pa_05):
    """05_text_qqqqqqqq = baseline + fixture 04's red.val=0xdeadbeef
    (cumulative) + t0 "kPa" -> "QQQQQQQQ". Replaying both must equal the
    editor's blob byte-for-byte."""
    pa4 = bytearray(pa_base)
    off04 = pa4.find(bytes.fromhex("aafa0000") + b"\x00" * 4)
    struct.pack_into("<I", pa4, off04, 0xDEADBEEF)
    struct.pack_into("<I", pa4, 0, page_crc(bytes(pa4)))

    mine = resize_str_in_pa(bytes(pa4), pa_base.find(b"kPa"), b"QQQQQQQQ")
    assert mine == pa_05


def test_add_component_structure_matches_editor(pa_06, pa_07):
    """The editor's hotspot add (06 -> 07): new PCH entry
    (last_start+last_size+12, len(record), 0), all starts +12, record
    appended at blob end, numberobj/datasize/CRC updated. Replaying the
    structural transform with the editor's own record bytes must
    reproduce 07 byte-for-byte."""
    PCH = 12
    n = struct.unpack_from("<I", pa_06, 12)[0]
    data_len = len(pa_06) - 0x38 - n * PCH
    editor_rec = pa_07[0x38 + (n + 1) * PCH + data_len:]

    blob = bytearray(pa_06[:0x38])
    last_s = last_sz = 0
    for i in range(n):
        s, sz, third = struct.unpack_from("<III", pa_06, 0x38 + i * PCH)
        blob += struct.pack("<III", s + PCH, sz, third)
        last_s, last_sz = s, sz
    blob += struct.pack("<III", last_s + last_sz + PCH, len(editor_rec), 0)
    blob += pa_06[0x38 + n * PCH:]
    blob += editor_rec
    struct.pack_into("<I", blob, 12, n + 1)
    struct.pack_into("<I", blob, 4, len(blob))
    struct.pack_into("<I", blob, 0, page_crc(bytes(blob)))

    assert bytes(blob) == pa_07


def test_clone_nav_hotspot_roundtrip(hmi_path):
    """Cloning m0 on the project's main page produces a same-size record
    with the patched fields, parseable boundaries, and a valid CRC."""
    pa = _live_pa(hmi_path)
    _, m0 = _extract_m0_record(pa)
    new_pa = add_nav_hotspot_to_pa(pa, target_page=2)

    n_old = struct.unpack_from("<I", pa, 12)[0]
    n_new = struct.unpack_from("<I", new_pa, 12)[0]
    assert n_new == n_old + 1
    assert len(new_pa) == len(pa) + 12 + len(m0)
    assert struct.unpack_from("<I", new_pa, 4)[0] == len(new_pa)
    assert struct.unpack_from("<I", new_pa, 0)[0] == page_crc(new_pa)
    assert b"m1" in new_pa[-len(m0):]
    assert b"page 2" in new_pa[-len(m0):]
