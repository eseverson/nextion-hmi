"""Acceptance gate for the from-scratch author CLI (me/miata-dash#2 item 6).

The discipline is STRICT byte-exactness.  For each oracle project:

    ir_from_hmi(project.HMI)  →  author_tft / author_main_hmi  →  cmp

must equal the reference ``.tft`` and the embedded ``main.HMI`` blob
byte-for-byte.  Because the author path reconstructs the compiler's page
models from the IR and drives the IDENTICAL byte-exact pipeline
(:func:`compile_hmi.compile_from_model`), the TFT round-trip inherits the
HMI→TFT compiler's guarantee; the main.HMI round-trip is via
:func:`main_hmi.write_main_hmi`.

The IR is also round-tripped through disk (JSON and YAML) — the real
hand-editable authoring path — to prove serialization is lossless.

The fonts/pictures resources blob (``0x10000``..``0x70000``) + ``Modelcrc``
are OUT OF SCOPE: copied from the ``--resources`` reference (= the project's
own reference TFT here), exactly as in :func:`compile_hmi.compile_hmi`.

Fixture binaries are gitignored (present in the working tree but kept out of
the public mirror), so each case is skipped if its files are absent.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.author import author_tft, author_main_hmi, author  # noqa: E402
from scripts.lib.project_ir import (  # noqa: E402
    ir_from_hmi, load_ir, save_ir, ir_to_dict, ir_from_dict,
    _extract_main_hmi_blob,
)

# (label, hmi, tft) — every oracle pair we hold.
_CASES = [
    ("source", REPO_ROOT / "source" / "nextion.hmi.HMI",
     REPO_ROOT / "source" / "nextion.hmi.tft"),
    ("live", REPO_ROOT / "tests" / "fixtures" / "live" / "nextion.hmi.HMI",
     REPO_ROOT / "tests" / "fixtures" / "live" / "nextion.hmi.tft"),
    ("mg_nextion", REPO_ROOT / "tests" / "fixtures" / "multi_global" / "nextion.HMI",
     REPO_ROOT / "tests" / "fixtures" / "multi_global" / "nextion.tft"),
    ("mg_3global", REPO_ROOT / "tests" / "fixtures" / "multi_global" / "3global.HMI",
     REPO_ROOT / "tests" / "fixtures" / "multi_global" / "3global.tft"),
    ("mg_mixed", REPO_ROOT / "tests" / "fixtures" / "multi_global" / "global_mixed.HMI",
     REPO_ROOT / "tests" / "fixtures" / "multi_global" / "global_mixed.tft"),
]


def _require(hmi: Path, tft: Path) -> None:
    if not hmi.exists() or not tft.exists():
        pytest.skip(f"oracle fixtures not present ({hmi.name})")


def _first_diff(a: bytes, b: bytes) -> str:
    n = min(len(a), len(b))
    i = next((k for k in range(n) if a[k] != b[k]), n)
    return (f"first diff at {i:#x}: {a[i:i + 8].hex()} != {b[i:i + 8].hex()} "
            f"(lens {len(a)} vs {len(b)})")


@pytest.mark.parametrize("label,hmi,tft", _CASES, ids=[c[0] for c in _CASES])
def test_author_tft_round_trip_byte_identical(label, hmi, tft):
    """ir_from_hmi → author_tft reproduces the reference TFT byte-for-byte."""
    _require(hmi, tft)
    ir = ir_from_hmi(hmi)
    out = author_tft(ir, str(tft))
    ref = tft.read_bytes()
    assert out == ref, f"[{label}] TFT not byte-identical: {_first_diff(out, ref)}"


@pytest.mark.parametrize("label,hmi,tft", _CASES, ids=[c[0] for c in _CASES])
def test_author_main_hmi_round_trip_byte_identical(label, hmi, tft):
    """ir_from_hmi → author_main_hmi reproduces the embedded main.HMI blob."""
    _require(hmi, tft)
    ir = ir_from_hmi(hmi)
    out = author_main_hmi(ir)
    ref = _extract_main_hmi_blob(hmi)
    assert out == ref, f"[{label}] main.HMI not byte-identical: {_first_diff(out, ref)}"


@pytest.mark.parametrize("ext", [".json", ".yaml"])
@pytest.mark.parametrize("label,hmi,tft", _CASES, ids=[c[0] for c in _CASES])
def test_author_via_disk_serialization(label, hmi, tft, ext):
    """The IR survives a disk round-trip (the real hand-editable path):
    HMI → IR → <file> → IR → author → cmp, byte-identical TFT + main.HMI."""
    _require(hmi, tft)
    ir = ir_from_hmi(hmi)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / f"project{ext}"
        save_ir(ir, p)
        ir2 = load_ir(p)
    out_tft = author_tft(ir2, str(tft))
    out_hmi = author_main_hmi(ir2)
    ref_tft = tft.read_bytes()
    ref_hmi = _extract_main_hmi_blob(hmi)
    assert out_tft == ref_tft, f"[{label}{ext}] TFT: {_first_diff(out_tft, ref_tft)}"
    assert out_hmi == ref_hmi, f"[{label}{ext}] main.HMI: {_first_diff(out_hmi, ref_hmi)}"


def test_ir_dict_round_trip_is_lossless():
    """ir_to_dict → ir_from_dict reproduces the same authored bytes."""
    hmi, tft = _CASES[0][1], _CASES[0][2]
    _require(hmi, tft)
    ir = ir_from_hmi(hmi)
    ir2 = ir_from_dict(ir_to_dict(ir))
    assert author_tft(ir2, str(tft)) == author_tft(ir, str(tft))
    assert author_main_hmi(ir2) == author_main_hmi(ir)


def test_author_cli_emits_both_files(tmp_path):
    """The ``author(...)`` driver writes <prefix>.tft + <prefix>.HMI, both
    byte-identical to the reference (drives the same path as ``nxt author``)."""
    hmi, tft = _CASES[0][1], _CASES[0][2]
    _require(hmi, tft)
    ir_path = tmp_path / "project.json"
    save_ir(ir_from_hmi(hmi), ir_path)

    out_prefix = tmp_path / "out"
    tft_path, hmi_path = author(str(ir_path), str(tft), str(out_prefix))

    assert tft_path.read_bytes() == tft.read_bytes()
    assert hmi_path.read_bytes() == _extract_main_hmi_blob(hmi)
