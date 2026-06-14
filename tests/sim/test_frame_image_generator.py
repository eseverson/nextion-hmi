"""Byte-exact media-frame image generation (work-item #4, gap 4).

``scripts/lib/frame_image_generator`` serializes each page's media-frame
image — the per-object ``<Kind>_PARAM`` structs populated with authored HMI
values — from the HMI page model.  Verified byte-for-byte against
``source/nextion.hmi.tft`` for pages 0, 1, and 3 (covering Page, Hotspot,
XFloat, Prog, Text incl. the long-string spill, Timer, and Variable).

Page 2 currently mismatches by exactly 3 bytes (the first Text's reserved
tail `01 4629`); see findings/next-steps.md for the precise residual.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.lib.tft_attrs import (  # noqa: E402
    parse_appinf1_corrected, parse_page_directory, parse_objxinxi,
)
from scripts.lib.h2_cipher import encrypt as h2_decrypt  # noqa: E402
from scripts.lib.hmi_model import (  # noqa: E402
    load_hmi_pages, build_page_name_hash_table,
)
from scripts.lib.media_allocator import layout_from_hmi_page  # noqa: E402
from scripts.lib.frame_image_generator import (  # noqa: E402
    build_frame_image, build_object_block, build_page_block,
)

H2_START, H2_END = 0xC8, 0xC8 + 196
SENTINEL = 0xFFFFFFFF
HMI_PATH = REPO_ROOT / "source" / "nextion.hmi.HMI"
TFT_PATH = REPO_ROOT / "source" / "nextion.hmi.tft"


def _load():
    data = TFT_PATH.read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[H2_START:H2_END], model_crc))
    return data, a1


def _live_frame_image(data, a1, pd, objs, pi, hmi):
    strd = a1["strdataaddr"]

    def evstart(p):
        po = objs[pd[p]["objstar"]:pd[p]["objstar"] + pd[p]["objqyt"]]
        return min(s for o in po
                   for s in struct.unpack_from("<6I", o.raw, 4) if s != SENTINEL)

    end = evstart(pi + 1) if pi + 1 < len(pd) else a1["staticstrBeg"]
    blob = data[strd + pd[pi]["hexpos"]:strd + end]
    layout = layout_from_hmi_page(hmi[pi])
    ht = build_page_name_hash_table(hmi[pi], layout.medatapos)
    return blob[len(ht):len(ht) + layout.medatapos]


@pytest.mark.parametrize("page_idx", [0, 1, 3])
def test_frame_image_byte_exact(page_idx):
    """Pages 0/1/3 frame images generate byte-for-byte from the HMI."""
    data, a1 = _load()
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi = load_hmi_pages(HMI_PATH)

    live = _live_frame_image(data, a1, pd, objs, page_idx, hmi)
    gen = build_frame_image(hmi[page_idx])
    assert gen == live, (
        f"page {page_idx} frame image mismatch (gen={len(gen)} live={len(live)})"
    )


def test_slider_block_byte_exact():
    """A Slider block serializes byte-for-byte from its authored attrs."""
    data, a1 = _load()
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi = load_hmi_pages(HMI_PATH)

    live = _live_frame_image(data, a1, pd, objs, 2, hmi)
    # page block 16 + slider at 16..44.
    h0 = hmi[2].components[2]
    assert h0.lei == 1
    gen = build_object_block(1, h0.attrs)
    assert gen == live[16:44]


def test_page_block_size_formula():
    """Page block size = 8 + 4*ceil(num_objects/4)."""
    hmi = load_hmi_pages(HMI_PATH)
    import math
    for hp in hmi:
        blk = build_page_block(len(hp.components), hp.page_attrs)
        assert len(blk) == 8 + 4 * math.ceil(len(hp.components) / 4)
