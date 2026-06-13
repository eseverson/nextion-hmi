"""HMI-driven event-region assembly (work-item #1).

These tests prove the **HMI** — not the TFT — supplies the *structure* of
the usercode event region: the page order (device-id), the per-page
component order, each component's ``lei`` (type), and therefore which
event/init slots the assembler emits and where the event-NAME strings
land.  The chain *bodies* are still recovered from the verified TFT (their
``LOAD`` operand addresses depend on the per-page attribute-record table
that work-item #2 generates), but everything *around* them is driven by
``scripts/lib/hmi_model.load_hmi_pages``.

If the HMI's component order / types diverged from the TFT's objxinxi
layout, the assembled region would no longer byte-match — so a green run
here is evidence the HMI model is a faithful structural source.
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
from scripts.lib.usercode_assembler import (  # noqa: E402
    assemble_event_region, assemble_usercode_region, PageBlocks,
)
from scripts.lib import tft_format  # noqa: E402
from scripts.lib.page_crc import crc32_bytewise  # noqa: E402
from scripts.lib.hmi_model import (  # noqa: E402
    load_hmi_pages, build_compiled_components, hmi_first_chain_source,
    compile_slot_from_hmi, build_appallvas, parse_program_globals,
)
from sim.loader import load_hmi  # noqa: E402

H2_START, H2_END = 0xC8, 0xC8 + 196
SENTINEL = 0xFFFFFFFF
HEADER_END = 0x9E
HMI_PATH = REPO_ROOT / "source" / "nextion.hmi.HMI"


def _load(tft_path: Path):
    data = tft_path.read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[H2_START:H2_END], model_crc))
    return data, a1


def _chain_body(data: bytes, strd: int, slot_off: int) -> bytes:
    pos = slot_off
    while True:
        ln = struct.unpack_from("<I", data, strd + pos)[0]
        if ln == 0 or ln > 0x400:
            break
        pos += 4 + ln
    return data[strd + slot_off:strd + pos]


def _tft_chain_source(data, strd, pd, objs):
    """Build a chain_source callback keyed by (page_index, comp_index,
    slot) that returns the verified TFT chain body for that slot.

    The page/component *indexing* matches the HMI model's ordering (page
    pseudo-component at index 0, user components after), so a correct HMI
    model indexes straight into the TFT's objxinxi entries."""
    # Per page: list of objxinxi entries in order (page pseudo first).
    per_page_objs = []
    for p in pd:
        per_page_objs.append(objs[p["objstar"]:p["objstar"] + p["objqyt"]])

    def source(page_index: int, comp_index: int, slot: int) -> bytes:
        o = per_page_objs[page_index][comp_index]
        slots = struct.unpack_from("<6I", o.raw, 4)
        off = slots[slot]
        if off == SENTINEL:
            return b""
        return _chain_body(data, strd, off)

    return source


def _build_pages_from_hmi(data, a1, hmi_first=False):
    strd = a1["strdataaddr"]
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi_pages = load_hmi_pages(HMI_PATH)
    src = _tft_chain_source(data, strd, pd, objs)
    if hmi_first:
        # Compile the HMI-compilable slots (hotspot navigation) directly
        # from the HMI script; everything else falls back to the TFT body.
        src = hmi_first_chain_source(hmi_pages, src)

    pages: list[PageBlocks] = []
    for pi, (hp, pdir) in enumerate(zip(hmi_pages, pd)):
        comps = build_compiled_components(hp, pi, src)
        if pi + 1 < len(pd):
            np = pd[pi + 1]
            np_objs = objs[np["objstar"]:np["objstar"] + np["objqyt"]]
            media_end = min(
                s for o in np_objs
                for s in struct.unpack_from("<6I", o.raw, 4) if s != SENTINEL
            )
        else:
            media_end = a1["staticstrBeg"]
        media = data[strd + pdir["hexpos"]:strd + media_end]
        pages.append(PageBlocks(components=comps, media=media))
    return hmi_pages, pd, objs, pages


