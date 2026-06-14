"""End-to-end acceptance gate for the LIVE fixture: compile(live.HMI) == live TFT.

This mirrors ``test_compile_hmi_end_to_end`` but targets the second fixture
``tests/fixtures/live/nextion.hmi.{HMI,tft}`` — the live display project,
authored in a *newer* Nextion editor build (H1 version letter ``'D'`` vs the
source fixture's ``'C'``).  It exercises the version/project parameterization
in ``scripts.lib.compiler_profile`` (read from the TFT H1 version letter):

  * the ``09 xx 08`` opcode family is shifted +1 (setbrush / doevents /
    timerset), and the ``baud`` / ``dims`` system-variable indices moved;
  * the media-frame runtime header is 0x40 (vs 0x10), and ``vscope=1``
    global-scope objects are pulled into a prefix slot;
  * ``Fontid`` attribute bounds follow the project font count (3 → max 2);
  * the ``txt`` buffer capacity follows the configured ``txt_maxl`` (the
    live ``s0`` has ``txt=''`` but ``txt_maxl=40``);
  * the per-project editor scratch (``S`` biaoji + ``settings/t0`` brush
    state) is captured against this project's own reference.

The whole-file output is asserted byte-identical to the live
``tests/fixtures/live/nextion.hmi.tft`` (510388 bytes).  This is ADDITIVE to
the source milestone — both fixtures compile from the one code path.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.lib.compile_hmi import compile_hmi  # noqa: E402
from scripts.lib.tft_attrs import parse_appinf1_corrected  # noqa: E402
from scripts.lib.h2_cipher import encrypt as h2_decrypt  # noqa: E402

HMI_PATH = REPO_ROOT / "tests" / "fixtures" / "live" / "nextion.hmi.HMI"
TFT_PATH = REPO_ROOT / "tests" / "fixtures" / "live" / "nextion.hmi.tft"


def test_compile_live_hmi_byte_identical():
    """compile(live.HMI) reproduces the live nextion.hmi.tft byte-for-byte."""
    out = compile_hmi(str(HMI_PATH), str(TFT_PATH))
    ref = TFT_PATH.read_bytes()
    assert len(out) == len(ref)
    # Pinpoint the first divergence if any, for a useful failure message.
    if out != ref:
        first = next(i for i in range(len(out)) if out[i] != ref[i])
        raise AssertionError(
            f"compile(live.HMI) diverges at offset {first:#x}: "
            f"got {out[first:first + 8].hex()} != {ref[first:first + 8].hex()}"
        )


def test_live_only_resources_prefix_is_reference_derived():
    """Reference-free claim for the live fixture: everything from
    ``strdataaddr`` up to the objxinxi table's end is GENERATED.  Zeroing the
    reference's entire usercode region still yields byte-identical content
    from ``strdataaddr`` to ``objxinxiadd + 232*objqyt``.

    The live file then has a 3-byte ``0xff`` alignment gap between the
    objxinxi table and the trailing CRC — template padding legitimately
    preserved from the resources source (the source fixture has no such
    gap), so the generated-content window stops at the table's end."""
    ref = TFT_PATH.read_bytes()
    model_crc = int.from_bytes(ref[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(ref[0xC8:0xC8 + 196], model_crc))
    strd = a1["strdataaddr"]
    gen_end = a1["objxinxiadd"] + 232 * a1["objqyt"]   # end of generated table

    doctored = bytearray(ref)
    doctored[strd:] = b"\x00" * (len(ref) - strd)
    tmp = REPO_ROOT / "work" / "_compile_live_e2e_resources_only.tft"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_bytes(doctored)
    try:
        out = compile_hmi(str(HMI_PATH), str(tmp))
    finally:
        tmp.unlink()

    assert out[strd:gen_end] == ref[strd:gen_end], (
        "live content past the resources prefix is NOT fully generated "
        "(it changed when the reference's usercode region was zeroed)"
    )
