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
# for the full reverse-engineering.
FRAME_PREFIX_D = 0x40
# The global-scope object's prefix slot ends at this fixed offset minus the
# page status-array size: vscope_objdatarampos = ``VSCOPE_SLOT_END - status``
# (validated: page0 status 0x20 → 0x0c, page1 status 0x08 → 0x24).
_VSCOPE_SLOT_END = 0x2C


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


def layout_page_frame(objects: list[MediaObject],
                      frame_prefix: int = FRAME_PREFIX) -> MediaFrameLayout:
    """Compute every object's ``objdatarampos`` and the page's
    ``medatapos`` from the page's object list (page pseudo-component
    first), purely from component type + the ``txt`` lengths.

    ``frame_prefix`` selects the editor build's runtime-header size:
    ``FRAME_PREFIX`` (0x10) for the source/C layout (every object inline,
    reproduces ``source/nextion.hmi.tft`` byte-for-byte), or
    ``FRAME_PREFIX_D`` (0x40) for the newer 'D' build, which pulls
    ``vscope=1`` objects into a prefix slot and reserves a zero hole for the
    pulled block ahead of the trailing non-visual cluster.
    """
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

    # Version 'D': global-scope (vscope) objects live in the prefix; the
    # rest are sequential after the page block.  When a vscope was pulled
    # AND the page carries variables, a zero hole the size of the pulled
    # block precedes the first trailing non-visual (hotspot/timer/variable).
    vs_idx = [i for i, o in enumerate(objects) if o.vscope]
    pulled = sum(SIZEOF_OBJDATA_T1.get(objects[i].lei, 0) for i in vs_idx)
    has_vars = any(o.lei == 52 for o in objects)

    hole_before = None
    if vs_idx and has_vars:
        for i, o in enumerate(objects):
            if i not in vs_idx and o.lei in (51, 52, 109):
                hole_before = i
                break

    positions = [0] * n
    # Prefix slots for vscope objects (stacked downward for multiples).
    for k, i in enumerate(vs_idx):
        positions[i] = _VSCOPE_SLOT_END - status \
            - SIZEOF_OBJDATA_T1.get(objects[i].lei, 0) * k

    cursor = frame_prefix + status
    for i, obj in enumerate(objects):
        if i in vs_idx:
            continue
        if i == hole_before:
            cursor += pulled
        positions[i] = cursor
        cursor = _obj_advance(cursor, obj)

    return MediaFrameLayout(positions, cursor, cursor - frame_prefix)


def layout_from_hmi_page(page, frame_prefix: int = FRAME_PREFIX) -> MediaFrameLayout:
    """Lay out a page's media frame from an :class:`hmi_model.HmiPageModel`.

    The page model's ``components`` already lead with the synthesised page
    pseudo-component (lei 121), matching objxinxi order.  ``frame_prefix``
    selects the editor build (see :func:`layout_page_frame`)."""
    objects: list[MediaObject] = []
    for i, hc in enumerate(page.components):
        # The page pseudo-component (index 0) carries no txt.
        txt = None if i == 0 else hc.attrs.get("txt")
        objects.append(MediaObject(lei=hc.lei, name=hc.name, txt=txt,
                                   vscope=hc.attrs.get("vscope") == 1))
    return layout_page_frame(objects, frame_prefix)


__all__ = [
    "FRAME_PREFIX",
    "FRAME_PREFIX_D",
    "INLINE_TXT_BUFFER",
    "frame_start",
    "MediaObject",
    "MediaFrameLayout",
    "layout_page_frame",
    "layout_from_hmi_page",
]
