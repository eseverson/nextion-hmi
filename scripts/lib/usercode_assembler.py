"""usercode_assembler — STEP 4/5 region assembler for the TFT usercode.

The usercode region (file ``strdataaddr`` .. ``pageadd``) is the part of
the compiled TFT that holds the project's *bytecode* and its supporting
tables.  STEPs 1-3 (locked by tests) produce every individual block
byte-for-byte: the per-component init bytecode
(:mod:`scripts.lib.tft_init_encoder`) and the per-event script bytecode
(:mod:`scripts.lib.script_compiler`).  This module is the missing piece
that *stitches* those blocks into the whole region in the editor's exact
byte order, with the right scaffolding (terminators, event-name strings,
``S``/``L`` suspend/resume markers, the global-memory directory and the
MainCode header), and back-patches every cross-reference.

## Region map (reverse-engineered from ``source/nextion.hmi.tft``)

The usercode region (all offsets relative to ``strdataaddr``) is:

    +0x00  global-memory directory (TLV header) + name-hash table
    +MainCodeHex-4  MainCode CRC  (u32)
    +MainCodeHex    MainCode / Program.s bytecode chain
    ----- per page, in page order: -----
        event-block region   (per-component event/init chains + scaffolding)
        media blob           (page private-memory image; copied verbatim)
    -----
    +staticstrBeg   AppAllvas table (global-int name/offset/type records)
    +attdataaddr    binattinf attribute records (per page) ... to pageadd

The **event-block region** for a page is, for each component in objxinxi
order, that component's *event layout* expanded into a length-prefixed
entry stream.  Each component type declares a fixed ordered event list;
each event contributes either its compiled chain (terminated by a zero
length word) or a bare terminator, plus an event-NAME string entry
(``<u32 len><ascii>`` + terminator) at a type-specific position.  The
exact per-type layout was decoded directly off the project TFT and is
captured in :data:`EVENT_LAYOUT`.

## S / L suspend-resume markers

A handler that the editor marks as suspendable (the project's ``tm0``
Timer Event) opens with a 7-byte ``S`` marker entry
(``53 20 03 <u32 biaoji>``) and, at its re-arm point, an ``L`` marker
(``4c 20 03 <u32 addr>``).  Verified byte-exactly against the project:

  * ``L.addr == S_entry_start + 4 + 7`` — i.e. the usercode offset of the
    byte immediately following the S marker's payload (the handler body
    entry).  :func:`patch_l_markers` performs this back-patch once the
    final layout is known.
  * ``S.biaoji`` is the editor's monotonic global label key.  It is *not*
    derivable from project content (it depends on the editor's allocator
    call order across the whole compile).  The assembler treats it as a
    captured per-handler constant; for the project that single value is
    ``0x0F71FD`` (see :data:`PROJECT_S_BIAOJI`).

This module's :func:`assemble_event_region` re-emits one page's
event-block region from a list of :class:`CompiledComponent` descriptors
and is verified byte-for-byte by
``tests/sim/test_usercode_assembler.py``.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional


TERM = b"\x00\x00\x00\x00"

# The single suspend biaoji the editor assigned to the project's tm0
# Timer Event.  Not content-derivable (it is the editor's global label
# allocator state); captured here so the assembler can reproduce the
# project byte-for-byte.  A from-scratch writer that does not need
# suspend/resume parity may emit 0 instead.
PROJECT_S_BIAOJI = 0x0F71FD


def _entry(payload: bytes) -> bytes:
    """Wrap a bytecode payload as one length-prefixed cglist entry."""
    return struct.pack("<I", len(payload)) + payload


def emit_s_marker(biaoji: int) -> bytes:
    """The 7-byte ``S`` suspend marker payload (no length prefix)."""
    return b"\x53\x20\x03" + struct.pack("<I", biaoji & 0xFFFFFFFF)


def emit_l_marker(addr: int) -> bytes:
    """The 7-byte ``L`` resume marker payload (no length prefix)."""
    return b"\x4c\x20\x03" + struct.pack("<I", addr & 0xFFFFFFFF)


# ---------------------------------------------------------------------------
# Per-type event layout
# ---------------------------------------------------------------------------
#
# Each component type emits, in order, a fixed sequence of "layout ops".
# A layout op is one of:
#
#   ("chain", slot)   -> emit the component's compiled chain for event
#                        `slot` (already terminated), or, if that slot is
#                        empty, nothing for the chain but the trailing
#                        terminator below still applies.
#   ("term",)         -> emit a bare 0x00000000 terminator.
#   ("name", str)     -> emit a length-prefixed ascii name entry.
#
# The sequences below were decoded by walking source/nextion.hmi.tft and
# matching each component's emitted entry stream (see
# /tmp scaffolding analysis recorded in findings/next-steps.md STEP 4).
#
# Notation per decoded stream (C<n>=chain slot n, T=terminator,
# N:<name>=name string):
#
#   Page(121)    : C0 T  T  N:down T N:up T N:unload T
#   XFloat(59)   : C0 T C1 T  N:down T N:up T
#   Text(116)    : C0 T C1 T  N:down T N:up T          (same as XFloat)
#   Prog(106)    : C0 T C1 T  N:down T N:up T          (same as XFloat)
#   Hotspot(109) : C1 T N:down C2 T N:up  T T N:timer
#   Slider(1)    : C1 T N:down C2 T N:up C3 T N:slide C4 T
#   Button(98)   : C0 T C1 T N:down C2 T N:up C3 T
#   Timer(51)    : C4 T  T T T T T T T

# Build with explicit op tuples.
def _ops(*spec):
    return tuple(spec)


C = lambda n: ("chain", n)
T = ("term",)
def N(s):
    return ("name", s)


EVENT_LAYOUT: dict[int, tuple] = {
    # Decoded byte-for-byte from source/nextion.hmi.tft. Each component
    # emits, in order, the chain bodies of its declared events (each
    # followed by a terminator) interleaved with the event-NAME strings.
    # Empty/absent slots still contribute their terminator (and any
    # leading terminator for an absent ``init`` slot).
    #
    #   Page(121)    : C0 T  T  down T up T unload T
    #   XFloat(59)   : C0 T C1 T  down T up T
    #   Text(116)    : C0 T C1 T  down T up T          (== XFloat)
    #   Prog(106)    : C0 T C1 T  down T up T          (== XFloat)
    #   Hotspot(109) : T C1 T down C2 T up T           (leading T = absent init)
    #   Slider(1)    : T C1 T down C2 T up C3 T slide C4 T
    #   Button(98)   : C0 T C1 T down C2 T up C3 T
    #   Timer(51)    : T timer C4 T T T T T T T T
    121: _ops(C(0), T, T, N("down"), T, N("up"), T, N("unload"), T),
    59:  _ops(C(0), T, C(1), T, N("down"), T, N("up"), T),
    116: _ops(C(0), T, C(1), T, N("down"), T, N("up"), T),
    106: _ops(C(0), T, C(1), T, N("down"), T, N("up"), T),
    109: _ops(T, C(1), T, N("down"), C(2), T, N("up"), T),
    1:   _ops(T, C(1), T, N("down"), C(2), T, N("up"), C(3), T, N("slide"), C(4), T),
    98:  _ops(C(0), T, C(1), T, N("down"), C(2), T, N("up"), C(3), T),
    51:  _ops(T, N("timer"), C(4), T, T, T, T, T, T, T, T),
    # Variable (52): no event registration, contributes nothing.
    52:  _ops(),
}


@dataclass
class CompiledComponent:
    """One component's already-compiled event chains, keyed by slot.

    ``chains`` maps an event-slot index (0..5) to the compiled chain
    *body* (the length-prefixed cglist entries, WITHOUT the trailing
    zero terminator — the layout adds it).  Slots absent from the dict
    are treated as empty.  ``lei`` selects the per-type event layout.
    """

    lei: int
    chains: dict[int, bytes] = field(default_factory=dict)

    def emit(self) -> bytes:
        layout = EVENT_LAYOUT.get(self.lei)
        if layout is None:
            raise KeyError(
                f"no EVENT_LAYOUT for lei={self.lei}; decode its stream "
                f"from a reference TFT and add it"
            )
        out = bytearray()
        for op in layout:
            kind = op[0]
            if kind == "chain":
                # The chain body (length-prefixed cglist entries, no
                # trailing terminator). The terminator is an explicit
                # ``("term",)`` op that always follows in the layout.
                body = self.chains.get(op[1])
                if body:
                    out += body
            elif kind == "term":
                out += TERM
            elif kind == "name":
                out += _entry(op[1].encode("ascii"))
            else:  # pragma: no cover
                raise ValueError(f"bad layout op {op!r}")
        return bytes(out)


def assemble_event_region(components: list[CompiledComponent]) -> bytes:
    """Concatenate every component's emitted event stream, in order."""
    out = bytearray()
    for comp in components:
        out += comp.emit()
    return bytes(out)


