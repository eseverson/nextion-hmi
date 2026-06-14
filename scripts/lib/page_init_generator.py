"""page_init_generator — the page pseudo-component's load-event chain.

The third and final section of each page's media blob (after the name-hash
table and the frame image) is the **page-init chain** — the page
pseudo-component's compiled load handler, in the same length-prefixed
bytecode format as the per-component event region:

    [8-byte preamble: two 0x00000000 terminators]
    [per-scrolling-component `timerset` blocks]   (e.g. page-0 s0 marquee)
    [`draw` + the compiled page `codesload` chain]  (e.g. page-2 h0.val=dim)
    [len-3 `doevents` block: 09 22 08]               (constant)
    [0x00000000 terminator]                          (constant)

Opcode identities (model-100 instruction set):
    09 1d 04  = ``draw``
    09 23 08  = ``timerset``
    09 22 08  = ``doevents``

The constant frame (no scrolling text, no page events) — pages 1 & 3 in the
project — is just ``[preamble][doevents][terminator]``.  Pages with a
``codesload`` event (page 2) emit ``draw`` + the compiled handler before
``doevents``; pages with scrolling text (page 0) emit ``timerset`` motion
blocks + the paired ``L`` re-arm marker (``patch_l_markers`` fills its
address).
"""
from __future__ import annotations

import struct


TERM = b"\x00\x00\x00\x00"

# Constant trailing block: the `doevents` opcode (page-load completion).
DOEVENTS_BLOCK = bytes.fromhex("092208")

# Two leading terminators (the page pseudo-component's empty init/ref slots).
PREAMBLE = TERM + TERM


def _entry(payload: bytes) -> bytes:
    return struct.pack("<I", len(payload)) + payload


def build_constant_page_init() -> bytes:
    """The page-init chain for a page with no scrolling text and no page
    events: ``[preamble][doevents][terminator]`` (pages 1 & 3)."""
    return PREAMBLE + _entry(DOEVENTS_BLOCK) + TERM


# ``draw`` opcode (09 1d 04) + the single ASCII arg the editor emits as the
# page-load redraw preamble before a compiled ``codesload`` handler.  In the
# project it is ``draw 2`` (`09 1d 04 32`).  Captured as the codesload
# preamble; its arg is a fixed page-load draw mode.
DRAW_OPCODE = bytes.fromhex("091d04")
CODESLOAD_DRAW_ARG = b"2"


def build_page_init_with_codesload(codesload_body: bytes) -> bytes:
    """The page-init chain for a page with a ``codesload`` event (page 2):
    ``[preamble][draw 2][compiled codesload][doevents][terminator]``.

    ``codesload_body`` is the already-compiled handler bytecode (the
    flattened cglist entries, WITHOUT a trailing terminator) — i.e. the
    output of ``script_compiler.compile_event_handler`` with the page's
    component-attribute refs resolved to *global* binattinf record indices.
    """
    out = bytearray(PREAMBLE)
    out += _entry(DRAW_OPCODE + CODESLOAD_DRAW_ARG)
    out += codesload_body
    out += _entry(DOEVENTS_BLOCK)
    out += TERM
    return bytes(out)


# ``timerset`` (09 23 08) and ``L`` (4c 20 03) opcodes for a page's Timer
# arm.  The page-load handler arms each Timer with
# ``timerset LOAD(en), <id>, LOAD(tim), 0`` followed by an ``L`` re-arm
# marker whose address is back-patched by ``patch_l_markers`` at assembly.
TIMERSET_OPCODE = bytes.fromhex("092308")
L_OPCODE = bytes.fromhex("4c2003")


def _load_op(record_index: int) -> bytes:
    return b"\x01" + struct.pack("<I", record_index & 0xFFFFFFFF)


def build_timerset_block(tim_record: int, en_record: int, timer_id: int) -> bytes:
    """One Timer's page-load arm: ``timerset LOAD(en), <id>, LOAD(tim), 0``.

    ``tim_record`` / ``en_record`` are the GLOBAL binattinf record indices
    of the timer's ``tim`` / ``en`` attributes."""
    return (TIMERSET_OPCODE
            + _load_op(en_record) + b"," + str(timer_id).encode("ascii")
            + b"," + _load_op(tim_record) + b",0")


def build_page_init_with_timer(tim_record: int, en_record: int,
                               timer_id: int) -> bytes:
    """The page-init chain for a page whose load handler arms a Timer
    (page 0): ``[preamble][timerset][L re-arm (placeholder)][doevents]
    [terminator]``.  The ``L`` marker's address is filled by
    ``usercode_assembler.patch_l_markers`` once the layout is fixed."""
    out = bytearray(PREAMBLE)
    out += _entry(build_timerset_block(tim_record, en_record, timer_id))
    out += _entry(L_OPCODE + struct.pack("<I", 0))   # placeholder addr
    out += _entry(DOEVENTS_BLOCK)
    out += TERM
    return bytes(out)


__all__ = [
    "TERM",
    "DOEVENTS_BLOCK",
    "PREAMBLE",
    "DRAW_OPCODE",
    "CODESLOAD_DRAW_ARG",
    "TIMERSET_OPCODE",
    "L_OPCODE",
    "build_constant_page_init",
    "build_page_init_with_codesload",
    "build_timerset_block",
    "build_page_init_with_timer",
]
