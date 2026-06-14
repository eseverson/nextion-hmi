"""Per-type binattinf byte-verification for the untested-component classes.

The ``03_untested-components`` oracle adds 10 component classes the compiler
hadn't generated before (me/miata-dash#38) on its error page (page 3):

    Number(54) Picture(112) Gauge/Zhizhen(122) Checkbox(56) Radio(57)
    Button_T(53) QRcode(58) Waveform(0) Picc(113) GText(55)

This test locks the part that is fully reverse-engineered and byte-exact: the
per-component **binattinf** record block (the DATAFROM_ATTRS / LITERAL_ATTRS /
PER_LEI_LAYOUT / SIZEOF tables).  Each class's generated block is asserted
byte-identical to the editor's.

(Whole-file compile of ``untested.tft`` is NOT yet byte-identical — the frame
``<Kind>_PARAM`` blocks, EVENT_LAYOUT, init bytecode and objxinxi for these
classes are still being reversed; see #38.  This test guards the binattinf
foundation so it can't silently regress while the rest lands.)
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.lib.tft_attrs import parse_appinf1_corrected  # noqa: E402
from scripts.lib.h2_cipher import encrypt as h2_decrypt  # noqa: E402
from scripts.lib.hmi_model import load_hmi_pages  # noqa: E402
from scripts.lib.tft_attrs_encoder import APP_ATT_NAMES  # noqa: E402
from scripts.lib.tft_attrs_layout import PER_LEI_LAYOUT  # noqa: E402
from scripts.lib.binattinf_generator import build_component_binattinf  # noqa: E402

TFT = REPO_ROOT / "tests" / "editor outputs" / "03_untested-components" / "untested.tft"
HMI = REPO_ROOT / "tests" / "editor outputs" / "03_untested-components" / "untested.HMI"

# odrp (objdatarampos) per error-page component, read from the oracle's
# objxinxi entries; the binattinf records embed it.
_ODRP = {
    "error": 0x4C, "m0": 0x54, "n0": 0x54, "p0": 0x6C, "z0": 0x70,
    "c0": 0x88, "r0": 0x94, "bt0": 0x9C, "qr0": 0xB4, "s0": 0xC4,
    "q0": 0xE8, "g0": 0xF0,
}

# The new component classes (skip the already-verified page/hotspot).
_NEW = {"n0", "p0", "z0", "c0", "r0", "bt0", "qr0", "s0", "q0", "g0"}


def _oracle():
    data = TFT.read_bytes()
    mc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[0xC8:0xC8 + 196], mc))
    strd = a1["strdataaddr"]
    objbase = a1["objxinxiadd"]
    pe = data[a1["pageadd"] + 3 * 16:a1["pageadd"] + 3 * 16 + 16]
    attdata_rel = struct.unpack_from("<I", pe, 8)[0]
    recbase = strd + attdata_rel
    pages = load_hmi_pages(str(HMI))
    return data, objbase, recbase, pages[3]


def _names():
    _, _, _, ep = _oracle()
    return [c.name for c in ep.components if c.name in _NEW]


@pytest.mark.parametrize("name", _names())
def test_untested_component_binattinf_byte_identical(name):
    data, objbase, recbase, ep = _oracle()
    ci, c = next((i, c) for i, c in enumerate(ep.components) if c.name == name)
    # The oracle's record slice for this component (full PER_LEI stride).
    e = data[objbase + (42 + ci) * 232:objbase + (42 + ci) * 232 + 232]
    slots = struct.unpack_from("<88H", e, 56)
    base = min(s for s in slots if s != 0xFFFF)
    stride = PER_LEI_LAYOUT[c.lei][0]
    oracle = data[recbase + base * 24:recbase + (base + stride) * 24]

    got = build_component_binattinf(
        c.lei, int(c.attrs.get("id") or 0),
        int(c.attrs.get("x") or 0), int(c.attrs.get("y") or 0),
        int(c.attrs.get("w") or 0), int(c.attrs.get("h") or 0),
        objdatarampos=_ODRP[name], frompageid=3, fromobjid=ci,
        authored=c.attrs, font_count=3,
    )
    assert got == oracle, (
        f"{name} (lei {c.lei}) binattinf diverges: "
        f"{len(got)} vs {len(oracle)} bytes"
    )
