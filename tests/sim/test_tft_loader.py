"""TFT loader smoke tests.

Loading a `.tft` should always produce a valid DisplayState. When a
sibling `.HMI` is present, components come from there (full fidelity).
Without one, the loader falls back to a skeleton with the right number
of pages at the right screen size.
"""
from __future__ import annotations
import shutil
from pathlib import Path

import pytest

from sim.loader import load
from sim.tft_loader import load_tft


def test_load_tft_with_sibling_hmi(tft_path):
    """source/nextion.hmi.tft has a sibling nextion.hmi.HMI — components
    should come from the HMI loader."""
    state = load_tft(tft_path)
    assert "main" in state.pages
    assert "settings" in state.pages
    main = state.pages["main"]
    assert main.by_name("x0") is not None
    assert state.orientation in (0, 90, 180, 270)


def test_load_dispatch_routes_tft(tft_path):
    """`sim.loader.load(...)` should route .tft → tft_loader and .HMI → hmi_loader."""
    state = load(tft_path)
    assert state.pages
    # If sibling HMI is present we get the same fidelity as load_hmi.
    assert "main" in state.pages


def test_load_tft_alone_falls_back_to_skeleton(tft_path, tmp_path):
    """When no sibling HMI exists, the loader returns pages with empty
    components but valid screen geometry."""
    # Copy just the .tft into a tmp dir (no sibling HMI).
    standalone = tmp_path / "alone.tft"
    standalone.write_bytes(tft_path.read_bytes())
    state = load_tft(standalone)
    assert len(state.pages) >= 1
    # Skeleton mode: each page has zero components but a valid w/h.
    for p in state.pages.values():
        assert p.components == []
        assert p.attrs["w"] > 0
        assert p.attrs["h"] > 0


def test_load_tft_rejects_non_f_series(tmp_path):
    """An empty/garbage TFT should be rejected with a clear error."""
    bad = tmp_path / "bad.tft"
    bad.write_bytes(b"\x00" * 0x200)
    with pytest.raises(ValueError, match="xiliemark"):
        load_tft(bad)