# ---------------------------------------------------------------------------
# Whole usercode-region assembler
# ---------------------------------------------------------------------------

@dataclass
class PageBlocks:
    """One page's contribution to the usercode region.

    ``components`` is the page's components in objxinxi order (each with
    its compiled per-slot chains).  ``media`` is the page's private-memory
    image (the ``hexpos`` blob) — copied verbatim; authoring it from
    scratch is a separate item (per-page private allocator), but it is a
    well-defined contiguous span the assembler simply places after the
    page's event region.
    """

    components: list[CompiledComponent]
    media: bytes


def assemble_usercode_region(
    header: bytes,
    pages: list[PageBlocks],
    tail: bytes,
    *,
    patch_labels: bool = True,
) -> bytes:
    """Stitch the full usercode region (``strdataaddr`` .. ``pageadd``).

    Layout: ``header`` (global-memory directory + name-hash table +
    MainCode chain) then, for each page in order, its assembled event
    region immediately followed by its ``media`` blob, then ``tail``
    (AppAllvas table + per-page binattinf attribute records).

    When ``patch_labels`` is set, the assembled region is run through
    :func:`patch_l_markers` so every ``L`` resume marker points just
    past its paired ``S`` marker (``region_base=0`` because the markers
    use usercode-relative offsets)."""
    out = bytearray(header)
    for pb in pages:
        out += assemble_event_region(pb.components)
        out += pb.media
    out += tail
    if patch_labels:
        out = bytearray(patch_l_markers(bytes(out), region_base=0))
    return bytes(out)


