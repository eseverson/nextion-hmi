"""Byte-exact corpus tests for the event-handler script compiler.

These lock the script compiler (`scripts/lib/script_compiler.py` +
`script_compiler_extras.py`) against the *live* project TFT
(`source/nextion.hmi.tft`), not a synthetic fixture. Each test:

  1. Locates a compiled event-handler block in the TFT by reading the
     owning component's objxinxi event slots (the 6×u32 slots in the
     objdata_Ram head).
  2. Compiles the corresponding source (transcribed from
     `work/hmi_text/main.txt`) with `compile_event_handler`.
  3. Asserts the compiled bytes equal the live block byte-for-byte.

Covered (STEP 2 of findings/next-steps.md):
  - tm0 Timer Event (codestimer): 9 if/else-if/else chains incl. a `||`
    condition and a `sys0=x7.val-x4.val` subtraction. 806-byte body.
  - All four pages' Hotspot Touch-Release navigation (`page N`).

The leading `S <addr>` suspend/resume marker the editor emits at the
start of the timer handler is excluded from the diff: its `addr` field
is a global label-table index patched at TFT-output time (see
findings/script-control-flow.md "L <addr> opcode"). The body that
follows — all the control flow whose byte offsets are self-relative —
is what these tests pin.
"""
from __future__ import annotations
import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.lib.script_compiler import (  # noqa: E402
    CompileContext, compile_event_handler,
)
from scripts.lib.tft_attrs import (  # noqa: E402
    parse_appinf1_corrected, parse_page_directory, parse_objxinxi,
)
from scripts.lib.h2_cipher import encrypt as h2_decrypt  # noqa: E402

OBJXINXI_STRIDE = 232
H2_START, H2_END = 0xC8, 0xC8 + 196
SENTINEL = 0xFFFFFFFF


# --- Live-TFT block location helpers --------------------------------------

def _load(tft_path: Path):
    data = tft_path.read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[H2_START:H2_END], model_crc))
    return data, a1


def _event_slots(data: bytes, objxinxiadd: int, obj_index: int) -> list[int]:
    """Return the 6 event-slot u32s for the obj at `obj_index`."""
    off = objxinxiadd + obj_index * OBJXINXI_STRIDE
    return list(struct.unpack_from("<6I", data, off + 4))


def _read_block(data: bytes, strd: int, slot_off: int) -> bytes:
    """Walk the length-prefixed cglist entries of one handler block,
    starting at `strd + slot_off`, and return the concatenated bytes
    (each entry as `<u32 len><body>`). Stops at a zero/garbage length."""
    pos = strd + slot_off
    out = bytearray()
    while True:
        length = struct.unpack_from("<I", data, pos)[0]
        if length == 0 or length > 0x200:
            break
        out += data[pos:pos + 4 + length]
        pos += 4 + length
    return bytes(out)


def _find_hotspot_indices(data, a1) -> list[tuple[int, int]]:
    """Return (page_index, obj_index) for every Hotspot (lei=109)."""
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    out = []
    for pi, pg in enumerate(pd):
        for oi in range(pg["objstar"], pg["objstar"] + pg["objqyt"]):
            if objs[oi].lei == 109:
                out.append((pi, oi))
    return out


def _find_timer_index(data, a1) -> int:
    """Return the obj_index of the first Timer (lei=51)."""
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    for oi, o in enumerate(objs):
        if o.lei == 51:
            return oi
    raise AssertionError("no Timer (lei=51) in project")


# --- tm0 source (transcribed verbatim from work/hmi_text/main.txt) --------

TM0_SOURCE = """
if(x2.val<1400)
{
    x2.bco=blu.val
}else if(x2.val>2100)
{
    x2.bco=red.val
}else
{
    x2.bco=bco.val
}
if(x5.val>1000)
{
    x5.bco=red.val
}else
{
    x5.bco=bco.val
}
if(x1.val>6800)
{
    x1.bco=red.val
}else if(x1.val>6500)
{
    x1.bco=org.val
}else if(x1.val>6000)
{
    x1.bco=yel.val
}else
{
    x1.bco=bco.val
}
if(x6.val<125)
{
    x6.bco=red.val
}else
{
    x6.bco=bco.val
}
sys0=x7.val-x4.val
if(sys0>20||sys0<-20)
{
    x4.bco=red.val
    x7.bco=red.val
}
if(x8.val>0)
{
    x8.bco=red.val
}else
{
    x8.bco=bco.val
}
if(x0.val>2000)
{
    x0.bco=red.val
}else if(x0.val>1700)
{
    x0.bco=org.val
}else if(x0.val>1550)
{
    x0.bco=yel.val
}else if(x0.val>1000)
{
    x0.bco=grn.val
}else
{
    x8.bco=bco.val
}
"""

