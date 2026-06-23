#!/usr/bin/env python3
"""tombstone_extract.py — surface recoverable history from a Nextion .HMI.

The `.HMI` source file is an **append-only journal** (see
[`findings/format-hmi.md`](../../findings/format-hmi.md) "Append-only
journal" and [`REPORT.md`](../../REPORT.md) §"The HMI is an append-only
journal"). Every editor save appends a fresh page/Program.s/font/main.HMI
blob and flips the *old* directory entry's `deleted` byte to 1
(tombstoning it) rather than overwriting in place. The live-directory
decoder (`scripts/lib/hmi_dir.parse_directory` consumers) silently drops
tombstoned entries, so earlier saves are invisible — but their `start`/
`size` still point at valid, parseable data.

This tool exposes that hidden history:

  list     every tombstoned entry: recovered name, size, file offset, and
           which live entry (if any) supersedes it.
  extract  the raw payload bytes of one tombstoned entry.
  diff     a live page/script blob vs a chosen tombstoned version of the
           same logical resource (byte count + first differing offsets).

Recovering identity
--------------------
A tombstoned entry's 16-byte directory `name` is **not** zeroed when the
slot is reclaimed — it holds stale buffer garbage (`format-hmi.md`,
"Stale names"). So we cannot trust the directory name for deleted
entries. Instead we classify each blob from its *payload*:

  * page         — PageHeader magic: `datainformationaddr == 0x38`,
                   `hmiffid == 0x4f` (+0x15), `filever == 0x21` (+0x16).
                   The real page name (e.g. "main") lives at +0x18.
  * program      — ASCII text beginning with `//`.
  * main-meta    — `hmifilehead`: `Datasize == 0x60` (+0x04),
                   `filever == 0x21` (+0x0a), `hmiffid == 0x4f` (+0x0e).
  * font         — `04 ff 00 0a` signature.

A tombstone is *superseded by* the live entry sharing its kind+identity:
for pages, the live `N.pa` whose embedded page name matches; for
program/main-meta, the (unique) live Program.s / main.HMI.

Usage
-----
    tombstone_extract.py file.HMI --list
    tombstone_extract.py file.HMI --extract <name> <index> -o old.pa
    tombstone_extract.py file.HMI --diff <name>
"""
from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.hmi_dir import (  # noqa: E402
    BACKUP_DIR_OFFSET,
    ENTRY_SIZE,
    parse_directory,
)

PAGE_INFO_ADDR = 0x38
PAGE_HMIFFID = 0x4F
PAGE_FILEVER = 0x21
MAIN_DATASIZE = 0x60
FONT_MAGIC = b"\x04\xff\x00\x0a"


# ---------------------------------------------------------------------------
# Blob classification — recover a resource's *kind* and *identity* from its
# payload, independent of the (possibly stale) directory name.
# ---------------------------------------------------------------------------

def _looks_like_page(blob: bytes) -> bool:
    if len(blob) < 0x38:
        return False
    (dinfoaddr,) = struct.unpack_from("<I", blob, 0x08)
    return (
        dinfoaddr == PAGE_INFO_ADDR
        and blob[0x15] == PAGE_HMIFFID
        and blob[0x16] == PAGE_FILEVER
    )


def _page_name(blob: bytes) -> str:
    return blob[0x18:0x28].split(b"\x00")[0].decode("latin-1", errors="replace")


def _looks_like_main(blob: bytes) -> bool:
    if len(blob) < MAIN_DATASIZE:
        return False
    (datasize,) = struct.unpack_from("<I", blob, 0x04)
    return datasize == MAIN_DATASIZE and blob[0x0A] == PAGE_FILEVER and blob[0x0E] == PAGE_HMIFFID


def classify_blob(blob: bytes):
    """Return (kind, identity) recovered from payload bytes.

    kind ∈ {"page", "program", "main-meta", "font", "unknown"}.
    identity is a stable key within a kind:
      page      -> embedded page name (e.g. "main")
      program   -> "Program.s"
      main-meta -> "main.HMI"
      font      -> None (fonts are matched positionally, not by name)
      unknown   -> None
    """
    if _looks_like_page(blob):
        return ("page", _page_name(blob))
    if blob[:2] == b"//":
        return ("program", "Program.s")
    if _looks_like_main(blob):
        return ("main-meta", "main.HMI")
    if blob[:4] == FONT_MAGIC:
        return ("font", None)
    return ("unknown", None)


# ---------------------------------------------------------------------------
# Entry model
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    index: int
    dir_name: str          # directory name field (garbage for tombstones)
    start: int
    size: int
    deleted: bool
    kind: str
    identity: str | None   # recovered identity (None for fonts/unknown)
    # for tombstones: index of the live entry that supersedes it (or None)
    superseded_by: int | None = None
    # human label: recovered name where possible, else the dir name in repr
    label: str = ""


