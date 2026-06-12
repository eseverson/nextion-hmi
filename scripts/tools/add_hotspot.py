#!/usr/bin/env python3
"""add_hotspot.py — add a Hotspot to a page of an existing .HMI file.

WARNING (me/nextion#1): this tool splices at the PCH-derived ``max_end``
and discards the bytes after it, which amputates the last component's
record overflow on any page whose components have event handlers. The
editor's real algorithm (isolated from fixtures 06->07) appends the
record at the true blob end with a PCH entry of
``(last_start + last_size + 12, len(record), 0)`` — see
``add_nav_hotspot_hmi.py`` for a correct, fixture-validated
implementation. Do not use this tool on non-trivial pages until it's
rewritten on that model.

First proof-of-concept of programmatic HMI authoring. Takes a target
.HMI and appends a Hotspot to the named page, following the editor's
append-only journal model (the old page payload is tombstoned in the
directory and the new larger payload is appended at EOF).

Usage:
    add_hotspot.py target.HMI -o new.HMI --page 0
                              [--x 0 --y 0 --w 60 --h 60]

What it does to the .HMI:
- Reads the live ``<page_id>.pa`` blob.
- Inserts a new 12-byte ``PageContentHeader`` at the end of the PCH
  array, shifts every existing PCH's ``startOffset`` by +12.
- Appends the 499-byte Hotspot template extracted verbatim from
  ``tests/editor outputs/_old/07_add_hotspot/`` (id, x, y, w, h
  patched per CLI args).
- Updates page header ``numberobj += 1`` and ``datasize``.
- Recomputes the page CRC.
- Writes the new blob to end-of-file, adds a new directory entry
  pointing at it, and tombstones the old ``<page_id>.pa`` entry.
- Mirrors the primary directory to the backup directory at 0x80000.

Limitations / known unknowns for v1:
- Doesn't update ``main.HMI`` (the project manifest) — works because the
  pa entry stays addressable by name. If the editor re-saves the
  project, it may rewrite the manifest.
- Doesn't sector-align the appended blob (the editor extends in 0x10238
  chunks; we just glue to EOF).
- Doesn't handle ``0.is`` / ``0.i`` companion entries — left untouched.
"""
from __future__ import annotations
import argparse
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.page_crc import page_crc, directory_checksum  # noqa: E402
from scripts.lib.hmi_dir import (BACKUP_DIR_OFFSET, ENTRY_FMT,  # noqa: E402
                                  ENTRY_SIZE, parse_directory)

# Hotspot template extracted from 07_add_hotspot fixture (object 30 on
# page 0). 499 bytes; the same layout the editor produces for an empty
# Hotspot at (0, 0, 60, 60) with name "m1" and id 30. The patcher below
# overwrites the id / x / y / w / h / endx / endy / objname fields.
_FIXTURE_07 = (REPO_ROOT / "tests" / "editor outputs" / "_old"
               / "07_add_hotspot" / "07.HMI")

HOTSPOT_TEMPLATE_SIZE = 511    # canonical (newer-editor) form: 14-byte
                                # objname field with typebyte 0x1e instead
                                # of the older 0x12 / 2-byte form (499 B).
HOTSPOT_TRAILING_SIZE = 56     # sub-record names for the Hotspot's events
PCH_SIZE = 12

# Exp 07's older 499-byte template (typebyte 0x12, max 2-byte objname).
_OLD_HOTSPOT_SIZE = 499
# Splice points within the old template, where the objname record lives.
_OLD_OBJNAME_RECORD_START = 0x6c
_OLD_OBJNAME_RECORD_END = 0x82