# Page-0 public-memory frame offsets for the components tm0 touches.
# Derived from the live TFT's event bytecode (T0 public-memory layout:
# XFloat block = 41 bytes, val at +5, bco at +1; Vari block = 11 bytes,
# val at +0). These match the page-0 layout of source/nextion.hmi.tft.
TM0_OFFSETS = {
    "x0.val": 0x3C, "x0.bco": 0x38,
    "x1.val": 0x65, "x1.bco": 0x61,
    "x2.val": 0x8E, "x2.bco": 0x8A,
    "x4.val": 0xE0, "x4.bco": 0xDC,
    "x5.val": 0x109, "x5.bco": 0x105,
    "x6.val": 0x153, "x6.bco": 0x14F,
    "x7.val": 0x17C, "x7.bco": 0x178,
    "x8.val": 0x1A5, "x8.bco": 0x1A1,
    "bco.val": 0x37B, "blu.val": 0x386, "red.val": 0x391,
    "yel.val": 0x3A7, "org.val": 0x3B2, "grn.val": 0x3BD,
}
TM0_GLOBALS = {"sys0": 0x00}

# Hotspot Touch-Release navigation target per page (from main.txt /
# settings.txt / etc. — each m0 navigates to the next page in the cycle).
HOTSPOT_RELEASE_PAGE = {0: 1, 1: 2, 2: 0, 3: 0}


# --- Tests ----------------------------------------------------------------

def test_tm0_codestimer_byte_exact(tft_path):
    """The full 9-chain Timer Event compiles byte-for-byte against the
    live block (after the leading S-marker entry)."""
    data, a1 = _load(tft_path)
    strd = a1["strdataaddr"]

    tm_idx = _find_timer_index(data, a1)
    slots = _event_slots(data, a1["objxinxiadd"], tm_idx)
    # The Timer Event lives in the 5th slot (index 4) for this project.
    timer_slot = next(s for s in slots if s != SENTINEL)
    block = _read_block(data, strd, timer_slot)

    # Split off the leading S-marker entry: <u32 len=7><53 20 03 LL LL LL LL>.
    first_len = struct.unpack_from("<I", block, 0)[0]
    assert first_len == 7 and block[4] == 0x53 and block[5] == 0x20, (
        f"expected leading S-marker, got len={first_len} "
        f"head={block[4:7].hex()}"
    )
    expected_body = block[4 + first_len:]

    ctx = CompileContext()
    ctx.component_offsets = dict(TM0_OFFSETS)
    ctx.globals = dict(TM0_GLOBALS)
    got = compile_event_handler(TM0_SOURCE, ctx)

    assert got == expected_body, (
        f"tm0 codestimer mismatch:\n"
        f"  got len={len(got)} exp len={len(expected_body)}"
    )


@pytest.mark.parametrize("page_idx", [0, 1, 2, 3])
def test_hotspot_release_navigation_byte_exact(tft_path, page_idx):
    """Each page's Hotspot Touch-Release `page N` navigation compiles
    byte-for-byte against the live release-slot block."""
    data, a1 = _load(tft_path)
    strd = a1["strdataaddr"]

    hotspots = _find_hotspot_indices(data, a1)
    page_to_obj = {pi: oi for pi, oi in hotspots}
    assert page_idx in page_to_obj, f"no hotspot on page {page_idx}"

    slots = _event_slots(data, a1["objxinxiadd"], page_to_obj[page_idx])
    # Touch Release lives in slot index 2.
    release_off = slots[2]
    assert release_off != SENTINEL, f"page {page_idx} hotspot has no release slot"
    expected = _read_block(data, strd, release_off)

    target = HOTSPOT_RELEASE_PAGE[page_idx]
    ctx = CompileContext()
    got = compile_event_handler(f"page {target}", ctx)

    assert got == expected, (
        f"page {page_idx} hotspot release nav mismatch:\n"
        f"  got {got.hex()} exp {expected.hex()}"
    )