def _blob(data: bytes, e) -> bytes:
    return data[e.start : e.start + e.size]


def load_entries(data: bytes) -> list[Entry]:
    """Parse the directory and classify every entry (live + tombstoned)."""
    out: list[Entry] = []
    for (i, name, start, size, deleted, t0, t1, t2, name_raw, off) in parse_directory(data):
        blob = data[start : start + size]
        kind, identity = classify_blob(blob)
        # Prefer the recovered page name as a label; for live entries fall back
        # to the (trustworthy) directory name.
        if kind == "page":
            label = f"{identity}.pa-content"  # page-name based; dir stem added below
        elif kind in ("program", "main-meta"):
            label = identity
        elif not deleted and name:
            label = name
        else:
            label = identity or ""
        out.append(
            Entry(
                index=i,
                dir_name=name,
                start=start,
                size=size,
                deleted=bool(deleted),
                kind=kind,
                identity=identity,
                label=label,
            )
        )
    return out


def _live_page_name_to_stem(entries: list[Entry]) -> dict[str, str]:
    """Map embedded page name -> live directory stem (e.g. "main" -> "0.pa").

    Live page entries have a trustworthy directory name (`N.pa`) AND a
    recoverable embedded page name; the join lets us label tombstoned pages
    with the stem of the page they are an older version of.
    """
    mapping: dict[str, str] = {}
    for e in entries:
        if e.kind == "page" and not e.deleted and e.dir_name:
            mapping[e.identity] = e.dir_name
    return mapping


def resolve_supersession(entries: list[Entry]) -> None:
    """Populate each tombstone's `superseded_by` (and refine its label).

    A tombstone is superseded by the live entry of the same kind+identity:
      * page      -> live page with the same embedded page name
      * program   -> the (unique) live Program.s
      * main-meta -> the (unique) live main.HMI
      * font      -> not resolved (fonts carry no in-band identity here)
    """
    live_by_key: dict[tuple[str, str | None], int] = {}
    for e in entries:
        if e.deleted:
            continue
        if e.kind == "page":
            live_by_key[("page", e.identity)] = e.index
        elif e.kind in ("program", "main-meta"):
            live_by_key[(e.kind, e.identity)] = e.index

    name_to_stem = _live_page_name_to_stem(entries)

    for e in entries:
        if not e.deleted:
            # refine live page label to its directory stem (e.g. "0.pa")
            if e.kind == "page" and e.dir_name:
                e.label = e.dir_name
            continue
        if e.kind == "page":
            e.superseded_by = live_by_key.get(("page", e.identity))
            stem = name_to_stem.get(e.identity)
            e.label = f"{stem}" if stem else f"{e.identity}.pa?"
        elif e.kind in ("program", "main-meta"):
            e.superseded_by = live_by_key.get((e.kind, e.identity))
            e.label = e.identity
        elif e.kind == "font":
            e.label = "font(.zi)"
        else:
            e.label = "unknown"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze(data: bytes) -> list[Entry]:
    """Full pipeline: parse + classify + resolve supersession."""
    entries = load_entries(data)
    resolve_supersession(entries)
    return entries


def live_entries(entries: list[Entry]) -> list[Entry]:
    return [e for e in entries if not e.deleted]


def tombstones(entries: list[Entry]) -> list[Entry]:
    return [e for e in entries if e.deleted]


def backup_matches_primary(data: bytes) -> bool:
    """True iff the backup directory at 0x80000 is byte-identical to primary."""
    (count,) = struct.unpack_from("<I", data, 0)
    n = 4 + count * ENTRY_SIZE
    return data[0:n] == data[BACKUP_DIR_OFFSET : BACKUP_DIR_OFFSET + n]


def find_tombstones_named(entries: list[Entry], name: str) -> list[Entry]:
    """Tombstones whose recovered label or stem matches `name`.

    `name` may be a directory stem ("0.pa"), a page name ("main"),
    "Program.s", or "main.HMI". Returned in directory order; the caller
    indexes them 0..N-1 for --extract.
    """
    name = name.strip()
    # Build name->page-name alias from live entries so "0.pa" resolves to "main".
    stem_to_pagename: dict[str, str] = {}
    for e in entries:
        if e.kind == "page" and not e.deleted and e.dir_name:
            stem_to_pagename[e.dir_name] = e.identity
    target_pagename = stem_to_pagename.get(name, name)

    matched: list[Entry] = []
    for e in tombstones(entries):
        if e.kind == "page" and (e.identity == name or e.identity == target_pagename or e.label == name):
            matched.append(e)
        elif e.kind in ("program", "main-meta") and (e.identity == name or e.label == name):
            matched.append(e)
    return matched


def live_entry_named(entries: list[Entry], name: str) -> Entry | None:
    """The single live entry matching `name` (stem, page name, or special)."""
    name = name.strip()
    for e in live_entries(entries):
        if e.kind == "page" and (e.dir_name == name or e.identity == name):
            return e
        if e.kind in ("program", "main-meta") and (e.identity == name or e.dir_name == name):
            return e
    return None