def _load_hotspot_template() -> bytes:
    """Return a 511-byte Hotspot byte template matching the canonical form
    the current Nextion Editor produces on save.

    Built by splicing the older 499-byte exp-07 template's prefix and
    suffix around a new 34-byte objname record that uses typebyte
    ``0x1e`` (= "string with 14-byte inline value"). Verified against
    ``/tmp/14_char_name.HMI`` — bytes [0..0x6c) and the suffix from the
    end of the new objname record onward are identical between the two
    templates."""
    raw = _FIXTURE_07.read_bytes()
    pa = _get_live_pa(raw, 0)
    old = pa[0x5644:0x5644 + _OLD_HOTSPOT_SIZE]
    prefix = old[:_OLD_OBJNAME_RECORD_START]
    suffix = old[_OLD_OBJNAME_RECORD_END:]
    new_objname = (
        b"\x1e\x00\x00\x00"           # typebyte 0x1e (string, 14-byte value)
        b"objname\x00"                 # 8-byte attr name (null-padded)
        b"\x00\x00\x00\x00\x00\x00\x00\x00"  # 8 bytes padding
        + b"\x00" * 14                 # value placeholder (patched later)
    )
    assert len(new_objname) == 34
    template = prefix + new_objname + suffix
    assert len(template) == HOTSPOT_TEMPLATE_SIZE
    return template


def _load_hotspot_trailing() -> bytes:
    """The 56 bytes after the Hotspot's component data in fixture 07. Length-
    prefixed sub-record names (``groupid1`` / ``codesdown-0`` / ``codesup-0``)
    that the editor expects when a Hotspot's event handlers are referenced.
    Without these the editor can render the page but throws two non-fatal
    "objedit ref error" dialogs on load."""
    raw = _FIXTURE_07.read_bytes()
    pa = _get_live_pa(raw, 0)
    return pa[0x5644 + _OLD_HOTSPOT_SIZE:
              0x5644 + _OLD_HOTSPOT_SIZE + HOTSPOT_TRAILING_SIZE]


def _get_live_pa(hmi: bytes, page_id: int) -> bytes:
    target = f"{page_id}.pa".encode()
    count = struct.unpack_from("<I", hmi, 0)[0]
    for i in range(count):
        off = 4 + i * ENTRY_SIZE
        name = hmi[off:off + 16].rstrip(b"\x00")
        if name != target or hmi[off + 24]:
            continue
        start = struct.unpack_from("<I", hmi, off + 16)[0]
        size = struct.unpack_from("<I", hmi, off + 20)[0]
        return hmi[start:start + size]
    raise KeyError(f"no live {page_id}.pa")


# Inside the 499-byte Hotspot template (offsets in template):
# Each attribute record has a 4-byte header (typebyte + 3 zero pad),
# an 8-byte name (null-padded ASCII), 8 bytes of value-area padding,
# and finally the value (1 byte for UU8/0x11, 2 bytes for UU16/0x12,
# 4 bytes for Sstr/0x14). So the value lives at record_start + 0x14
# (for 1-byte values it's at record_start + 0x14 since storage is
# LE-aligned to the start of the value area; for 2-byte values likewise).
# Offsets re-derived 2026-05-17 against the actual exp 07 template
# bytes; the previous values were 5+ bytes off and corrupted the
# adjacent attribute-name fields.
_PATCH_OFFSETS = {
    # (template_offset_of_value_first_byte, value_size_in_bytes)
    # Offsets shifted by +12 vs the older 499-byte template for every
    # field at-or-after the objname record (which grew from 22 to 34
    # bytes with typebyte 0x1e).
    "id":      (0x6b, 1),
    "objname": (0x80, 14),    # ASCII name, null-padded; max 14 bytes
    "x":       (0x122, 2),
    "y":       (0x138, 2),
    "w":       (0x14e, 2),
    "h":       (0x164, 2),
    "endx":    (0x17a, 2),
    "endy":    (0x190, 2),
}


def _patch_template(template: bytes, *, comp_id: int, name: str,
                    x: int, y: int, w: int, h: int) -> bytes:
    """Patch the editable fields in a copy of the Hotspot template."""
    out = bytearray(template)

    def _put(field: str, value: int | bytes):
        offset, size = _PATCH_OFFSETS[field]
        if isinstance(value, int):
            for i in range(size):
                out[offset + i] = (value >> (8 * i)) & 0xFF
        else:
            buf = bytes(value).ljust(size, b"\x00")[:size]
            out[offset:offset + size] = buf

    # "type" intentionally not patched — the template already has 109
    # (= GuiObj Hotspot) baked in at its value offset.
    _put("id", comp_id)
    _put("objname", name.encode("ascii"))
    _put("x", x)
    _put("y", y)
    _put("w", w)
    _put("h", h)
    _put("endx", x + w - 1)
    _put("endy", y + h - 1)
    return bytes(out)


