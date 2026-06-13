"""Per-page media-frame allocator (the keystone for work-item #2/#3).

``scripts/lib/media_allocator`` reproduces, from the HMI page model alone,
every object's ``objdatarampos`` and the page's ``medatapos`` — the
editor's per-page private-memory frame layout.  Verified byte-for-byte
against ``source/nextion.hmi.tft`` for all 4 pages:

  * frame start = 16 + 4*ceil(num_objects/4)
  * each object advances the cursor by SIZEOF_OBJDATA_T1[lei] (4-aligned)
  * Text/Button/Button_T `txt` that overflows its inline buffer spills
    round4(len+1) bytes after the block
  * medatapos = frame_end - 16

This unblocks generating the objxinxi ``objdatarampos`` field, the
binattinf record ``objdatarampos`` values, the page-dir ``medatapos``, and
the media-blob name-hash terminator.
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
from scripts.lib.media_allocator import (  # noqa: E402
    layout_from_hmi_page, frame_start, FRAME_PREFIX,
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


@pytest.mark.parametrize("page_idx", [0, 1, 2, 3])
def test_objdatarampos_reproduced(page_idx):
    """Every object's objdatarampos matches the TFT objxinxi entry."""
    data, a1 = _load()
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi = load_hmi_pages(HMI_PATH)

    p = pd[page_idx]
    page_objs = objs[p["objstar"]:p["objstar"] + p["objqyt"]]
    layout = layout_from_hmi_page(hmi[page_idx])

    assert len(layout.objdatarampos) == len(page_objs)
    for got, o in zip(layout.objdatarampos, page_objs):
        assert got == o.objdatarampos, (
            f"page {page_idx} obj lei={o.lei}: objdatarampos {got:#x} "
            f"!= {o.objdatarampos:#x}"
        )


@pytest.mark.parametrize("page_idx", [0, 1, 2, 3])
def test_medatapos_reproduced(page_idx):
    """The derived medatapos matches the TFT page directory field."""
    data, a1 = _load()
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    hmi = load_hmi_pages(HMI_PATH)

    layout = layout_from_hmi_page(hmi[page_idx])
    assert layout.medatapos == pd[page_idx]["medatapos"], (
        f"page {page_idx} medatapos {layout.medatapos} != "
        f"{pd[page_idx]['medatapos']}"
    )


def test_frame_start_formula():
    """Spot-check the frame-start formula at the page object counts."""
    assert frame_start(2) == FRAME_PREFIX + 4   # ceil(2/4)=1
    assert frame_start(5) == FRAME_PREFIX + 8    # ceil(5/4)=2
    assert frame_start(30) == FRAME_PREFIX + 32  # ceil(30/4)=8