def diff_blobs(a: bytes, b: bytes, max_show: int = 16):
    """Return (n_diff, [(offset, a_byte, b_byte), ...]) for the first
    `max_show` differing offsets. If sizes differ, compares the common
    prefix and counts the size delta separately (reported by caller)."""
    n = min(len(a), len(b))
    diffs = []
    n_diff = 0
    for o in range(n):
        if a[o] != b[o]:
            n_diff += 1
            if len(diffs) < max_show:
                diffs.append((o, a[o], b[o]))
    return n_diff, diffs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_list(data: bytes) -> int:
    entries = analyze(data)
    live = live_entries(entries)
    tombs = tombstones(entries)
    print(f"live entries:      {len(live)}")
    print(f"tombstoned entries: {len(tombs)}")
    print(f"backup directory @0x{BACKUP_DIR_OFFSET:x}: "
          f"{'IDENTICAL' if backup_matches_primary(data) else 'MISMATCH'}")
    print()
    print(f"{'#':>2}  {'recovered':<14}  {'kind':<9}  {'start':>10}  {'size':>8}  superseded-by")
    print("-" * 72)
    for e in tombs:
        sup = "" if e.superseded_by is None else f"#{e.superseded_by}"
        sup_lbl = ""
        if e.superseded_by is not None:
            live_e = entries[e.superseded_by]
            sup_lbl = f" ({live_e.label})"
        print(
            f"{e.index:>2}  {e.label:<14}  {e.kind:<9}  0x{e.start:08x}  "
            f"{e.size:>8}  {sup}{sup_lbl}"
        )
    return 0


def _cmd_extract(data: bytes, name: str, index: int, out: Path) -> int:
    entries = analyze(data)
    matched = find_tombstones_named(entries, name)
    if not matched:
        print(f"no tombstoned entry recovered for name {name!r}", file=sys.stderr)
        avail = sorted({e.label for e in tombstones(entries) if e.label})
        print(f"available tombstone labels: {avail}", file=sys.stderr)
        return 2
    if index < 0 or index >= len(matched):
        print(f"index {index} out of range; {len(matched)} tombstone(s) "
              f"named {name!r} (valid 0..{len(matched)-1})", file=sys.stderr)
        return 2
    e = matched[index]
    payload = _blob(data, e)
    out.write_bytes(payload)
    print(f"extracted tombstone #{e.index} ({e.kind}, label={e.label!r}) "
          f"size={e.size} -> {out}")
    return 0


def _cmd_diff(data: bytes, name: str) -> int:
    entries = analyze(data)
    live_e = live_entry_named(entries, name)
    if live_e is None:
        print(f"no live entry named {name!r}", file=sys.stderr)
        return 2
    matched = find_tombstones_named(entries, name)
    if not matched:
        print(f"no tombstoned version of {name!r} to diff against", file=sys.stderr)
        return 2
    live_blob = _blob(data, live_e)
    print(f"live {name!r}: entry #{live_e.index} size={live_e.size}")
    for j, e in enumerate(matched):
        tomb_blob = _blob(data, e)
        n_diff, diffs = diff_blobs(live_blob, tomb_blob)
        size_note = ""
        if e.size != live_e.size:
            size_note = f"  (size differs: {e.size} vs {live_e.size}, " \
                        f"delta {live_e.size - e.size})"
        print(f"\n  tombstone[{j}] entry #{e.index} size={e.size}{size_note}")
        print(f"    differing bytes (common prefix): {n_diff}")
        for o, a, b in diffs:
            print(f"      +0x{o:04x}: live={a:02x} tomb={b:02x}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("hmi", type=Path, help="path to .HMI file")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true",
                   help="list all tombstoned entries + supersede mapping")
    g.add_argument("--extract", nargs=2, metavar=("NAME", "INDEX"),
                   help="extract the INDEX-th tombstone matching NAME "
                        "(stem like 0.pa, page name like main, "
                        "Program.s, or main.HMI)")
    g.add_argument("--diff", metavar="NAME",
                   help="diff the live NAME blob vs every tombstoned version")
    ap.add_argument("-o", "--output", type=Path,
                    help="output path (required with --extract)")
    args = ap.parse_args(argv)

    data = args.hmi.read_bytes()

    if args.list:
        return _cmd_list(data)
    if args.extract is not None:
        if args.output is None:
            ap.error("--extract requires -o/--output")
        name, index_s = args.extract
        try:
            index = int(index_s)
        except ValueError:
            ap.error(f"INDEX must be an integer, got {index_s!r}")
        return _cmd_extract(data, name, index, args.output)
    if args.diff is not None:
        return _cmd_diff(data, args.diff)
    return 0  # unreachable (group required)


if __name__ == "__main__":
    sys.exit(main())
