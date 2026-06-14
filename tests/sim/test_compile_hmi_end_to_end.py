"""End-to-end acceptance gate: compile(project.HMI) == reference TFT.

``scripts.lib.compile_hmi.compile_hmi`` assembles a complete ``.tft`` from
the project ``.HMI``, GENERATING **every project-content region** from the
HMI model — the usercode header (global dir + page name-hash + MainCode),
the per-page event regions, the media blobs (name-hash + frame image +
page-init), AppAllvas, binattinf, the objxinxi table (geometry + offsets +
attstr 88-slot tables), and the page directory — and reseals the three
CRCs.

The **only** span reused from the reference is its **resources prefix**
``[0 .. strdataaddr)`` — the H1/H2/appinf framing plus the fonts/pictures
resources blob (``0x10000``..``0x70000``), a property of the bootloader/
driver/font composition rather than the HMI project content, and the one
legitimately-external input (the resources blob is explicitly OUT OF SCOPE
per me/nextion#2).  This is the reference-free milestone.

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
