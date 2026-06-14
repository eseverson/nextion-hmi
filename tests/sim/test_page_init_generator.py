"""Byte-exact page-init bytecode chain generation (work-item #4, gap 4c).

The page-init chain is the third section of each page's media blob (after
the name-hash table and the frame image) — the page pseudo-component's
compiled load handler.  Verified against ``source/nextion.hmi.tft``:

  * pages 1 & 3 (no scrolling text, no page events) — the constant
    ``[preamble][doevents][terminator]``;
  * page 2 (``codesload: h0.val=dim``) — ``[preamble][draw 2][compiled
    codesload with the GLOBAL binattinf record index][doevents][term]``.

Page 0 (the s0 marquee ``timerset`` + L-marker) is the remaining case.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

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
from scripts.lib.script_compiler import (  # noqa: E402
    compile_event_handler, CompileContext,
)
from scripts.lib.page_init_generator import (  # noqa: E402
    build_constant_page_init, build_page_init_with_codesload,
    build_page_init_with_timer,
)
from scripts.lib.tft_attrs_encoder import APP_ATT_NAMES  # noqa: E402

H2_START, H2_END = 0xC8, 0xC8 + 196
SENTINEL = 0xFFFFFFFF
HMI_PATH = REPO_ROOT / "source" / "nextion.hmi.HMI"
TFT_PATH = REPO_ROOT / "source" / "nextion.hmi.tft"


def _load():
    data = TFT_PATH.read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[H2_START:H2_END], model_crc))
    return data, a1


def _live_page_init(data, a1, pd, objs, pi, hmi):
    strd = a1["strdataaddr"]

    def evstart(p):
        po = objs[pd[p]["objstar"]:pd[p]["objstar"] + pd[p]["objqyt"]]
        return min(s for o in po
                   for s in struct.unpack_from("<6I", o.raw, 4) if s != SENTINEL)

    end = evstart(pi + 1) if pi + 1 < len(pd) else a1["staticstrBeg"]
    blob = data[strd + pd[pi]["hexpos"]:strd + end]
    layout = layout_from_hmi_page(hmi[pi])
    ht = build_page_name_hash_table(hmi[pi], layout.medatapos)
    return blob[len(ht) + layout.medatapos:]


def test_constant_page_init_pages_1_and_3():
    """Pages 1 & 3 page-init chains generate byte-for-byte (constant)."""
    data, a1 = _load()
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi = load_hmi_pages(HMI_PATH)
    for pi in (1, 3):
        live = _live_page_init(data, a1, pd, objs, pi, hmi)
        assert build_constant_page_init() == live, f"page {pi} page-init mismatch"


def test_page2_codesload_page_init():
    """Page 2's page-init (draw + compiled `h0.val=dim`) generates
    byte-for-byte, using the GLOBAL binattinf record index for h0.val."""
    data, a1 = _load()
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi = load_hmi_pages(HMI_PATH)

    # Global record index of h0.val = cumulative record count of pages 0,1
    # + h0.val's page-local index.
    strd = a1["strdataaddr"]
    counts = []
    for i in range(len(pd)):
        end = pd[i + 1]["attdataaddr_rel"] if i + 1 < len(pd) else a1["pageadd"] - strd
        counts.append((end - pd[i]["attdataaddr_rel"]) // 24)
    from scripts.lib.tft_attrs_encoder import APP_ATT_NAMES
    po = objs[pd[2]["objstar"]:pd[2]["objstar"] + pd[2]["objqyt"]]
    h0 = po[2]
    h0_val_global = sum(counts[:2]) + h0.slots[APP_ATT_NAMES.index("val")]

    ctx = CompileContext()
    ctx.component_offsets = {"h0.val": h0_val_global}
    body = compile_event_handler("h0.val=dim", ctx)
    gen = build_page_init_with_codesload(body)

    live = _live_page_init(data, a1, pd, objs, 2, hmi)
    assert gen == live, "page 2 page-init mismatch"


def test_page0_timer_page_init():
    """Page 0's page-init (the tm0 marquee `timerset` + L re-arm) generates
    byte-for-byte except the L-marker address, which is back-patched by the
    usercode assembler.  tim/en use the GLOBAL binattinf record indices."""
    data, a1 = _load()
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi = load_hmi_pages(HMI_PATH)

    po = objs[pd[0]["objstar"]:pd[0]["objstar"] + pd[0]["objqyt"]]
    tm0 = next(o for o in po if o.lei == 51)
    tim_g = tm0.slots[APP_ATT_NAMES.index("tim")]  # page 0 → global == local
    en_g = tm0.slots[APP_ATT_NAMES.index("en")]
    timer_comp = next(c for c in hmi[0].components if c.lei == 51)
    timer_id = int(timer_comp.attrs["id"])

    gen = build_page_init_with_timer(tim_g, en_g, timer_id)
    live = _live_page_init(data, a1, pd, objs, 0, hmi)

    # Everything matches except the 4-byte L-marker address (offsets 35-38
    # in the chain), which the assembler back-patches.
    diffs = [i for i in range(min(len(gen), len(live))) if gen[i] != live[i]]
    # The L address is `4c 20 03 <u32>`; locate the L marker and confirm the
    # only diffs are inside its u32 addr field.
    l_pos = gen.find(bytes.fromhex("4c2003"))
    assert l_pos != -1
    addr_range = set(range(l_pos + 3, l_pos + 7))
    assert all(d in addr_range for d in diffs), (
        f"unexpected page-0 page-init diffs outside the L addr: {diffs}"
    )
    # The reference L address is 0x0d90.
    assert struct.unpack_from("<I", live, l_pos + 3)[0] == 0x0D90
