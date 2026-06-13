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


@dataclass
class MediaObject:
    """One object's contribution to the page media frame."""

    lei: int
    name: str
    txt: object = None   # the component's `txt` attribute, if any


@dataclass
class MediaFrameLayout:
    """Result of laying out one page's media frame."""

    objdatarampos: list[int]     # one entry per object, in objxinxi order
    frame_end: int               # cursor after the last block + spill
    medatapos: int               # frame_end - 16 (page-dir field)


def layout_page_frame(objects: list[MediaObject]) -> MediaFrameLayout:
    """Compute every object's ``objdatarampos`` and the page's
    ``medatapos`` from the page's object list (page pseudo-component
    first), purely from component type + the ``txt`` lengths.

    Reproduces ``source/nextion.hmi.tft`` byte-for-byte for all 4 pages.
    """
    n = len(objects)
    cursor = frame_start(n)
    positions: list[int] = []
    for obj in objects:
        positions.append(cursor)
        sz = SIZEOF_OBJDATA_T1.get(obj.lei, 0)
        cursor = _align4(cursor + sz)
        buf = INLINE_TXT_BUFFER.get(obj.lei)
        if buf is not None:
            blen = _txt_bytes(obj.txt)
            if blen + 1 > buf:
                cursor += _align4(blen + 1)
    return MediaFrameLayout(
        objdatarampos=positions,
        frame_end=cursor,
        medatapos=cursor - FRAME_PREFIX,
    )


def layout_from_hmi_page(page) -> MediaFrameLayout:
    """Lay out a page's media frame from an :class:`hmi_model.HmiPageModel`.

    The page model's ``components`` already lead with the synthesised page
    pseudo-component (lei 121), matching objxinxi order."""
    objects: list[MediaObject] = []
    for i, hc in enumerate(page.components):
        # The page pseudo-component (index 0) carries no txt.
        txt = None if i == 0 else hc.attrs.get("txt")
        objects.append(MediaObject(lei=hc.lei, name=hc.name, txt=txt))
    return layout_page_frame(objects)


__all__ = [
    "FRAME_PREFIX",
    "INLINE_TXT_BUFFER",
    "frame_start",
    "MediaObject",
    "MediaFrameLayout",
    "layout_page_frame",
    "layout_from_hmi_page",
]