def test_hmi_model_component_order_matches_tft():
    """The HMI's per-page component list (page pseudo + user components)
    matches the TFT objxinxi layout one-for-one in count and ``lei``."""
    data, a1 = _load(REPO_ROOT / "source" / "nextion.hmi.tft")
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi_pages = load_hmi_pages(HMI_PATH)

    assert len(hmi_pages) == a1["pageqyt"]
    for hp, p in zip(hmi_pages, pd):
        page_objs = objs[p["objstar"]:p["objstar"] + p["objqyt"]]
        assert len(hp.components) == len(page_objs), (
            f"page {hp.page_id} component count {len(hp.components)} != "
            f"objxinxi {len(page_objs)}"
        )
        for hc, o in zip(hp.components, page_objs):
            assert hc.lei == o.lei, (
                f"page {hp.page_id} comp {hc.name}: lei {hc.lei} != {o.lei}"
            )


@pytest.mark.parametrize("page_idx", [0, 1, 2, 3])
def test_hmi_driven_event_region_byte_exact(page_idx):
    """Each page's event-block region re-assembles byte-for-byte when the
    component order/lei/slot-set is driven from the HMI model."""
    data, a1 = _load(REPO_ROOT / "source" / "nextion.hmi.tft")
    strd = a1["strdataaddr"]
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])

    hmi_pages, _, _, pages = _build_pages_from_hmi(data, a1)
    got = assemble_event_region(pages[page_idx].components)

    p = pd[page_idx]
    page_objs = objs[p["objstar"]:p["objstar"] + p["objqyt"]]
    rstart = min(
        s for o in page_objs
        for s in struct.unpack_from("<6I", o.raw, 4) if s != SENTINEL
    )
    live = data[strd + rstart:strd + p["hexpos"]]
    assert got == live, (
        f"page {page_idx} HMI-driven event region mismatch: "
        f"got len={len(got)} live len={len(live)}"
    )


def test_hmi_driven_whole_file_round_trip_byte_identical():
    """The whole file round-trips byte-identically with the usercode
    event region driven by the HMI model (component order + lei from the
    HMI; chain bodies + media + tail from the verified TFT)."""
    data, a1 = _load(REPO_ROOT / "source" / "nextion.hmi.tft")
    strd = a1["strdataaddr"]
    pageadd = a1["pageadd"]

    _, _, _, pages = _build_pages_from_hmi(data, a1)
    header = data[strd:strd + HEADER_END]
    tail = data[strd + a1["staticstrBeg"]:pageadd]
    usercode = assemble_usercode_region(header, pages, tail)

    out = bytearray(data)
    out[strd:pageadd] = usercode
    assert len(out) == len(data)

    h1 = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H1_START:tft_format.H1_END]))
    out[tft_format.H1_CRC_OFF:tft_format.H1_CRC_OFF + 4] = struct.pack("<I", h1)
    h2 = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H2_START:tft_format.H2_END]))
    out[tft_format.H2_CRC_OFF:tft_format.H2_CRC_OFF + 4] = struct.pack("<I", h2)
    info = tft_format.trailing_crc_mask(data)
    body = crc32_bytewise(0xFFFFFFFF, bytes(out[:-4]))
    out[-4:] = struct.pack("<I", (body ^ info.mask) & 0xFFFFFFFF)

    assert bytes(out) == data, "HMI-driven whole-file round-trip not byte-identical"


def test_hotspot_navigation_compiles_from_hmi():
    """Each Hotspot's release-slot navigation bytecode is GENERATED from
    the HMI ``codesdown`` script and matches the live TFT slot-2 chain
    byte-for-byte (no attribute table needed — only the ``page`` opcode)."""
    data, a1 = _load(REPO_ROOT / "source" / "nextion.hmi.tft")
    strd = a1["strdataaddr"]
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi_pages = load_hmi_pages(HMI_PATH)

    per_page_objs = [objs[p["objstar"]:p["objstar"] + p["objqyt"]] for p in pd]
    found = 0
    for pi, hp in enumerate(hmi_pages):
        for ci, hc in enumerate(hp.components):
            if hc.lei != 109:
                continue
            generated = compile_slot_from_hmi(hc, 2)
            assert generated is not None, f"page {pi} hotspot {hc.name}: no nav compiled"
            o = per_page_objs[pi][ci]
            slot2 = struct.unpack_from("<6I", o.raw, 4)[2]
            assert slot2 != SENTINEL
            live = _chain_body(data, strd, slot2)
            assert generated == live, (
                f"page {pi} hotspot {hc.name}: generated nav {generated.hex()} "
                f"!= live {live.hex()}"
            )
            found += 1
    assert found == 4, f"expected 4 hotspots, checked {found}"


