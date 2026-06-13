"""Byte-exact corpus tests for the usercode region assembler (STEP 4/5).

Locks `scripts/lib/usercode_assembler` against the *live* project TFT
(`source/nextion.hmi.tft`). STEPs 1-3 produce each individual block
byte-for-byte; this module stitches them into the whole-region byte
stream in the editor's exact order, with the correct scaffolding
(terminators, event-NAME strings, the per-type event layout) and
back-patches the ``S``/``L`` suspend-resume markers.

What these tests pin:

  * **Per-page event-block region** — for every page, the assembler
    re-emits the complete component event/init block stream
    (objxinxi order, with the per-type :data:`EVENT_LAYOUT` scaffolding)
    byte-for-byte. This covers Page(121), XFloat(59), Text(116),
    Prog(106), Hotspot(109), Slider(1), Button(98), Timer(51) and
    Variable(52) layouts as they actually appear in the project.

  * **S/L marker back-patch** — ``patch_l_markers`` recomputes the
    project's single ``L`` marker address (``0x0d90``) from the paired
    ``S`` marker's location, reproducing the page-0 region (event blocks
    + media blob, which holds the timer re-arm ``L``) byte-for-byte.

The per-slot chain bodies are recovered from the live TFT and fed back
to the assembler, so the tests pin the *assembly* (ordering, scaffolding,
back-patch math) — the block contents themselves are STEP 1-3's
territory and are locked by their own corpus tests.
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
    CompiledComponent, PageBlocks, assemble_event_region,
    assemble_usercode_region, patch_l_markers,
)
from scripts.lib import tft_format  # noqa: E402
from scripts.lib.page_crc import crc32_bytewise  # noqa: E402

H2_START, H2_END = 0xC8, 0xC8 + 196
SENTINEL = 0xFFFFFFFF
HEADER_END = 0x9E  # first event block (obj0 init) — header = dir+namehash+MainCode


def _load(tft_path: Path):
    data = tft_path.read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[H2_START:H2_END], model_crc))
    return data, a1


def _chain_body(data: bytes, strd: int, slot_off: int) -> bytes:
    """The length-prefixed cglist entries of one handler, WITHOUT the
    trailing zero terminator (the layout adds it)."""
    pos = slot_off
    while True:
        ln = struct.unpack_from("<I", data, strd + pos)[0]
        if ln == 0 or ln > 0x400:
            break
        pos += 4 + ln
    return data[strd + slot_off:strd + pos]


def _page_components(data, strd, page_objs) -> list[CompiledComponent]:
    comps = []
    for o in page_objs:
        slots = struct.unpack_from("<6I", o.raw, 4)
        chains = {
            si: _chain_body(data, strd, s)
            for si, s in enumerate(slots)
            if s != SENTINEL
        }
        comps.append(CompiledComponent(lei=o.lei, chains=chains))
    return comps


def _page_event_region(data, strd, page_objs, media_off) -> tuple[int, bytes]:
    """Return (region_start, live_bytes) for one page's event-block
    region (first component's first slot .. page media blob)."""
    starts = [s for o in page_objs
              for s in struct.unpack_from("<6I", o.raw, 4) if s != SENTINEL]
    assert starts, "page has no event blocks"
    rstart = min(starts)
    return rstart, data[strd + rstart:strd + media_off]


@pytest.mark.parametrize("page_idx", [0, 1, 2, 3])
def test_page_event_region_byte_exact(tft_path, page_idx):
    """Each page's full component event-block region re-assembles
    byte-for-byte from per-slot chains + the per-type event layout."""
    data, a1 = _load(tft_path)
    strd = a1["strdataaddr"]
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])

    p = pd[page_idx]
    page_objs = objs[p["objstar"]:p["objstar"] + p["objqyt"]]
    rstart, live = _page_event_region(data, strd, page_objs, p["hexpos"])

    comps = _page_components(data, strd, page_objs)
    got = assemble_event_region(comps)

    assert got == live, (
        f"page {page_idx} event region mismatch: "
        f"got len={len(got)} live len={len(live)}"
    )


