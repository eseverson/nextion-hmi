"""End-to-end acceptance gate for the MULTI-GLOBAL fixtures: compile == oracle.

These two fixtures (editor-produced, version 'D') exercise the multi-global
(``vscope=1``) per-page prefix allocator that ``media_allocator`` cracks in
me/miata-dash#37.  The baseline ``nextion`` project already carries THREE
global xfloats on its gauge page (x1, g0, g1); ``3global`` adds a fourth
(g2).  The earlier single-global allocator (``0x2c - status - SIZEOF*k``,
stacking downward) underflowed at the second global; the corrected rule
places the first global at ``0x2c - status_g`` and stacks UPWARD, and adds a
project-wide constant shift to every page's non-global region.

Both whole-file outputs are asserted byte-identical to the editor's TFT.
(The ``global_mixed`` oracle — which also has a global *Text* — is NOT yet
reproduced: a global Text serializes its txt into the global-memory directory
as a 28-byte record, which is the open #35 global-Text problem; see #37.)
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


@pytest.mark.parametrize("name", ["nextion", "3global"])
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
