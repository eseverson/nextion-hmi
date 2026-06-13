"""Byte-exact binattinf generation (work-item #2).

``scripts/lib/binattinf_generator`` GENERATES a component's 24-byte-record
block from its HMI geometry/attributes + the media allocator's
``objdatarampos``.  Whole-block byte-verification currently covers the
page+hotspot classes (lei 121 / 109) — i.e. pages 1 and 3 in the project
are generated end-to-end byte-exact.  The display-component classes
(XFloat/Text/Prog/Slider/Button/Timer/Variable) have their datafrom-attr
tables reversed but still need the att_changeid model + the txt_maxl/Sstr
long-string pairing before their blocks match (see findings/next-steps.md);
those are gated behind ``allow_unverified=True``.
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
    parse_appinf1_corrected, parse_page_directory,
)
from scripts.lib.h2_cipher import encrypt as h2_decrypt  # noqa: E402
from scripts.lib.binattinf_generator import (  # noqa: E402
    build_component_binattinf, VERIFIED_LEIS,
)
from scripts.lib.media_allocator import layout_from_hmi_page  # noqa: E402
from scripts.lib.hmi_model import load_hmi_pages  # noqa: E402

H2_START, H2_END = 0xC8, 0xC8 + 196
HMI_PATH = REPO_ROOT / "source" / "nextion.hmi.HMI"
TFT_PATH = REPO_ROOT / "source" / "nextion.hmi.tft"


def _load():
    data = TFT_PATH.read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[H2_START:H2_END], model_crc))
    return data, a1


def _page_binattinf_span(data, a1, pd, pi):
    strd = a1["strdataaddr"]
    start = pd[pi]["attdataaddr_rel"]
    end = pd[pi + 1]["attdataaddr_rel"] if pi + 1 < len(pd) else a1["pageadd"] - strd
    return data[strd + start:strd + end]


def _gen_page_binattinf(hmi_page):
    layout = layout_from_hmi_page(hmi_page)
    out = b""
    for ci, hc in enumerate(hmi_page.components):
        pa = hc.attrs
        x = 0 if ci == 0 else int(pa.get("x") or 0)
        y = 0 if ci == 0 else int(pa.get("y") or 0)
        w = int(pa.get("w") or (480 if ci == 0 else 0))
        h = int(pa.get("h") or (320 if ci == 0 else 0))
        out += build_component_binattinf(
            hc.lei, hc.comp_id, x, y, w, h,
            objdatarampos=layout.objdatarampos[ci],
            frompageid=hmi_page.page_id, fromobjid=ci,
        )
    return out


@pytest.mark.parametrize("page_idx", [1, 3])
def test_page_binattinf_byte_exact(page_idx):
    """Pages 1 & 3 (page + hotspot) binattinf blocks generate byte-for-byte
    from the HMI + media allocator (all classes byte-verified)."""
    data, a1 = _load()
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    hmi = load_hmi_pages(HMI_PATH)

    live = _page_binattinf_span(data, a1, pd, page_idx)
    gen = _gen_page_binattinf(hmi[page_idx])
    assert gen == live, (
        f"page {page_idx} binattinf mismatch (len gen={len(gen)} live={len(live)})"
    )


def test_verified_pages_only_use_verified_leis():
    """Pages 1 & 3 consist solely of byte-verified component classes."""
    hmi = load_hmi_pages(HMI_PATH)
    for pi in (1, 3):
        for hc in hmi[pi].components:
            assert hc.lei in VERIFIED_LEIS, (
                f"page {pi} has unverified lei {hc.lei}"
            )


def test_head_record_geometry_in_block():
    """The generated hotspot block carries its HMI geometry in the head
    records (x/y/w/h/endx/endy as literals)."""
    data, a1 = _load()
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    hmi = load_hmi_pages(HMI_PATH)
    layout = layout_from_hmi_page(hmi[1])
    # Hotspot is component index 1 on page 1, at (420, 260, 60, 60).
    hc = hmi[1].components[1]
    blk = build_component_binattinf(
        hc.lei, hc.comp_id,
        int(hc.attrs["x"]), int(hc.attrs["y"]),
        int(hc.attrs["w"]), int(hc.attrs["h"]),
        objdatarampos=layout.objdatarampos[1],
        frompageid=1, fromobjid=1,
    )
    # rec offsets for Hotspot: x@9, y@10, w@11, h@12, endx@13, endy@14.
    def memval(off):
        return struct.unpack_from("<i", blk, off * 24 + 4)[0]
    assert memval(9) == 420 and memval(10) == 260
    assert memval(11) == 60 and memval(12) == 60
    assert memval(13) == 420 + 60 - 1 and memval(14) == 260 + 60 - 1
