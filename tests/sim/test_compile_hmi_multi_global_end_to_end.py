"""End-to-end acceptance gate for the MULTI-GLOBAL fixtures: compile == oracle.

These three fixtures (editor-produced, version 'D') exercise the multi-global
(``vscope=1``) per-page prefix allocator (me/miata-dash#37) and the global
*Text* serialization (me/miata-dash#35):

  * ``nextion`` — THREE global xfloats on its gauge page (x1, g0, g1);
  * ``3global`` — a fourth (g2);
  * ``global_mixed`` — a fourth global xfloat plus a global **Text** (t0,
    ``txt='ABC'``).

The earlier single-global allocator (``0x2c - status - SIZEOF*k``, stacking
downward) underflowed at the second global; the corrected rule places the
first global at ``0x2c - status_g`` and stacks UPWARD, adds a project-wide
constant shift to every page's non-global region, and (for ``global_mixed``)
writes the global Text's txt into the global-memory directory as a 20-byte
record + emits the global-Text z-order refresh-ref prefix + sizes the txt
binattinf capacity to the 11-byte global-record inline budget.

All three whole-file outputs are asserted byte-identical to the editor's TFT.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.lib.compile_hmi import compile_hmi  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "multi_global"


@pytest.mark.parametrize("name", ["nextion", "3global", "global_mixed"])
def test_compile_multi_global_byte_identical(name):
    """compile(<multi-global>.HMI) reproduces the editor TFT byte-for-byte."""
    hmi = FIXTURES / f"{name}.HMI"
    tft = FIXTURES / f"{name}.tft"
    out = compile_hmi(str(hmi), str(tft))
    ref = tft.read_bytes()
    assert len(out) == len(ref), (
        f"{name}: size {len(out)} != {len(ref)}"
    )
    if out != ref:
        first = next(i for i in range(len(out)) if out[i] != ref[i])
        raise AssertionError(
            f"compile({name}.HMI) diverges at offset {first:#x}: "
            f"got {out[first:first + 8].hex()} != {ref[first:first + 8].hex()}"
        )
