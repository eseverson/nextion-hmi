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


def test_load_tft_alone_recovers_components(tft_path, tmp_path):
    """When no sibling HMI exists, the loader still recovers per-component
    type + position from the on-disk objdata_Ram records."""
    standalone = tmp_path / "alone.tft"
    standalone.write_bytes(tft_path.read_bytes())
    state = load_tft(standalone)

    # We don't get original page names without the HMI, but we do get
    # a page per TFT directory entry, each with valid geometry.
    assert len(state.pages) >= 1
    for p in state.pages.values():
        assert p.attrs["w"] > 0
        assert p.attrs["h"] > 0

    # Sum components across pages and compare to the HMI-loaded total.
    # The TFT contains every authored component; only the page-meta is
    # excluded from the components list (it's the page itself).
    from sim.loader import load_hmi
    hmi_state = load_hmi(tft_path.with_suffix(".HMI"))
    tft_total = sum(len(p.components) for p in state.pages.values())
    hmi_total = sum(len(p.components) for p in hmi_state.pages.values())
    assert tft_total == hmi_total, (
        f"TFT-only loader should recover the same component count as HMI: "
        f"got tft={tft_total} vs hmi={hmi_total}"
    )

    # Spot-check: every component has integer x/y/w/h.
    for p in state.pages.values():
        for c in p.components:
            assert isinstance(c.attrs["x"], int)
            assert isinstance(c.attrs["w"], int)
            assert c.attrs["w"] > 0 and c.attrs["h"] > 0


def test_load_tft_alone_matches_hmi_geometry(tft_path, tmp_path):
    """Component types and bounding boxes from the TFT-only path should
    match what the HMI loader produces — across all pages combined.

    The editor reorders pages during TFT compile, so TFT page id N is
    not necessarily the same page as HMI page id N. We compare the
    aggregated set of components, grouped by (page_signature, type, id,
    x, y, w, h), where page_signature is the page's component count —
    enough to disambiguate which TFT page corresponds to which HMI page
    without depending on order.

    Non-visual components (Timer, Variable) don't have x/y/w/h in the
    HMI loader's view, so for those we only compare type+id."""
    standalone = tmp_path / "alone.tft"
    standalone.write_bytes(tft_path.read_bytes())
    tft_state = load_tft(standalone)

    from sim.loader import load_hmi
    hmi_state = load_hmi(tft_path.with_suffix(".HMI"))

    NON_VISUAL = {51, 52}   # Timer, Variable

    def page_set(state):
        """Return a set of frozen-tuples per page; pages are matched
        across loaders by their content fingerprint, not their id."""
        result = set()
        for p in state.pages.values():
            entries = []
            for c in p.components:
                if c.type in NON_VISUAL:
                    entries.append((c.type, c.id))
                else:
                    entries.append((c.type, c.id, c.attrs["x"], c.attrs["y"],
                                    c.attrs["w"], c.attrs["h"]))
            result.add((len(entries), tuple(sorted(entries, key=str))))
        return result

    assert page_set(tft_state) == page_set(hmi_state), (
        "TFT-only and HMI loaders should produce the same per-page "
        "component signatures (modulo page ordering)."
    )


def test_load_tft_rejects_non_f_series(tmp_path):
    """An empty/garbage TFT should be rejected with a clear error."""
    bad = tmp_path / "bad.tft"
    bad.write_bytes(b"\x00" * 0x200)
    with pytest.raises(ValueError, match="xiliemark"):
        load_tft(bad)
