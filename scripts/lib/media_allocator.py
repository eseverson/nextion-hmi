"""media_allocator — per-page private-memory frame layout (objdatarampos).

The editor lays out each page's runtime "private memory" frame and assigns
every object (the page pseudo-component + its user components) an
``objdatarampos`` — the byte offset of that object's data block within the
frame.  Those offsets are stored in the objxinxi entries and every
binattinf record, and the frame's total size drives the page directory's
``medatapos`` and the media-blob name-hash table terminator.

The frame layout, reverse-engineered from ``source/nextion.hmi.tft`` (all
4 pages reproduce every ``objdatarampos`` and ``medatapos`` byte-exact):

    +0                fixed 16-byte prefix (runtime header)
    +16               per-object status array: ceil(num_objects/4) u32
                      words (one packed byte per object)
    = FRAME_START     first object's objdatarampos
        for each object in objxinxi order:
            objdatarampos = cursor
            cursor += SIZEOF_OBJDATA_T1[lei]      (4-byte aligned)
            if the object is a Text/Button/Button_T whose `txt` doesn't
            fit its inline buffer, an inline spillover of
            round4(len(txt)+1) bytes follows the block.

``medatapos`` (the page directory field + ``terminator = medatapos + 8``
in the media-blob name-hash table) is ``frame_end - 16``.

This is the keystone the rest of work-item #2/#3 depends on: with
``objdatarampos`` known, the binattinf record values, the objxinxi
objdatarampos field, and the page-dir offsets all become HMI-derivable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from scripts.lib.memory_allocator import SIZEOF_OBJDATA_T1


# Fixed 16-byte runtime-header prefix before the per-object status array.
FRAME_PREFIX = 16

# Inline ``txt`` buffer capacity (bytes, incl. the NUL terminator budget)
# for the types that carry one in-block.  A ``txt`` whose byte length + 1
# exceeds this spills into an inline tail right after the object block.
# Derived from SIZEOF_OBJDATA_T1: Text(28) = 9 struct + ~16 buffer, Button
# / Button_T (24) = 13 struct + 11 buffer.
INLINE_TXT_BUFFER: dict[int, int] = {
    116: 16,   # Text
    98:  11,   # Button
    53:  11,   # Button_T
}


def _align4(n: int) -> int:
    return (n + 3) & ~3


def frame_start(num_objects: int) -> int:
    """The first object's ``objdatarampos`` for a page with ``num_objects``
    objects (page pseudo-component + user components):
    ``16 + 4*ceil(num_objects/4)``."""
    return FRAME_PREFIX + 4 * math.ceil(num_objects / 4)


def _txt_bytes(value) -> int:
    """Byte length of a ``txt`` attribute value (latin-1), or 0 if absent."""
    if not isinstance(value, str):
        return 0
    return len(value.encode("latin-1", "replace"))


# Newer-editor (version 'D') runtime-header prefix.  The 'D' editor reserves
# a 0x40-byte runtime header (vs 0x10 in the source/C build) and lays out
# *global-scope* (``vscope=1``) objects in a separate prefix slot instead of
# inline in the page sequence.  See ``findings/next-steps.md`` (live fixture)
# and me/miata-dash#37 (multi-global) for the full reverse-engineering.
FRAME_PREFIX_D = 0x40
# Anchor for the per-page global-object region.  The first global on a page
# lands at ``_VSCOPE_SLOT_END - status_g``, then successive globals STACK
# UPWARD (each at ``prev + SIZEOF``).  ``status_g`` is the status-array size
# computed over the page's NON-global object count (+1 for the page block):
# ``4*ceil((num_objects - num_globals + 1)/4)``.  Validated byte-exact across
# source/live (single global) + the 01_multi-global oracles (1..5 globals,
# xfloat + Text).
_VSCOPE_SLOT_END = 0x2C

# Per-global contribution to the project-wide non-global shift (see
# ``project_global_shift``).  A vscope=1 component pushes every page's
# non-global region up by this much — EXCEPT the project's first global,
# which is absorbed by the baseline prefix slot.  xfloat (59) = 0x18 = its
# SIZEOF; Text (116) = 0x14 (its frame SIZEOF is 0x1c, but it contributes
# only 0x14 to the shift — established from the single `global_mixed` Text
# oracle; see #37).  Other global-capable types are unverified — fall back
# to SIZEOF and flag.
_GLOBAL_SHIFT_SIZE: dict[int, int] = {
    59:  0x18,   # XFloat
    116: 0x14,   # Text (≠ SIZEOF 0x1c; one-oracle value, see #37)
}


@dataclass
class MediaObject:
    """One object's contribution to the page media frame."""

    lei: int
    name: str
    txt: object = None   # the component's `txt` attribute, if any
    vscope: bool = False  # global-scope (``vscope=1``) — 'D' prefix slot


