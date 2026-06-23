"""Tests for the HMI tombstone history extractor (me/miata-dash #11).

The `.HMI` source is an append-only journal: every editor save appends a
fresh blob and tombstones (flips `deleted=1` on) the old directory entry,
which the live decoder silently drops. `scripts/tools/tombstone_extract.py`
surfaces those recoverable earlier saves. Every assertion here is backed by
a real fixture — no synthetic data.

Oracle fixtures (real editor outputs in `tests/editor outputs/_old`):

  * ``save A.HMI`` — a tombstoned ``0.pa`` (page "main") of size **22123**
    that differs from the live ``0.pa`` in exactly **5 bytes** (4 in the
    leading page CRC + 1 content byte at +0x4a5). This is the concrete
    "older 0.pa differing in only ~7 of 22,123 bytes" case cited in
    ``REPORT.md`` / ``findings/format-hmi.md`` (the exact count is
    save-dependent; ``vertical.HMI`` shows the same 22123-byte page at a
    6-byte diff).
  * The live project fixture (``tests/fixtures/live/nextion.hmi.HMI``) and
    the repo-root project HMI carry 15 tombstones with a byte-identical
    backup directory at 0x80000.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.hmi_dir import (  # noqa: E402
    BACKUP_DIR_OFFSET,
    ENTRY_SIZE,
    parse_directory,
)
from scripts.tools.tombstone_extract import (  # noqa: E402
    analyze,
    backup_matches_primary,
    classify_blob,
    diff_blobs,
    find_tombstones_named,
    live_entries,
    live_entry_named,
    tombstones,
)

EDITOR_OUT = REPO_ROOT / "tests" / "editor outputs" / "_old"
SAVE_A = EDITOR_OUT / "save A.HMI"
VERTICAL = EDITOR_OUT / "vertical.HMI"
LIVE = REPO_ROOT / "tests" / "fixtures" / "live" / "nextion.hmi.HMI"
ROOT_HMI = REPO_ROOT.parent / "nextion.hmi.HMI"  # repo-root live project (parent repo)


def _read(p: Path) -> bytes:
    if not p.exists():
        pytest.skip(f"fixture missing: {p}")
    return p.read_bytes()


# ---------------------------------------------------------------------------
# Blob classification recovers identity from payload, not the stale dir name
# ---------------------------------------------------------------------------

def test_classify_page_program_main():
    data = _read(SAVE_A)
    entries = analyze(data)
    by_kind = {}
    for e in entries:
        by_kind.setdefault(e.kind, []).append(e)
    # save A has live + tombstoned pages, one Program.s, main.HMI copies
    assert "page" in by_kind
    assert "program" in by_kind
    assert "main-meta" in by_kind
    # every page entry recovered a real embedded page name
    for e in by_kind["page"]:
        assert e.identity in {"main", "gauge", "settings", "error"}, e.identity


def test_classify_blob_signatures():
    data = _read(SAVE_A)
    # entry 15 is the live 0.pa "main"; 14 the tombstoned one
    entries = list(parse_directory(data))
    live15 = entries[15]
    blob = data[live15[2] : live15[2] + live15[3]]
    assert classify_blob(blob) == ("page", "main")


# ---------------------------------------------------------------------------
# Live set extracted by the tool == parse_directory's live set (no overlap)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [SAVE_A, VERTICAL, LIVE])
def test_live_set_matches_parse_directory(path):
    data = _read(path)
    entries = analyze(data)
    tool_live = {e.index for e in live_entries(entries)}
    tool_tomb = {e.index for e in tombstones(entries)}
    # parse_directory is the oracle for live vs deleted (row[0]=index, row[4]=deleted)
    pd_live = {row[0] for row in parse_directory(data) if not row[4]}
    pd_tomb = {row[0] for row in parse_directory(data) if row[4]}
    assert tool_live == pd_live
    assert tool_tomb == pd_tomb
    # live and tombstone index sets are disjoint
    assert tool_live.isdisjoint(tool_tomb)


# ---------------------------------------------------------------------------
# Primary directory == backup directory at 0x80000
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [SAVE_A, VERTICAL, LIVE])
def test_backup_directory_identical(path):
    data = _read(path)
    assert backup_matches_primary(data)
    # cross-check the raw slice equality directly
    (count,) = struct.unpack_from("<I", data, 0)
    n = 4 + count * ENTRY_SIZE
    assert data[0:n] == data[BACKUP_DIR_OFFSET : BACKUP_DIR_OFFSET + n]


# ---------------------------------------------------------------------------
# The headline oracle: tombstoned 0.pa in save A, size 22123, 5-byte diff
# ---------------------------------------------------------------------------

def test_save_a_tombstoned_0pa_recovered():
    data = _read(SAVE_A)
    entries = analyze(data)
    matched = find_tombstones_named(entries, "0.pa")
    assert len(matched) == 1
    tomb = matched[0]
    assert tomb.index == 14
    assert tomb.kind == "page"
    assert tomb.identity == "main"
    assert tomb.size == 22123
    # superseded by the live 0.pa (entry 15)
    assert tomb.superseded_by == 15
    live15 = entries[15]
    assert live15.dir_name == "0.pa"
    assert live15.identity == "main"


def test_save_a_0pa_diff_is_five_bytes():
    data = _read(SAVE_A)
    entries = analyze(data)
    live = live_entry_named(entries, "0.pa")
    tomb = find_tombstones_named(entries, "0.pa")[0]
    assert live.size == tomb.size == 22123
    live_blob = data[live.start : live.start + live.size]
    tomb_blob = data[tomb.start : tomb.start + tomb.size]
    n_diff, diffs = diff_blobs(live_blob, tomb_blob, max_show=64)
    assert n_diff == 5
    offsets = [o for (o, _a, _b) in diffs]
    # 4 CRC bytes at the head + 1 content byte at +0x4a5
    assert offsets[:4] == [0, 1, 2, 3]
    assert 0x4A5 in offsets


def test_extract_tombstoned_0pa_payload(tmp_path):
    data = _read(SAVE_A)
    entries = analyze(data)
    tomb = find_tombstones_named(entries, "0.pa")[0]
    out = tmp_path / "old_0.pa"
    payload = data[tomb.start : tomb.start + tomb.size]
    out.write_bytes(payload)
    assert out.stat().st_size == 22123
    # the extracted payload is itself a valid page blob naming "main"
    assert classify_blob(out.read_bytes()) == ("page", "main")
    # and it is NOT the live copy (genuinely an older version)
    live = live_entry_named(entries, "0.pa")
    assert payload != data[live.start : live.start + live.size]


# ---------------------------------------------------------------------------
# Re-running the live decoder still picks the newest (live) copy
# ---------------------------------------------------------------------------

def test_live_decoder_picks_newest():
    """After identifying the tombstoned 0.pa, the live-directory decoder must
    still resolve `0.pa` to the *newest* (live, deleted=0) entry, never the
    tombstone — proving extraction is non-destructive / read-only."""
    data = _read(SAVE_A)
    entries = analyze(data)
    # the only live entry named 0.pa / main
    live = live_entry_named(entries, "0.pa")
    assert live is not None
    assert live.deleted is False
    assert live.index == 15
    # parse_directory (the live decoder) yields exactly one non-deleted 0.pa
    live_0pa = [
        row for row in parse_directory(data)
        if not row[4] and row[1] == "0.pa"
    ]
    assert len(live_0pa) == 1
    assert live_0pa[0][0] == 15


# ---------------------------------------------------------------------------
# A second, independent oracle: vertical.HMI same 22123-byte main page
# ---------------------------------------------------------------------------

def test_vertical_has_same_size_main_tombstone():
    data = _read(VERTICAL)
    entries = analyze(data)
    live = live_entry_named(entries, "0.pa")
    tombs = find_tombstones_named(entries, "0.pa")
    assert live is not None
    assert len(tombs) >= 1
    tomb = tombs[0]
    assert tomb.identity == "main"
    assert tomb.size == 22123 == live.size
    n_diff, _ = diff_blobs(
        data[live.start : live.start + live.size],
        data[tomb.start : tomb.start + tomb.size],
    )
    # small, single-digit diff (6 bytes in this save) — same family as save A
    assert 0 < n_diff < 10


# ---------------------------------------------------------------------------
# Live project fixture: 15 tombstones, pages map to their live N.pa
# ---------------------------------------------------------------------------

def test_live_fixture_tombstone_count_and_mapping():
    data = _read(LIVE)
    entries = analyze(data)
    tombs = tombstones(entries)
    assert len(tombs) == 15
    # the page tombstones each resolve to a live page of the same name
    page_tombs = [t for t in tombs if t.kind == "page"]
    assert page_tombs, "expected at least one tombstoned page in the live fixture"
    for t in page_tombs:
        assert t.superseded_by is not None
        live_e = entries[t.superseded_by]
        assert live_e.deleted is False
        assert live_e.identity == t.identity  # same embedded page name


def test_root_hmi_matches_if_present():
    """The repo-root project HMI (parent repo) is also an append-only journal;
    when present it must show the same structural invariants."""
    if not ROOT_HMI.exists():
        pytest.skip(f"root project HMI not present: {ROOT_HMI}")
    data = ROOT_HMI.read_bytes()
    entries = analyze(data)
    assert backup_matches_primary(data)
    assert len(tombstones(entries)) >= 1
    # 0.pa resolves to a live "main" page
    live = live_entry_named(entries, "0.pa")
    assert live is not None and live.identity == "main"


# ---------------------------------------------------------------------------
# extract / diff name resolution accepts stem, page name, and specials
# ---------------------------------------------------------------------------

def test_name_resolution_aliases():
    data = _read(SAVE_A)
    entries = analyze(data)
    # stem "0.pa" and page name "main" select the same tombstone
    by_stem = find_tombstones_named(entries, "0.pa")
    by_pagename = find_tombstones_named(entries, "main")
    assert [e.index for e in by_stem] == [e.index for e in by_pagename] == [14]
    # Program.s and main.HMI resolve too
    assert find_tombstones_named(entries, "Program.s")
    assert find_tombstones_named(entries, "main.HMI")
