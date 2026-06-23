"""CLI dispatch tests for scripts/nxt.py.

Smoke-tests each subcommand: verifies that `nxt <cmd>` dispatches
correctly (produces output, exits cleanly, etc.) without re-testing the
underlying library logic (which existing tests cover).

Tests that require the source HMI/TFT are skipped when those files are
absent (CI / sandboxed environments).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NXT = [sys.executable, str(REPO_ROOT / "scripts" / "nxt.py")]
MINIMAL_HMI = REPO_ROOT / "tests" / "editor outputs" / "23_minimal_project" / "23.HMI"
MINIMAL_TFT = REPO_ROOT / "tests" / "editor outputs" / "23_minimal_project" / "23.tft"
OLD = REPO_ROOT / "tests" / "editor outputs" / "_old"


def _run(*args: str, expect_fail: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(NXT + list(args), capture_output=True, text=True)
    if not expect_fail:
        assert r.returncode == 0, (
            f"nxt {' '.join(args)} exited {r.returncode}\n"
            f"stdout: {r.stdout[:500]}\nstderr: {r.stderr[:500]}"
        )
    return r


# ---------------------------------------------------------------------------
# Help / top-level
# ---------------------------------------------------------------------------

def test_help():
    r = _run("--help")
    assert "inspect-hmi" in r.stdout
    assert "patch-tft" in r.stdout
    assert "hotspot" in r.stdout
    assert "tombstones" in r.stdout


def test_no_args_prints_help():
    r = _run()
    assert "SUBCOMMAND" in r.stdout or "inspect-hmi" in r.stdout


# ---------------------------------------------------------------------------
# inspect-hmi / inspect-tft
# ---------------------------------------------------------------------------

def test_inspect_hmi_text():
    r = _run("inspect-hmi", str(MINIMAL_HMI))
    assert "page" in r.stdout


def test_inspect_hmi_json():
    import json
    r = _run("inspect-hmi", "--json", str(MINIMAL_HMI))
    data = json.loads(r.stdout)
    assert "pages" in data


def test_inspect_tft_text():
    r = _run("inspect-tft", str(MINIMAL_TFT))
    assert "resolution" in r.stdout or "magic" in r.stdout


# ---------------------------------------------------------------------------
# tombstones
# ---------------------------------------------------------------------------

_SAVE_A = OLD / "save A.HMI"


def test_tombstones_list():
    if not _SAVE_A.exists():
        pytest.skip("fixture missing")
    r = _run("tombstones", str(_SAVE_A), "--list")
    assert "tombstoned entries" in r.stdout
    assert "IDENTICAL" in r.stdout  # backup directory check
    assert "0.pa" in r.stdout


def test_tombstones_diff():
    if not _SAVE_A.exists():
        pytest.skip("fixture missing")
    r = _run("tombstones", str(_SAVE_A), "--diff", "0.pa")
    assert "differing bytes" in r.stdout


def test_tombstones_extract(tmp_path):
    if not _SAVE_A.exists():
        pytest.skip("fixture missing")
    out = tmp_path / "old_0.pa"
    r = _run("tombstones", str(_SAVE_A), "--extract", "0.pa", "0", "-o", str(out))
    assert out.exists()
    assert out.stat().st_size == 22123
    assert "extracted" in r.stdout


def test_tombstones_requires_mode():
    if not _SAVE_A.exists():
        pytest.skip("fixture missing")
    # no --list/--extract/--diff -> argparse error
    r = _run("tombstones", str(_SAVE_A), expect_fail=True)
    assert r.returncode != 0


def test_inspect_tft_json():
    import json
    r = _run("inspect-tft", "--json", str(MINIMAL_TFT))
    data = json.loads(r.stdout)
    assert "h1" in data


# ---------------------------------------------------------------------------
# diff-hmi / diff-tft / diff-bytes
# ---------------------------------------------------------------------------

def test_diff_hmi_identical():
    r = _run("diff-hmi", str(MINIMAL_HMI), str(MINIMAL_HMI))
    # Diffing a file against itself: all entries identical, 0 differ.
    assert "0 differ" in r.stdout or "identical" in r.stdout


def test_diff_tft_identical():
    r = _run("diff-tft", str(MINIMAL_TFT), str(MINIMAL_TFT))
    assert "identical" in r.stdout


def test_diff_bytes_identical():
    r = _run("diff-bytes", str(MINIMAL_TFT), str(MINIMAL_TFT))
    assert "identical" in r.stdout


def test_diff_hmi_two_saves():
    """Two different HMI saves should show differences."""
    a = OLD / "00_baseline" / "base.HMI"
    b = OLD / "05_text_qqqqqqqq" / "05.HMI"
    if not (a.exists() and b.exists()):
        pytest.skip("old fixture HMIs not present")
    r = _run("diff-hmi", str(a), str(b))
    assert "differ" in r.stdout


# ---------------------------------------------------------------------------
# patch-tft --verify
# ---------------------------------------------------------------------------

def test_patch_tft_verify():
    r = _run("patch-tft", str(MINIMAL_TFT), "--verify")
    assert "OK" in r.stdout
    assert "MISMATCH" not in r.stdout


# ---------------------------------------------------------------------------
# patch-hmi round-trip
# ---------------------------------------------------------------------------

def test_patch_hmi_roundtrip(tmp_path):
    """Patch a u8 in the minimal HMI, then verify the file is still parseable."""
    out = tmp_path / "patched.HMI"
    # Page 0, offset 4 is datasize (u32); use a safe same-length u8 patch to
    # a known-zero byte deep in the page rather than corrupting the header.
    # Instead just use --verify mode to check the baseline.
    # We actually patch and verify the page CRC is updated:
    _run("patch-hmi", str(MINIMAL_HMI), "--page", "0", "--offset", "8",
         "--u8", "0", "-o", str(out), "--verify")
    assert out.exists()


# ---------------------------------------------------------------------------
# resize-str round-trip
# ---------------------------------------------------------------------------

def test_resize_str_roundtrip(tmp_path):
    """resize-str on the baseline HMI fixture produces a page with updated size."""
    base = OLD / "00_baseline" / "base.HMI"
    if not base.exists():
        pytest.skip("old fixture base.HMI not present")
    out = tmp_path / "resized.HMI"
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    from scripts.tools.resize_str_hmi import _get_live_pa
    raw = base.read_bytes()
    _, pa = _get_live_pa(raw, 0)
    offset = pa.find(b"kPa")
    if offset < 0:
        pytest.skip("kPa string not found in fixture")
    _run("resize-str", str(base), "--page", "0",
         "--offset", str(offset), "--str", "MAP", "-o", str(out))
    assert out.exists()
    new_raw = out.read_bytes()
    assert b"MAP" in new_raw


# ---------------------------------------------------------------------------
# hotspot (add-nav-hotspot-hmi) round-trip
# ---------------------------------------------------------------------------

def test_hotspot_roundtrip(tmp_path):
    """hotspot subcommand adds a nav hotspot to the project HMI without error."""
    hmi = REPO_ROOT / "source" / "nextion.hmi.HMI"
    if not hmi.exists():
        pytest.skip("source HMI not available")
    out = tmp_path / "with_hotspot.HMI"
    _run("hotspot", str(hmi), "--page", "0", "--target-page", "2", "-o", str(out))
    assert out.exists()
    assert out.stat().st_size > hmi.stat().st_size


# ---------------------------------------------------------------------------
# list-tft
# ---------------------------------------------------------------------------

def test_list_tft():
    r = _run("list-tft", str(MINIMAL_TFT))
    assert "page" in r.stdout or "pages" in r.stdout


# ---------------------------------------------------------------------------
# Unsafe gate
# ---------------------------------------------------------------------------

def test_unsafe_gate_rejects_without_flag():
    r = subprocess.run(
        NXT + ["add-hotspot-tft", str(MINIMAL_TFT), "-o", "/dev/null"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "hardware-invalid" in r.stderr or "UNSAFE" in r.stderr.upper() or "--unsafe" in r.stderr


def test_unsafe_gate_rejects_add_xfloat_without_flag():
    r = subprocess.run(
        NXT + ["add-xfloat-tft", str(MINIMAL_TFT), "-o", "/dev/null"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "--unsafe" in r.stderr
