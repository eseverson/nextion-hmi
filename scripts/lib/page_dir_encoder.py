"""page_dir_encoder — generate the page directory (16 bytes / page).

The page directory lives at ``appinf1.pageadd``; one 16-byte record per
page (in device-id order):

    +0  u16  objstar          (index of this page's first objxinxi entry)
    +2  u8   objqyt           (number of objects on the page, incl. the
                               page pseudo-component)
    +3  u8   res0             (constant 0)
    +4  u32  hexpos           (strdata-relative offset of the page's media
                               blob; per-page media ALLOCATOR)
    +8  u32  attdataaddr_rel  (strdata-relative offset of the page's
                               binattinf records; ALLOCATOR / layout)
    +12 u32  medatapos        (media-blob metadata size/pointer; ALLOCATOR)

``objstar`` and ``objqyt`` are pure functions of the HMI page/component
counts: ``objstar`` is the running total of objects on earlier pages, and
``objqyt`` is this page's object count (the page pseudo-component + its
user components).  ``res0`` is always 0.  The three u32 fields are
strdata-relative offsets / sizes produced by the per-page media allocator
and the binattinf layout, so they are carried as inputs.

Verified byte-for-byte against ``source/nextion.hmi.tft``.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass


PAGE_DIR_ENTRY_SIZE = 16


@dataclass
class PageDirInputs:
    """The allocator/layout-dependent fields for one page directory entry."""

    hexpos: int             # media-blob offset (per-page media allocator)
    attdataaddr_rel: int    # binattinf records offset (layout)
    medatapos: int          # media metadata size/pointer (allocator)


def encode_page_dir_entry(objstar: int, objqyt: int, inputs: PageDirInputs) -> bytes:
    """Assemble one 16-byte page directory entry.

    ``objstar`` / ``objqyt`` are HMI-derived; the three offsets come from
    ``inputs``."""
    out = bytearray(PAGE_DIR_ENTRY_SIZE)
    struct.pack_into("<HBB", out, 0, objstar & 0xFFFF, objqyt & 0xFF, 0)
    struct.pack_into("<III", out, 4,
                     inputs.hexpos & 0xFFFFFFFF,
                     inputs.attdataaddr_rel & 0xFFFFFFFF,
                     inputs.medatapos & 0xFFFFFFFF)
    return bytes(out)


def page_object_counts(component_counts: list[int]) -> list[tuple[int, int]]:
    """Return ``[(objstar, objqyt), ...]`` from per-page object counts.

    ``component_counts[i]`` is the total number of objxinxi entries on
    page ``i`` (page pseudo-component + user components)."""
    out = []
    cursor = 0
    for n in component_counts:
        out.append((cursor, n))
        cursor += n
    return out


__all__ = [
    "PAGE_DIR_ENTRY_SIZE",
    "PageDirInputs",
    "encode_page_dir_entry",
    "page_object_counts",
]