@dataclass
class MediaFrameLayout:
    """Result of laying out one page's media frame."""

    objdatarampos: list[int]     # one entry per object, in objxinxi order
    frame_end: int               # cursor after the last block + spill
    medatapos: int               # frame_end - FRAME_PREFIX (page-dir field)


def _obj_advance(cursor: int, obj: MediaObject) -> int:
    """Advance ``cursor`` past one object's block (+ inline-txt spill)."""
    cursor = _align4(cursor + SIZEOF_OBJDATA_T1.get(obj.lei, 0))
    buf = INLINE_TXT_BUFFER.get(obj.lei)
    if buf is not None:
        blen = _txt_bytes(obj.txt)
        if blen + 1 > buf:
            cursor += _align4(blen + 1)
    return cursor


def project_global_shift(pages) -> int:
    """The project-wide constant added to EVERY 'D'-build page's non-global
    region base.  Each ``vscope=1`` component instance (unique by objname,
    in page order) pushes the non-global region up — EXCEPT the project's
    first global, which the baseline prefix slot absorbs.

    ``pages`` is the device-id-ordered :class:`hmi_model.HmiPageModel` list.
    Returns 0 for a project with ≤1 distinct global (source/live), so the
    single/zero-global path is unperturbed."""
    seen: set[str] = set()
    total = 0
    first = True
    for hp in pages:
        for c in hp.components:
            if c.attrs.get("vscope") == 1 and c.name not in seen:
                seen.add(c.name)
                if first:
                    first = False          # first global → absorbed, no shift
                else:
                    total += _GLOBAL_SHIFT_SIZE.get(
                        c.lei, SIZEOF_OBJDATA_T1.get(c.lei, 0))
    return total


