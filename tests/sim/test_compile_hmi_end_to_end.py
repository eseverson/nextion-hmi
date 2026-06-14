"""End-to-end acceptance gate: compile(project.HMI) == reference TFT.

``scripts.lib.compile_hmi.compile_hmi`` assembles a complete ``.tft`` from
the project ``.HMI``, GENERATING **every project-content region** from the
HMI model — the usercode header (global dir + page name-hash + MainCode),
the per-page event regions, the media blobs (name-hash + frame image +
page-init), AppAllvas, binattinf, the objxinxi table (geometry + offsets +
attstr 88-slot tables), the page directory, and the **appinf1 (H2)
directory** (its region offsets/counts derived from the assembled sizes) —
and reseals the H1 + 3 CRCs.

The span reused from the reference is its **resources prefix**
``[0 .. strdataaddr)`` — the H1 framing plus the fonts/pictures resources
blob (``0x10000``..``0x70000``), a property of the bootloader/driver/font
composition rather than the HMI project content (the resources blob is
explicitly OUT OF SCOPE per me/nextion#2).  The H2 directory inside that
prefix is REGENERATED; only the resources-coupled appinf1 fields
(resources_size, picxinxi/zimo offsets, picture/font descriptors) are read
from it.

The whole-file output is asserted byte-identical to
``source/nextion.hmi.tft``.
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

HMI_PATH = REPO_ROOT / "source" / "nextion.hmi.HMI"
TFT_PATH = REPO_ROOT / "source" / "nextion.hmi.tft"


def test_compile_hmi_byte_identical():
    """compile(project.HMI) reproduces source/nextion.hmi.tft byte-for-byte."""
    out = compile_hmi(str(HMI_PATH), str(TFT_PATH))
    ref = TFT_PATH.read_bytes()
    assert len(out) == len(ref)
    # Pinpoint the first divergence if any, for a useful failure message.
    if out != ref:
        first = next(i for i in range(len(out)) if out[i] != ref[i])
        raise AssertionError(
            f"compile(HMI) diverges at offset {first:#x}: "
            f"got {out[first:first + 8].hex()} != {ref[first:first + 8].hex()}"
        )


def test_only_resources_prefix_is_reference_derived():
    """Prove the reference-free claim: the compiled output is GENERATED for
    everything from ``strdataaddr`` onward.  Compiling with a reference whose
    usercode-onward region is zeroed still produces a byte-identical result
    from ``strdataaddr`` to the trailing CRC — i.e. nothing past the
    resources prefix is copied from the reference."""
    ref = TFT_PATH.read_bytes()
    model_crc = int.from_bytes(ref[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(ref[0xC8:0xC8 + 196], model_crc))
    strd = a1["strdataaddr"]

    # A doctored "resources source" with everything from strdataaddr zeroed.
    doctored = bytearray(ref)
    doctored[strd:] = b"\x00" * (len(ref) - strd)
    tmp = REPO_ROOT / "work" / "_compile_e2e_resources_only.tft"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_bytes(doctored)
    try:
        out = compile_hmi(str(HMI_PATH), str(tmp))
    finally:
        tmp.unlink()

    # The generated content (strdataaddr .. last 4 CRC bytes) must match the
    # real reference exactly despite the resources source being zeroed there.
    assert out[strd:-4] == ref[strd:-4], (
        "content past the resources prefix is NOT fully generated "
        "(it changed when the reference's usercode region was zeroed)"
    )


def test_generated_appinf1_equals_reference():
    """The GENERATED appinf1 (H2 directory) equals the reference's — every
    content field (staticstrBeg / attdataaddr / pageadd / objxinxiadd /
    page+obj counts / MainCodeHex / AppAllvasQty) is *derived* from the HMI
    + the assembled region sizes, not copied.  The encrypted 196-byte H2
    block must match byte-for-byte."""
    from scripts.lib.compile_hmi import (
        assemble_usercode_and_layout, _build_appinf1,
    )
    from scripts.lib.hmi_model import load_hmi_pages, parse_program_globals
    from sim.loader import load_hmi

    ref = TFT_PATH.read_bytes()
    model_crc = int.from_bytes(ref[0x2E:0x32], "little")
    live_h2 = ref[0xC8:0xC8 + 196]

    program_s = load_hmi(str(HMI_PATH)).program_s
    hmi = load_hmi_pages(HMI_PATH)
    qty = len(parse_program_globals(program_s))
    _, _, _, _, static_str_beg = assemble_usercode_and_layout(
        hmi, program_s, qty)
    gen = _build_appinf1(ref, model_crc, hmi, program_s, qty, static_str_beg)

    # Every content field equals the reference's parsed value.
    live = parse_appinf1_corrected(h2_decrypt(live_h2, model_crc))
    for key in ("staticstrBeg", "AppAllvasAddr", "AppAllvasQty",
                "attdataaddr", "strdataaddr", "pageadd", "objxinxiadd",
                "MainCodeHex", "pageqyt", "objqyt", "picqyt", "zimoqyt"):
        assert gen.fields[key] == live[key], (
            f"appinf1.{key}: generated {gen.fields[key]:#x} "
            f"!= reference {live[key]:#x}"
        )
    # And the encrypted H2 block is byte-identical.
    assert gen.encrypted == live_h2, "generated H2 (appinf1) != reference"


def test_appinf1_offsets_self_consistent_with_assembly():
    """The GENERATED appinf1 offsets point exactly where the assembled
    regions land — the invariant that makes the directory correct under a
    STRUCTURAL edit (added/removed components change the region sizes, and
    the derived offsets follow).  Checks:

        strdataaddr + len(usercode)        == pageadd
        pageadd     + 16*pageqyt           == objxinxiadd
        objxinxiadd + 232*objqyt           == file_size - 4 (trailing CRC)
        AppAllvasAddr + 12*AppAllvasQty    == attdataaddr
    """
    from scripts.lib.compile_hmi import (
        assemble_usercode_and_layout, _build_appinf1,
    )
    from scripts.lib.hmi_model import load_hmi_pages, parse_program_globals
    from sim.loader import load_hmi

    ref = TFT_PATH.read_bytes()
    model_crc = int.from_bytes(ref[0x2E:0x32], "little")

    program_s = load_hmi(str(HMI_PATH)).program_s
    hmi = load_hmi_pages(HMI_PATH)
    qty = len(parse_program_globals(program_s))
    usercode, _, _, _, static_str_beg = assemble_usercode_and_layout(
        hmi, program_s, qty)
    f = _build_appinf1(ref, model_crc, hmi, program_s, qty, static_str_beg).fields

    assert f["strdataaddr"] + len(usercode) == f["pageadd"]
    assert f["pageadd"] + 16 * f["pageqyt"] == f["objxinxiadd"]
    assert f["objxinxiadd"] + 232 * f["objqyt"] == len(ref) - 4
    assert f["AppAllvasAddr"] + 12 * f["AppAllvasQty"] == f["attdataaddr"]