def build_page_with_hotspot(base_pa: bytes, *, comp_id: int | None = None,
                            name: str | None = None,
                            x: int = 0, y: int = 0,
                            w: int = 60, h: int = 60) -> bytes:
    """Produce a new .pa blob with one Hotspot appended."""
    n_objs = struct.unpack_from("<I", base_pa, 12)[0]
    if comp_id is None:
        comp_id = n_objs
    if name is None:
        name = f"m{comp_id}"

    header = bytearray(base_pa[:0x38])
    pch_array = bytearray(base_pa[0x38:0x38 + n_objs * PCH_SIZE])

    # Each existing PCH's startOffset shifts by +12 (one new PCH inserted
    # at the end of the array).
    new_pchs = bytearray()
    max_end = 0
    for i in range(n_objs):
        s, sz, third = struct.unpack_from("<III", pch_array, i * PCH_SIZE)
        new_pchs.extend(struct.pack("<III", s + PCH_SIZE, sz, third))
        if s + sz > max_end:
            max_end = s + sz

    # The original .pa has page-level trailing metadata (length-prefixed
    # sub-record names) after the last component's data. We discard
    # baseline's trailing and replace it with the Hotspot's trailing so
    # the new component's event-handler sub-records (``groupid1`` /
    # ``codesdown-0`` / ``codesup-0``) are the only ones present.
    pch_array_end_in_blob = 0x38 + n_objs * PCH_SIZE
    components_only = bytearray(base_pa[pch_array_end_in_blob:max_end])

    new_comp_start = (0x38 + (n_objs + 1) * PCH_SIZE
                      + len(components_only))
    new_pchs.extend(struct.pack("<III", new_comp_start,
                                HOTSPOT_TEMPLATE_SIZE, 0))

    template = _patch_template(_load_hotspot_template(),
                               comp_id=comp_id, name=name,
                               x=x, y=y, w=w, h=h)
    trailing = _load_hotspot_trailing()

    blob = bytearray(bytes(header) + bytes(new_pchs)
                     + bytes(components_only) + template + trailing)
    struct.pack_into("<I", blob, 12, n_objs + 1)   # numberobj
    struct.pack_into("<I", blob, 4, len(blob))      # datasize
    crc = page_crc(bytes(blob))
    struct.pack_into("<I", blob, 0, crc)
    return bytes(blob)


def _set_dir_entry(buf: bytearray, dir_base: int, entry_idx: int, *,
                   name: bytes, start: int, size: int, deleted: bool,
                   tail3: bytes = b"\x00\x00\x00") -> None:
    """Overwrite one 28-byte directory entry. ``tail3`` are the three
    bytes at +0x19..+0x1b inside the entry; their semantics are not yet
    decoded (the editor uses non-zero values on many entries), so the
    caller should pass through the original bytes for entries it's
    modifying in place, and zero-fill for fresh entries.
    """
    off = dir_base + 4 + entry_idx * ENTRY_SIZE
    name_padded = name.ljust(16, b"\x00")[:16]
    struct.pack_into(
        ENTRY_FMT, buf, off,
        name_padded, start, size,
        1 if deleted else 0,
        tail3[0], tail3[1], tail3[2],
    )


def _bump_dir_count(buf: bytearray, dir_base: int, delta: int) -> int:
    count = struct.unpack_from("<I", buf, dir_base)[0]
    new_count = count + delta
    struct.pack_into("<I", buf, dir_base, new_count)
    return count  # old count (= index of newly-added entry)


