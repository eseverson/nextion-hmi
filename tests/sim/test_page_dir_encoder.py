"""Byte-exact page-directory generation (work-item #2).

``scripts/lib/page_dir_encoder`` GENERATES the HMI-derivable fields of the
page directory (``objstar`` = cumulative object count, ``objqyt`` = the
page's object count, ``res0`` = 0).  The three strdata-relative offset
fields (``hexpos`` / ``attdataaddr_rel`` / ``medatapos``) come from the
per-page media allocator + binattinf layout and are still sourced from
the TFT.  A green run proves the object-count framing is HMI-derived
byte-for-byte.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.lib.tft_attrs import (  # noqa: E402
    parse_appinf1_corrected, parse_page_directory,
)
from scripts.lib.h2_cipher import encrypt as h2_decrypt  # noqa: E402
from scripts.lib.page_dir_encoder import (  # noqa: E402
    encode_page_dir_entry, PageDirInputs, page_object_counts,
    PAGE_DIR_ENTRY_SIZE,
)
from scripts.lib.hmi_model import load_hmi_pages  # noqa: E402

H2_START, H2_END = 0xC8, 0xC8 + 196
HMI_PATH = REPO_ROOT / "source" / "nextion.hmi.HMI"
TFT_PATH = REPO_ROOT / "source" / "nextion.hmi.tft"


def _load():
    data = TFT_PATH.read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[H2_START:H2_END], model_crc))
    return data, a1


def test_object_counts_match_tft():
    data, a1 = _load()
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    hmi = load_hmi_pages(HMI_PATH)
    counts = [len(hp.components) for hp in hmi]
    derived = page_object_counts(counts)
    for (objstar, objqyt), p in zip(derived, pd):
        assert objstar == p["objstar"], f"objstar {objstar} != {p['objstar']}"
        assert objqyt == p["objqyt"], f"objqyt {objqyt} != {p['objqyt']}"


def test_page_directory_byte_exact():
    """The whole page directory reconstructs byte-for-byte: object counts
    HMI-derived, offsets fed from the TFT."""
    data, a1 = _load()
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    hmi = load_hmi_pages(HMI_PATH)
    counts = [len(hp.components) for hp in hmi]
    derived = page_object_counts(counts)

    out = bytearray()
    for (objstar, objqyt), p in zip(derived, pd):
        inputs = PageDirInputs(
            hexpos=p["hexpos"],
            attdataaddr_rel=p["attdataaddr_rel"],
            medatapos=p["medatapos"],
        )
        out += encode_page_dir_entry(objstar, objqyt, inputs)

    live = data[a1["pageadd"]:a1["pageadd"] + a1["pageqyt"] * PAGE_DIR_ENTRY_SIZE]
    assert bytes(out) == live, "page directory mismatch"