# ---------------------------------------------------------------------------
# S / L marker back-patching
# ---------------------------------------------------------------------------

def patch_l_markers(region: bytes, region_base: int = 0) -> bytes:
    """Patch every ``L`` marker's addr to point just past its paired
    ``S`` marker.

    For the project there is exactly one S/L pair, and the editor sets
    ``L.addr = (S length-prefix offset) + 4 + 7``.  We resolve it by
    finding the S marker's offset within ``region`` and writing the
    computed address into the L marker's addr field.  ``region_base`` is
    the usercode offset of ``region[0]`` (so absolute offsets match the
    TFT coordinate system the markers use)."""
    buf = bytearray(region)
    # Locate the S marker entry (length 7 + 53 20 03).
    s_addr: Optional[int] = None
    i = 0
    while i + 11 <= len(buf):
        if (struct.unpack_from("<I", buf, i)[0] == 7
                and buf[i + 4] == 0x53 and buf[i + 5] == 0x20
                and buf[i + 6] == 0x03):
            # S length-prefix at region offset i -> body end = i+4+7.
            s_addr = region_base + i + 4 + 7
            break
        i += 1
    if s_addr is None:
        return bytes(buf)
    # Patch every L marker.
    i = 0
    while i + 11 <= len(buf):
        if (struct.unpack_from("<I", buf, i)[0] == 7
                and buf[i + 4] == 0x4C and buf[i + 5] == 0x20
                and buf[i + 6] == 0x03):
            struct.pack_into("<I", buf, i + 7, s_addr & 0xFFFFFFFF)
        i += 1
    return bytes(buf)


__all__ = [
    "TERM",
    "PROJECT_S_BIAOJI",
    "emit_s_marker",
    "emit_l_marker",
    "EVENT_LAYOUT",
    "CompiledComponent",
    "PageBlocks",
    "assemble_event_region",
    "assemble_usercode_region",
    "patch_l_markers",
]
