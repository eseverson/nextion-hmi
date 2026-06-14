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


# The dash-lights oracle (5 global vscope=1 Text boxes, txt_maxl 15 → 28-byte
# global-dir records) lives outside tracked fixtures (HMI/TFT are gitignored
# from the public mirror), so reference it by path and skip if absent.
_LIGHTS = REPO_ROOT / "tests" / "editor outputs" / "04_global_lights"


@pytest.mark.skipif(not (_LIGHTS / "lights.tft").exists(),
                    reason="lights oracle not present (gitignored)")
def test_compile_global_lights_byte_identical():
    """compile(lights.HMI) reproduces the editor's dash-lights TFT byte-for-byte.

    Exercises the MULTI global-Text path: 5 vscope=1 Text boxes with
    txt_maxl=15, whose global-memory-directory records are 28 bytes each
    (align4(9 + txt_maxl + 1)) — the generalization of the single global_mixed
    Text (txt_maxl=10 → 20 B) that the lights oracle pinned (me/miata-dash#35,
    #37): the record's pco field + txt_maxl-driven size, and the matching
    per-Text project-shift contribution."""
    hmi = _LIGHTS / "lights.HMI"
    tft = _LIGHTS / "lights.tft"
    out = compile_hmi(str(hmi), str(tft))
    ref = tft.read_bytes()
    assert len(out) == len(ref), f"lights: size {len(out)} != {len(ref)}"
    if out != ref:
        first = next(i for i in range(len(out)) if out[i] != ref[i])
        raise AssertionError(
            f"compile(lights.HMI) diverges at offset {first:#x}: "
            f"got {out[first:first + 8].hex()} != {ref[first:first + 8].hex()}"
        )