def test_l_marker_backpatch_byte_exact(tft_path):
    """The page-0 region (event blocks + media blob holding the timer
    re-arm ``L``) reproduces byte-for-byte after zeroing the ``L``
    address and letting ``patch_l_markers`` recompute it from the paired
    ``S`` marker."""
    data, a1 = _load(tft_path)
    strd = a1["strdataaddr"]
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])

    # Page-0 region spans from the first event block to the start of
    # page-1's blocks (covers page-0 event region + its media blob).
    rstart = 0x9E
    page1_start = pd[1]["hexpos"]  # page-1 blocks begin where? use objxinxi
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    p1 = pd[1]
    p1_objs = objs[p1["objstar"]:p1["objstar"] + p1["objqyt"]]
    p1_rstart = min(
        s for o in p1_objs
        for s in struct.unpack_from("<6I", o.raw, 4) if s != SENTINEL
    )
    live = data[strd + rstart:strd + p1_rstart]

    # Locate and zero the L marker, then recompute via the patcher.
    buf = bytearray(live)
    found_l = False
    for i in range(len(buf) - 11):
        if (struct.unpack_from("<I", buf, i)[0] == 7
                and buf[i + 4] == 0x4C and buf[i + 5] == 0x20
                and buf[i + 6] == 0x03):
            assert struct.unpack_from("<I", buf, i + 7)[0] == 0x0D90, (
                "expected project L addr 0x0d90"
            )
            struct.pack_into("<I", buf, i + 7, 0)
            found_l = True
    assert found_l, "page-0 region should contain the timer re-arm L marker"

    patched = patch_l_markers(bytes(buf), region_base=rstart)
    assert patched == live, "L back-patch did not reproduce the live region"


def _page_start(objs, p) -> int:
    page_objs = objs[p["objstar"]:p["objstar"] + p["objqyt"]]
    return min(s for o in page_objs
               for s in struct.unpack_from("<6I", o.raw, 4) if s != SENTINEL)


def _build_pages(data, strd, pd, objs, a1) -> list[PageBlocks]:
    pages = []
    for pi, p in enumerate(pd):
        page_objs = objs[p["objstar"]:p["objstar"] + p["objqyt"]]
        comps = _page_components(data, strd, page_objs)
        # Media blob spans from this page's hexpos to the next page's
        # first event block (or staticstrBeg for the last page).
        if pi + 1 < len(pd):
            media_end = _page_start(objs, pd[pi + 1])
        else:
            media_end = a1["staticstrBeg"]
        media = data[strd + p["hexpos"]:strd + media_end]
        pages.append(PageBlocks(components=comps, media=media))
    return pages


def test_whole_usercode_region_byte_exact(tft_path):
    """The entire usercode region (strdataaddr .. pageadd) reassembles
    byte-for-byte: header + per-page (event region + media blob) + tail,
    with the L marker back-patched."""
    data, a1 = _load(tft_path)
    strd = a1["strdataaddr"]
    pageadd = a1["pageadd"]
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])

    header = data[strd:strd + HEADER_END]
    tail = data[strd + a1["staticstrBeg"]:pageadd]
    pages = _build_pages(data, strd, pd, objs, a1)

    got = assemble_usercode_region(header, pages, tail)
    live = data[strd:pageadd]
    assert got == live, (
        f"usercode region mismatch: got len={len(got)} live len={len(live)}"
    )


def test_whole_file_round_trip_byte_identical(tft_path):
    """Reassembling the usercode region in place and resealing the three
    CRCs reproduces source/nextion.hmi.tft byte-for-byte — the STEP 4/5
    whole-file oracle."""
    data, a1 = _load(tft_path)
    strd = a1["strdataaddr"]
    pageadd = a1["pageadd"]
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])

    header = data[strd:strd + HEADER_END]
    tail = data[strd + a1["staticstrBeg"]:pageadd]
    pages = _build_pages(data, strd, pd, objs, a1)
    usercode = assemble_usercode_region(header, pages, tail)

    out = bytearray(data)
    out[strd:pageadd] = usercode
    assert len(out) == len(data)

    # Reseal H1, H2, trailing CRCs (verbatim H1/H2/resources/tables).
    h1 = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H1_START:tft_format.H1_END]))
    out[tft_format.H1_CRC_OFF:tft_format.H1_CRC_OFF + 4] = struct.pack("<I", h1)
    h2 = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H2_START:tft_format.H2_END]))
    out[tft_format.H2_CRC_OFF:tft_format.H2_CRC_OFF + 4] = struct.pack("<I", h2)
    info = tft_format.trailing_crc_mask(data)
    body = crc32_bytewise(0xFFFFFFFF, bytes(out[:-4]))
    out[-4:] = struct.pack("<I", (body ^ info.mask) & 0xFFFFFFFF)

    assert bytes(out) == data, "whole-file round-trip is not byte-identical"
