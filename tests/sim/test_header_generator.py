"""Byte-exact usercode-header generation (drop the reference dependency).

``scripts/lib/header_generator`` generates the 0x9e-byte usercode header —
the global memory directory, the page name-hash table, and the MainCode
(compiled ``Program.s`` + the startup ``page N``) — from the HMI alone.
Verified byte-for-byte against ``source/nextion.hmi.tft``.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.lib.tft_attrs import parse_appinf1_corrected  # noqa: E402
from scripts.lib.h2_cipher import encrypt as h2_decrypt  # noqa: E402
from scripts.lib.hmi_model import load_hmi_pages  # noqa: E402
from scripts.lib.header_generator import (  # noqa: E402
    build_header, build_name_hash_table, build_maincode,
)
from sim.loader import load_hmi  # noqa: E402

H2_START, H2_END = 0xC8, 0xC8 + 196
HMI_PATH = REPO_ROOT / "source" / "nextion.hmi.HMI"
TFT_PATH = REPO_ROOT / "source" / "nextion.hmi.tft"
HEADER_END = 0x9E


def _load():
    data = TFT_PATH.read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[H2_START:H2_END], model_crc))
    return data, a1


def test_header_byte_exact():
    data, a1 = _load()
    strd = a1["strdataaddr"]
    hmi = load_hmi_pages(HMI_PATH)
    program_s = load_hmi(str(HMI_PATH)).program_s
    gen = build_header(hmi, program_s)
    live = data[strd:strd + HEADER_END]
    assert gen == live, "usercode header mismatch"


def test_name_hash_table_and_maincode():
    data, a1 = _load()
    strd = a1["strdataaddr"]
    hmi = load_hmi_pages(HMI_PATH)
    program_s = load_hmi(str(HMI_PATH)).program_s
    # MainCode starts at MainCodeHex (0x4c); name-hash between 0x34 and there.
    mch = a1["MainCodeHex"]
    assert build_maincode(program_s) == data[strd + mch:strd + HEADER_END]
    assert build_name_hash_table(hmi) == data[strd + 0x34:strd + mch]