def layout_page_frame(objects: list[MediaObject],
                      frame_prefix: int = FRAME_PREFIX,
                      project_shift: int = 0) -> MediaFrameLayout:
    """Compute every object's ``objdatarampos`` and the page's
    ``medatapos`` from the page's object list (page pseudo-component
    first), purely from component type + the ``txt`` lengths.

    ``frame_prefix`` selects the editor build's runtime-header size:
    ``FRAME_PREFIX`` (0x10) for the source/C layout (every object inline,
    reproduces ``source/nextion.hmi.tft`` byte-for-byte), or
    ``FRAME_PREFIX_D`` (0x40) for the newer 'D' build, which pulls
    ``vscope=1`` objects into a separate per-page region.

    ``project_shift`` (from :func:`project_global_shift`) is the constant the
    'D' build adds to the non-global region base on every page because of the
    project's globals beyond the first; 0 for ≤1-global projects.

    'D'-build RAM layout (validated byte-exact on the 01_multi-global oracles
    + source/live):

      * **Globals** (``vscope=1``), in component order, occupy their own
        region: the first lands at ``0x2c - status_g`` where
        ``status_g = 4*ceil((n - num_globals + 1)/4)``; each next stacks
        UPWARD at ``prev + SIZEOF`` (txt spill included).
      * **Non-globals** start at ``frame_prefix + 4*ceil(n/4) + project_shift``
        and run sequentially; when a global was pulled AND the page carries
        Variables, a ``pulled``-size zero hole precedes the first trailing
        non-visual (hotspot/timer/variable).

    ``medatapos``/``frame_end`` describe the ON-DISK frame (which OMITS the
    global blocks and carries NO project shift), so they are computed from a
    separate disk cursor — unchanged from the single-global path."""
    n = len(objects)
    status = 4 * math.ceil(n / 4)

    if frame_prefix != FRAME_PREFIX_D:
        # Source/C: a single inline sequence from frame_start.
        cursor = frame_prefix + status
        positions = []
        for obj in objects:
            positions.append(cursor)
            cursor = _obj_advance(cursor, obj)
        return MediaFrameLayout(positions, cursor, cursor - frame_prefix)

    # Version 'D'.
    vs_idx = [i for i, o in enumerate(objects) if o.vscope]
    num_globals = len(vs_idx)
    pulled = sum(SIZEOF_OBJDATA_T1.get(objects[i].lei, 0) for i in vs_idx)
    has_vars = any(o.lei == 52 for o in objects)

    hole_before = None
    if vs_idx and has_vars:
        for i, o in enumerate(objects):
            if i not in vs_idx and o.lei in (51, 52, 109):
                hole_before = i
                break

    positions = [0] * n

    # Global region: first at 0x2c - status_g, then stack UPWARD by SIZEOF.
    status_g = 4 * math.ceil((n - num_globals + 1) / 4)
    g_cursor = _VSCOPE_SLOT_END - status_g
    for i in vs_idx:
        positions[i] = g_cursor
        g_cursor = _obj_advance(g_cursor, objects[i])

    # Non-global region: frame_prefix + status + project_shift, sequential,
    # with the variables hole.  This is also the disk cursor MINUS the shift,
    # so track the disk frame_end separately (globals omitted, no shift).
    nonglob_base = frame_prefix + status + project_shift
    disk_base = frame_prefix + status
    cursor = nonglob_base
    disk_cursor = disk_base
    for i, obj in enumerate(objects):
        if i in vs_idx:
            continue
        if i == hole_before:
            cursor += pulled
            disk_cursor += pulled
        positions[i] = cursor
        cursor = _obj_advance(cursor, obj)
        disk_cursor = _obj_advance(disk_cursor, obj)

    return MediaFrameLayout(positions, disk_cursor, disk_cursor - frame_prefix)


def layout_from_hmi_page(page, frame_prefix: int = FRAME_PREFIX,
                         project_shift: int = 0) -> MediaFrameLayout:
    """Lay out a page's media frame from an :class:`hmi_model.HmiPageModel`.

    The page model's ``components`` already lead with the synthesised page
    pseudo-component (lei 121), matching objxinxi order.  ``frame_prefix``
    selects the editor build (see :func:`layout_page_frame`); ``project_shift``
    is the project-wide non-global base shift (see :func:`project_global_shift`),
    relevant only to the 'D' build's multi-global pages."""
    objects: list[MediaObject] = []
    for i, hc in enumerate(page.components):
        # The page pseudo-component (index 0) carries no txt.
        txt = None if i == 0 else hc.attrs.get("txt")
        objects.append(MediaObject(lei=hc.lei, name=hc.name, txt=txt,
                                   vscope=hc.attrs.get("vscope") == 1))
    return layout_page_frame(objects, frame_prefix, project_shift)


__all__ = [
    "FRAME_PREFIX",
    "FRAME_PREFIX_D",
    "INLINE_TXT_BUFFER",
    "frame_start",
    "MediaObject",
    "MediaFrameLayout",
    "layout_page_frame",
    "layout_from_hmi_page",
    "project_global_shift",
]
