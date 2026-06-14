"""Byte-exact event-region generation (work-item: generate the usercode
event region instead of copying it).

``scripts/lib/event_region_generator`` builds each component's event/init
chains from the HMI model + the per-page binattinf record table, resolving
every ``LOAD`` operand to the GLOBAL record index.  Verified against
``source/nextion.hmi.tft``:

  * pages 1 & 3 (page + hotspot) — whole event region byte-exact;
  * the init (slot 0) chains of XFloat / Prog / Text byte-exact;
  * the hotspot navigation + the page/auto-fill chains byte-exact.

Pages 0 & 2 still have un-wired chains (the Timer cascade, the Slider/
Button event handlers, the Text long-string scroll block) — tracked in
findings/next-steps.md.
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
from scripts.lib.hmi_model import load_hmi_pages  # noqa: E402
from scripts.lib.tft_attrs_encoder import APP_ATT_NAMES  # noqa: E402
from scripts.lib.usercode_assembler import assemble_event_region  # noqa: E402
from scripts.lib.event_region_generator import (  # noqa: E402
    build_component_chains, build_page_attr_offsets, page_overlap_refs,
)
from scripts.lib.media_blob_generator import page_record_counts  # noqa: E402

H2_START, H2_END = 0xC8, 0xC8 + 196
SENTINEL = 0xFFFFFFFF
HMI_PATH = REPO_ROOT / "source" / "nextion.hmi.HMI"
TFT_PATH = REPO_ROOT / "source" / "nextion.hmi.tft"


def _load():
    data = TFT_PATH.read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[H2_START:H2_END], model_crc))
    return data, a1


def _evstart(objs, pd, p):
    po = objs[pd[p]["objstar"]:pd[p]["objstar"] + pd[p]["objqyt"]]
    return min(s for o in po
               for s in struct.unpack_from("<6I", o.raw, 4) if s != SENTINEL)


def _build_page_event_region(data, a1, pd, objs, hmi, pi, cum):
    po = objs[pd[pi]["objstar"]:pd[pi]["objstar"] + pd[pi]["objqyt"]]
    base = cum[pi]
    page_bco = base + po[0].slots[APP_ATT_NAMES.index("bco")]
    attr_offs = build_page_attr_offsets(hmi[pi].components, po, base)
    orefs = page_overlap_refs(hmi[pi].components)
    comps = [
        build_component_chains(hc, list(o.slots), base, page_bco,
                               hmi[pi].page_id, attr_offs, orefs.get(hc.name))
        for hc, o in zip(hmi[pi].components, po)
    ]
    return assemble_event_region(comps)


@pytest.mark.parametrize("page_idx", [0, 1, 2, 3])
def test_event_region_byte_exact_pages_1_3(page_idx):
    """All 4 pages' whole event regions generate byte-for-byte.

    Page 0 exercises the Timer codestimer cascade (with S marker) + the
    s0<->m0 overlap cross-refresh; page 2 the Slider events + the Button
    press/release auto-logic + the user codesdown."""
    data, a1 = _load()
    strd = a1["strdataaddr"]
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi = load_hmi_pages(HMI_PATH)
    counts = page_record_counts(hmi)
    cum = [sum(counts[:i]) for i in range(len(counts) + 1)]

    gen = _build_page_event_region(data, a1, pd, objs, hmi, page_idx, cum)
    live = data[strd + _evstart(objs, pd, page_idx):strd + pd[page_idx]["hexpos"]]
    assert gen == live, f"page {page_idx} event region mismatch"


def test_xfloat_prog_text_init_chains_byte_exact():
    """The init (slot 0) chains of XFloat / Prog / Text generate
    byte-for-byte with the global-record-index resolver."""
    data, a1 = _load()
    strd = a1["strdataaddr"]
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi = load_hmi_pages(HMI_PATH)
    counts = page_record_counts(hmi)
    cum = [sum(counts[:i]) for i in range(len(counts) + 1)]

    po = objs[pd[0]["objstar"]:pd[0]["objstar"] + pd[0]["objqyt"]]
    base = cum[0]
    page_bco = base + po[0].slots[APP_ATT_NAMES.index("bco")]

    def chain(off):
        pos = off
        while True:
            ln = struct.unpack_from("<I", data, strd + pos)[0]
            if ln == 0 or ln > 0x400:
                break
            pos += 4 + ln
        return data[strd + off:strd + pos]

    for ci, lei in [(1, 59), (7, 106), (11, 116)]:  # x0, j0, t0
        hc = hmi[0].components[ci]
        o = po[ci]
        cc = build_component_chains(hc, list(o.slots), base, page_bco, 0)
        live = chain(struct.unpack_from("<6I", o.raw, 4)[0])
        assert cc.chains[0] == live, f"comp {hc.name} (lei {lei}) init mismatch"
