"""Byte-exact corpus tests for the per-component init-bytecode encoder.

Locks `scripts/lib/tft_init_encoder.encode_init_block` against the *live*
project TFT (`source/nextion.hmi.tft`) for every component type whose
init block this project actually contains (STEP 3 of
findings/next-steps.md):

  - Slider (1), Timer (51), Variable (52): empty init blocks
    (init_off == 0xFFFFFFFF).
  - ProgressBar (106, dir=0): the j0 two-rectangle fill with a scratch
    sysvar `sya0 = val * w / 100` (83 bytes).
  - Button (98, sta=1/style=4): the b0 if/else draw incl. draw3d bevel
    (305 bytes).
  - XFloat (59) and Text (116): setbrush + draw, verified as a side
    effect of pinning the editor-version setbrush opcode.

Editor-version note: the project TFT was produced by the nxt-1.65.1
toolchain, whose `setbrush` opcode head is `09 1c 08`. The encoder
defaults to the 1.67.1 head (`09 1d 08`, used by the
`17_more_components` fixtures), so these tests pass
`opcode_override={"setbrush": b"\\x09\\x1c\\x08"}`.

The per-attribute LOAD addresses are public-memory offsets that the
editor assigns deterministically; here they're recovered from the live
block (by position, in the encoder's own attr-request order) and fed
back to the encoder. The test therefore pins the *structure* (opcodes,
operand kinds, arg order, control-flow shape) byte-for-byte; the
addresses themselves are the project's, by construction.
"""
from __future__ import annotations
import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.lib.tft_init_encoder import (  # noqa: E402
    encode_init_block, Component,
)
from scripts.lib.tft_attrs import (  # noqa: E402
    parse_appinf1_corrected, parse_objxinxi,
)
from scripts.lib.h2_cipher import encrypt as h2_decrypt  # noqa: E402

H2_START, H2_END = 0xC8, 0xC8 + 196
SENTINEL = 0xFFFFFFFF
# nxt-1.65.1 setbrush head (the toolchain that produced the project TFT).
SETBRUSH_165 = {"setbrush": bytes((0x09, 0x1C, 0x08))}


def _load(tft_path: Path):
    data = tft_path.read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[H2_START:H2_END], model_crc))
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    return data, a1, objs


def _live_block(data: bytes, strd: int, obj) -> bytes:
    """Concatenate the length-prefixed init entries for one component."""
    ab = strd + obj.init_off
    pos = ab
    while True:
        ln = struct.unpack_from("<I", data, pos)[0]
        if ln == 0 or ln > 0x300:
            break
        pos += 4 + ln
    return data[ab:pos]


def _loads(blk: bytes) -> list[int]:
    """Extract every `01 LL LL LL LL` LOAD operand value, in order."""
    out = []
    i = 0
    while i < len(blk):
        if blk[i] == 0x01 and i + 5 <= len(blk):
            out.append(struct.unpack_from("<I", blk, i + 1)[0])
            i += 5
        else:
            i += 1
    return out


def _addr_map_from_live(comp: Component, live: bytes) -> dict[str, int]:
    """Recover attr -> LOAD-address by replaying the encoder's attr-request
    order against the live block's LOAD operands."""
    order: list[str] = []

    def record(name: str) -> int:
        order.append(name)
        return 0

    encode_init_block(comp, record, opcode_override=SETBRUSH_165)
    live_loads = _loads(live)
    assert len(order) == len(live_loads), (
        f"{comp.comp_type}: encoder requests {len(order)} LOADs "
        f"({order}) but live block has {len(live_loads)} "
        f"({[hex(x) for x in live_loads]})"
    )
    return dict(zip(order, live_loads))


def _find_first(objs, lei: int):
    for oi, o in enumerate(objs):
        if o.lei == lei:
            return oi, o
    raise AssertionError(f"no component with lei={lei}")


# --- empty-init types -----------------------------------------------------

@pytest.mark.parametrize("lei,label", [(1, "Slider"), (51, "Timer"), (52, "Variable")])
def test_empty_init_types(tft_path, lei, label):
    """Slider / Timer / Variable have no per-component init bytecode."""
    data, a1, objs = _load(tft_path)
    oi, o = _find_first(objs, lei)
    assert o.init_off == SENTINEL, (
        f"{label} (lei={lei}) obj[{oi}] init_off={hex(o.init_off)}, "
        f"expected empty (0xFFFFFFFF)"
    )
    # The encoder agrees: empty block.
    comp = Component(comp_type=lei, comp_id=o.id, page_id=0,
                     x=0, y=0, w=1, h=1, attrs={})
    assert encode_init_block(comp, lambda n: 0,
                             opcode_override=SETBRUSH_165) == b""