def test_hmi_first_whole_file_round_trip_byte_identical():
    """Whole-file round-trip stays byte-identical with the hotspot
    navigation slots GENERATED from the HMI (not copied from the TFT)."""
    data, a1 = _load(REPO_ROOT / "source" / "nextion.hmi.tft")
    strd = a1["strdataaddr"]
    pageadd = a1["pageadd"]

    _, _, _, pages = _build_pages_from_hmi(data, a1, hmi_first=True)
    header = data[strd:strd + HEADER_END]
    tail = data[strd + a1["staticstrBeg"]:pageadd]
    usercode = assemble_usercode_region(header, pages, tail)

    out = bytearray(data)
    out[strd:pageadd] = usercode

    h1 = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H1_START:tft_format.H1_END]))
    out[tft_format.H1_CRC_OFF:tft_format.H1_CRC_OFF + 4] = struct.pack("<I", h1)
    h2 = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H2_START:tft_format.H2_END]))
    out[tft_format.H2_CRC_OFF:tft_format.H2_CRC_OFF + 4] = struct.pack("<I", h2)
    info = tft_format.trailing_crc_mask(data)
    body = crc32_bytewise(0xFFFFFFFF, bytes(out[:-4]))
    out[-4:] = struct.pack("<I", (body ^ info.mask) & 0xFFFFFFFF)

    assert bytes(out) == data, "HMI-first round-trip not byte-identical"


def test_appallvas_generated_byte_exact():
    """The AppAllvas global-int table is GENERATED from the HMI's
    Program.s and matches the live TFT region byte-for-byte."""
    data, a1 = _load(REPO_ROOT / "source" / "nextion.hmi.tft")
    strd = a1["strdataaddr"]
    sb = a1["staticstrBeg"]
    ad = a1["attdataaddr"]   # strd-relative; first page's attdata start
    live = data[strd + sb:strd + ad]

    st = load_hmi(str(HMI_PATH))
    gen = build_appallvas(st.program_s)
    assert gen == live, f"AppAllvas mismatch: gen={gen.hex()} live={live.hex()}"

    globs = parse_program_globals(st.program_s)
    assert globs == [("sys0", 0), ("sys1", 4), ("sys2", 8)]
    assert len(gen) == a1["AppAllvasQty"] * 12


def test_hmi_first_round_trip_with_generated_appallvas():
    """Whole-file round-trip stays byte-identical with the AppAllvas
    region GENERATED from Program.s (the binattinf tail still copied)."""
    data, a1 = _load(REPO_ROOT / "source" / "nextion.hmi.tft")
    strd = a1["strdataaddr"]
    pageadd = a1["pageadd"]
    sb = a1["staticstrBeg"]
    ad = a1["attdataaddr"]

    _, _, _, pages = _build_pages_from_hmi(data, a1, hmi_first=True)
    header = data[strd:strd + HEADER_END]

    st = load_hmi(str(HMI_PATH))
    appallvas = build_appallvas(st.program_s)
    binattinf = data[strd + ad:pageadd]   # still copied (work-item #2)
    tail = appallvas + binattinf

    usercode = assemble_usercode_region(header, pages, tail)
    out = bytearray(data)
    out[strd:pageadd] = usercode

    h1 = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H1_START:tft_format.H1_END]))
    out[tft_format.H1_CRC_OFF:tft_format.H1_CRC_OFF + 4] = struct.pack("<I", h1)
    h2 = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H2_START:tft_format.H2_END]))
    out[tft_format.H2_CRC_OFF:tft_format.H2_CRC_OFF + 4] = struct.pack("<I", h2)
    info = tft_format.trailing_crc_mask(data)
    body = crc32_bytewise(0xFFFFFFFF, bytes(out[:-4]))
    out[-4:] = struct.pack("<I", (body ^ info.mask) & 0xFFFFFFFF)

    assert bytes(out) == data, "round-trip with generated AppAllvas not byte-identical"
