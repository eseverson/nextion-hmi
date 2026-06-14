"""End-to-end acceptance gate: compile(project.HMI) == reference TFT.

``scripts.lib.compile_hmi.compile_hmi`` assembles a complete ``.tft`` from
the project ``.HMI``, GENERATING every region from the HMI model — event
regions, media blobs (name-hash + frame image + page-init), AppAllvas,
binattinf, objxinxi geometry + back-patched offsets, and the page
directory — and reseals H1/H2 + the three CRCs.

Spans still taken from the reference TFT (documented follow-ups, not yet
HMI-generated; the resources blob is legitimately out of scope):

  * the **resources region** ``0x10000``..``0x70000`` (fonts/pictures — a
    property of the bootloader/driver/font composition, OUT OF SCOPE);
  * the **usercode header** (global memory directory + 4-entry page
    name-hash + MainCode = compiled Program.s) — header generation is a
    follow-up;
  * the per-component **attstr 88-slot tables** in the objxinxi entries
    (produced byte-exact by ``tft_attrs_encoder.build_component_block`` in
    its own test, but not yet wired into this assembly).

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