# --- ProgressBar (106, dir=0) ---------------------------------------------

def test_prog_dir0_byte_exact(tft_path):
    """j0 ProgressBar (dir=0) init block compiles byte-for-byte."""
    data, a1, objs = _load(tft_path)
    strd = a1["strdataaddr"]
    oi, o = _find_first(objs, 106)
    live = _live_block(data, strd, o)
    assert live, "Prog has no init block"

    x = o.endx - o.w + 1
    y = o.endy - o.h + 1
    comp = Component(comp_type=106, comp_id=o.id, page_id=0,
                     x=x, y=y, w=o.w, h=o.h,
                     attrs={"dir": 0, "val": 50, "sta": 0})
    amap = _addr_map_from_live(comp, live)
    got = encode_init_block(comp, lambda n: amap[n],
                            opcode_override=SETBRUSH_165)
    assert got == live, (
        f"Prog dir=0 mismatch: got len={len(got)} live len={len(live)}\n"
        f"  got  {got.hex()}\n  live {live.hex()}"
    )


# --- Button (98, sta=1/style=4) -------------------------------------------

def test_button_style4_byte_exact(tft_path):
    """b0 Button (sta=1, style=4) init block compiles byte-for-byte.

    The editor packs the button's attribute slots contiguously, so the
    per-attr LOAD address is ``base + delta`` for a fixed delta table.
    `base` is recovered from the live block's first LOAD (the cjmp's
    `val` operand), so nothing is hard-coded to a magic offset."""
    data, a1, objs = _load(tft_path)
    strd = a1["strdataaddr"]
    oi, o = _find_first(objs, 98)
    live = _live_block(data, strd, o)
    assert live, "Button has no init block"

    # First LOAD in the block is the cjmp's `val` operand.
    val_addr = _loads(live)[0]
    # Slot deltas relative to `val` (contiguous packing observed in the
    # b0 block: font/picc/picc2/pco/pco2/xcen/ycen/val/txt run +0..+7
    # from font, isbr is +2 past txt). Anchored at val == base+11.
    base = val_addr - 11
    DELTA = {"font": 4, "picc": 5, "picc2": 6, "pco": 7, "pco2": 8,
             "xcen": 9, "ycen": 10, "val": 11, "txt": 12, "isbr": 14}
    amap = {k: base + d for k, d in DELTA.items()}

    x = o.endx - o.w + 1
    y = o.endy - o.h + 1
    comp = Component(comp_type=98, comp_id=o.id, page_id=2,
                     x=x, y=y, w=o.w, h=o.h,
                     attrs={"sta": 1, "style": 4, "spax": 0, "spay": 0})
    got = encode_init_block(comp, lambda n: amap[n],
                            opcode_override=SETBRUSH_165)
    assert got == live, (
        f"Button mismatch: got len={len(got)} live len={len(live)}\n"
        f"  got  {got.hex()}\n  live {live.hex()}"
    )


# --- XFloat (59) + Text (116): setbrush users -----------------------------

@pytest.mark.parametrize("lei,attrs", [
    (59, {"sta": 1}),
    (116, {"sta": 1}),
])
def test_setbrush_user_byte_exact(tft_path, lei, attrs):
    """First XFloat / Text on page 0 compiles byte-for-byte (pins the
    nxt-1.65.1 setbrush opcode head)."""
    data, a1, objs = _load(tft_path)
    strd = a1["strdataaddr"]
    oi, o = _find_first(objs, lei)
    live = _live_block(data, strd, o)
    assert live, f"lei={lei} has no init block"

    x = o.endx - o.w + 1
    y = o.endy - o.h + 1
    comp = Component(comp_type=lei, comp_id=o.id, page_id=0,
                     x=x, y=y, w=o.w, h=o.h, attrs=dict(attrs))
    amap = _addr_map_from_live(comp, live)
    got = encode_init_block(comp, lambda n: amap[n],
                            opcode_override=SETBRUSH_165)
    assert got == live, (
        f"lei={lei} mismatch: got len={len(got)} live len={len(live)}\n"
        f"  got  {got.hex()}\n  live {live.hex()}"
    )
