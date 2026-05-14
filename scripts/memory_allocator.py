"""memory_allocator.py — Nextion public-memory layout for component attributes.

The Nextion runtime keeps a flat "public memory" region per page.  The region
starts with 4 bytes per global variable (AppAllvasQty globals), followed by
per-component blocks allocated in the order the components appear on the page
(including the page pseudo-component at slot 0).

The allocator here models the T0/non-F-series (model_series == 0) path, which
is what the 16_loop test fixture uses.  Key differences from the F-series path:

* No 4-byte alignment padding between objects.
* Object sizes are empirically derived from 16_loop/16.tft (see findings below).
* Attribute offsets within each block are the T0-specific positions, which
  differ from the attpos values in ``attrs-raw.txt`` (those were compiled from
  the F-series IL; the T0 runtime lays out PARAM fields differently).

Empirical findings for T0 (model_series=0)
-------------------------------------------
Derived by inspecting the init-code bytecode in ``tests/editor outputs/_old/
16_loop/16.tft``.  The init code touches every byte it initialises with a
local-var-ref (``01 LL LL LL LL``), so contiguous runs of references delimit
each component's allocated block.  Verified attribute positions against the
known OFFSETS dict (all checked byte-for-byte against the TFT):

XFloat (type 59)
    block size : 41 bytes
    bco  at    : +1  (Color, 2 bytes)
    val  at    : +5  (SS32,  4 bytes)
    pco  at    : +3  (Color, 2 bytes)  — derived from layout

Variable / Vari (type 52)
    block size : 11 bytes
    val  at    : +0  (SS32,  4 bytes)  — matches attrs-raw.txt second block

The page pseudo-component (type 56 in the page-record sense, or just the
implicit "page" object) allocates 39 bytes before any user component on page 0
of 16_loop.  This is not a hard constant — callers must pass the correct
``starting_offset`` for the page being allocated.

Usage
-----
::

    alloc = MemoryAllocator(app_allvas_qty=4)
    # Optionally set a non-default starting offset (e.g. after an implicit
    # page block has already been reserved).
    alloc.cursor = 4 * 4 + 39   # globals + page pseudo-obj
    alloc.add_object(59, "x0")
    alloc.add_object(59, "x1")
    alloc.add_object(52, "bco")
    offset = alloc.frame_offset("x0", "bco")   # → 1
    offset = alloc.frame_offset("bco", "val")  # → bco.memorypos + 0
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


# ---------------------------------------------------------------------------
# SIZEOF_OBJDATA
# Public-memory block size (bytes) for each component type.
# Empirically derived from 16_loop/16.tft (T0/non-F-series path).
# ---------------------------------------------------------------------------

SIZEOF_OBJDATA: Dict[int, int] = {
    59: 41,   # XFloat  — 11-byte init block, 41-byte total stride (no alignment)
    52: 11,   # Variable/Vari — stride observed between 7 consecutive Vari objects
}


# ---------------------------------------------------------------------------
# ATTPOSUP_TABLE
# Per-type, per-attribute byte offset *within* the allocated block.
# "attpos" here means the distance from the component's memorypos to the
# first byte of the attribute's value in public memory.
#
# Derived empirically for T0 (model_series=0):
#   XFloat x0: memorypos=0x37, x0.bco=0x38 → bco_attpos=1
#                              x0.val=0x3c  → val_attpos=5
#   Vari bco:  memorypos=0x37b, bco.val=0x37b → val_attpos=0
# ---------------------------------------------------------------------------

ATTPOSUP_TABLE: Dict[int, Dict[str, int]] = {
    59: {   # XFloat
        "bco":  1,   # Color (2 bytes) at byte 1
        "picc": 1,   # Picid aliases bco
        "pic":  1,   # Picid aliases bco
        "pco":  3,   # Color (2 bytes) at byte 3
        "val":  5,   # SS32  (4 bytes) at byte 5
    },
    52: {   # Variable / Vari
        "val":  0,   # SS32 (4 bytes), first field
    },
}


# ---------------------------------------------------------------------------
# MemoryAllocator
# ---------------------------------------------------------------------------

@dataclass
class _ComponentRecord:
    comp_type: int
    comp_name: str
    memorypos: int   # absolute offset within public memory


@dataclass
class MemoryAllocator:
    """Allocates public-memory slots for page components and resolves
    component-attribute byte offsets.

    Parameters
    ----------
    app_allvas_qty:
        Number of global ``int`` variables declared in ``Program.s``
        (``AppAllvasQty`` in the TFT header).  Each global occupies 4 bytes
        starting at offset 0.
    cursor:
        Current allocation pointer.  Defaults to ``4 * app_allvas_qty``,
        i.e. right after the global-variable area.  Override if you want to
        skip the implicit page pseudo-component block first.

    Examples
    --------
    Round-trip the 16_loop page-0 layout::

        alloc = MemoryAllocator(app_allvas_qty=4)
        alloc.cursor = 4 * 4 + 39   # 16 bytes globals + 39-byte page block
        alloc.add_object(59, "x0")
        assert alloc.frame_offset("x0", "bco") == 0x38
        assert alloc.frame_offset("x0", "val") == 0x3c
    """

    app_allvas_qty: int = 0
    cursor: int = field(init=False)
    _components: Dict[str, _ComponentRecord] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.cursor = 4 * self.app_allvas_qty

    # ------------------------------------------------------------------

    def add_object(self, comp_type: int, comp_name: str) -> int:
        """Reserve a public-memory block for *comp_name* of *comp_type*.

        Returns the component's ``memorypos`` (absolute offset where its
        block begins).  Raises ``ValueError`` if the type is unknown.
        """
        if comp_type not in SIZEOF_OBJDATA:
            raise ValueError(
                f"unknown component type {comp_type} for {comp_name!r}: "
                f"add it to SIZEOF_OBJDATA to continue"
            )
        mempos = self.cursor
        self._components[comp_name] = _ComponentRecord(
            comp_type=comp_type,
            comp_name=comp_name,
            memorypos=mempos,
        )
        self.cursor += SIZEOF_OBJDATA[comp_type]
        return mempos

    def frame_offset(self, comp_name: str, attr_name: str) -> int:
        """Return the absolute public-memory byte offset of *attr_name* on
        component *comp_name*.

        Raises ``KeyError`` if the component or attribute is unknown.
        """
        rec = self._components[comp_name]
        attpos_table = ATTPOSUP_TABLE.get(rec.comp_type, {})
        if attr_name not in attpos_table:
            raise KeyError(
                f"attribute {attr_name!r} not found for type {rec.comp_type} "
                f"({comp_name!r}); known attrs: {sorted(attpos_table)}"
            )
        return rec.memorypos + attpos_table[attr_name]

    def has_component(self, comp_name: str) -> bool:
        """Return True if *comp_name* has been registered."""
        return comp_name in self._components


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Verify ATTPOSUP offsets against byte-verified positions from
    # 16_loop/16.tft (T0 fixture, model_series=0). The page contains
    # many unknown intermediate components, so we set cursor explicitly
    # for each known component rather than assuming sequential order.

    alloc = MemoryAllocator(app_allvas_qty=4)

    # XFloat (type 59): memorypos = bco_offset - ATTPOSUP_TABLE[59]["bco"]
    _bco_ap = ATTPOSUP_TABLE[59]["bco"]
    for comp_name, bco_offset in [
        ("x0", 0x38), ("x2", 0x8a), ("x4", 0xdc), ("x5", 0xe5),
        ("x1", 0x105), ("x6", 0x161), ("x7", 0x178), ("x8", 0x1a1),
    ]:
        alloc.cursor = bco_offset - _bco_ap
        alloc.add_object(59, comp_name)

    assert alloc.frame_offset("x0", "bco") == 0x38
    assert alloc.frame_offset("x0", "val") == 0x3c
    assert alloc.frame_offset("x2", "bco") == 0x8a
    assert alloc.frame_offset("x2", "val") == 0x8e
    assert alloc.frame_offset("x4", "bco") == 0xdc
    assert alloc.frame_offset("x4", "val") == 0xe0
    assert alloc.frame_offset("x5", "bco") == 0xe5
    assert alloc.frame_offset("x5", "val") == 0xe9
    assert alloc.frame_offset("x1", "bco") == 0x105
    assert alloc.frame_offset("x1", "val") == 0x109
    assert alloc.frame_offset("x6", "bco") == 0x161
    assert alloc.frame_offset("x6", "val") == 0x165
    assert alloc.frame_offset("x7", "bco") == 0x178
    assert alloc.frame_offset("x7", "val") == 0x17c
    assert alloc.frame_offset("x8", "bco") == 0x1a1
    assert alloc.frame_offset("x8", "val") == 0x1a5

    # x7 and x8 are consecutive in memory (no unknown component between them);
    # their stride must equal SIZEOF_OBJDATA[59].
    assert (0x1a1 - _bco_ap) - (0x178 - _bco_ap) == SIZEOF_OBJDATA[59]

    # Vari (type 52): val_attpos=0 so memorypos == val offset
    _val_ap = ATTPOSUP_TABLE[52]["val"]
    for comp_name, val_offset in [
        ("bco", 0x37b), ("blu", 0x386), ("red", 0x391),
        ("wht", 0x39c), ("yel", 0x3a7), ("org", 0x3b2), ("grn", 0x3bd),
    ]:
        alloc.cursor = val_offset - _val_ap
        alloc.add_object(52, comp_name)

    assert alloc.frame_offset("bco", "val") == 0x37b
    assert alloc.frame_offset("blu", "val") == 0x386
    assert alloc.frame_offset("red", "val") == 0x391
    assert alloc.frame_offset("wht", "val") == 0x39c
    assert alloc.frame_offset("yel", "val") == 0x3a7
    assert alloc.frame_offset("org", "val") == 0x3b2
    assert alloc.frame_offset("grn", "val") == 0x3bd

    # Consecutive Vari stride must equal SIZEOF_OBJDATA[52].
    assert 0x386 - 0x37b == SIZEOF_OBJDATA[52]

    print("memory_allocator self-test OK")