def add_hotspot_to_hmi(hmi: bytes, *, page_id: int, x: int, y: int,
                      w: int, h: int, name: str | None = None) -> bytes:
    """Return a new HMI bytes object with one Hotspot added to ``page_id``.

    ``name`` overrides the default ``"m<id>"`` objname. Capped at 2
    ASCII bytes by the template's 0x12 (UU16-inline) value slot.
    """
    # 1. Build new .pa blob.
    old_pa = _get_live_pa(hmi, page_id)
    new_pa = build_page_with_hotspot(old_pa, x=x, y=y, w=w, h=h, name=name)

    # 2. Find the old entry to tombstone and the next free entry slot.
    target_name = f"{page_id}.pa".encode()
    out = bytearray(hmi)
    # Append the new blob at EOF.
    new_start = len(out)
    out.extend(new_pa)
    new_size = len(new_pa)

    # 3. Update primary directory in place: find the live entry for
    # ``<page_id>.pa`` and rewrite its start + size to point at the new
    # blob at EOF. We keep count and entry index unchanged; the old blob
    # in the middle of the file becomes orphaned but no live entry
    # references it, so the editor treats it as dead space. Tail bytes
    # at +0x19..+0x1b are preserved (semantics undecoded).
    primary_idx = None
    for entry in parse_directory(bytes(out)):
        i, name, _, _, deleted, *_ = entry
        if not deleted and name.encode("latin-1") == target_name:
            primary_idx = i
            break
    if primary_idx is None:
        raise RuntimeError(f"no live {page_id}.pa in primary directory")
    entry_off = 4 + primary_idx * ENTRY_SIZE
    _set_dir_entry(
        out, 0, primary_idx,
        name=target_name, start=new_start, size=new_size, deleted=False,
        tail3=bytes(out[entry_off + 25:entry_off + 28]),
    )

    # 4. Mirror to backup directory at 0x80000.
    out[BACKUP_DIR_OFFSET + entry_off:BACKUP_DIR_OFFSET + entry_off + ENTRY_SIZE] = (
        out[entry_off:entry_off + ENTRY_SIZE]
    )

    # 5. Recompute the 4-byte directory checksum the editor stores at
    # ``4 + count * 28`` (and mirrored at ``0x80000 + 4 + count * 28``).
    # This is ``CRC32_T(0xFFFFFFFF, directory_bytes + "ADEC")``; see
    # ``scripts/lib/page_crc.directory_checksum``.
    count = struct.unpack_from("<I", out, 0)[0]
    dir_end = 4 + count * ENTRY_SIZE
    new_checksum = directory_checksum(bytes(out[:dir_end]))
    struct.pack_into("<I", out, dir_end, new_checksum)
    struct.pack_into("<I", out, BACKUP_DIR_OFFSET + dir_end, new_checksum)

    return bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="source .HMI file")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--page", type=int, default=0,
                    help="page id to add the Hotspot to (default: 0)")
    ap.add_argument("--x", type=int, default=0)
    ap.add_argument("--y", type=int, default=0)
    ap.add_argument("--w", type=int, default=60)
    ap.add_argument("--h", type=int, default=60)
    ap.add_argument("--name", default=None,
                    help="Hotspot objname (max 14 ASCII bytes); "
                         "default = 'm<id>'")
    args = ap.parse_args()

    raw = Path(args.input).read_bytes()
    print(f"input: {args.input}  ({len(raw):,} bytes)")
    out = add_hotspot_to_hmi(raw, page_id=args.page,
                             x=args.x, y=args.y, w=args.w, h=args.h,
                             name=args.name)
    Path(args.output).write_bytes(out)
    print(f"output: {args.output}  ({len(out):,} bytes, "
          f"+{len(out) - len(raw)} bytes)")

    # Sanity-check the new .pa blob's CRC is valid.
    new_pa = _get_live_pa(out, args.page)
    crc_stored = struct.unpack_from("<I", new_pa, 0)[0]
    crc_calc = page_crc(new_pa)
    print(f"page {args.page} CRC: stored=0x{crc_stored:08x}  "
          f"recomputed=0x{crc_calc:08x}  "
          f"{'OK' if crc_stored == crc_calc else 'FAIL'}")

    n_objs = struct.unpack_from("<I", new_pa, 12)[0]
    print(f"page {args.page} numberobj: {n_objs} (new Hotspot id={n_objs - 1})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
