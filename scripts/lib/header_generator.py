"""header_generator — the usercode region header (global dir + name-hash + MainCode).

The first bytes of the usercode region (``strdataaddr``) are:

    [global memory directory]  TLV: u32 total_size + (kind,val) u32 pairs
    [page name-hash table]     sorted (u32 hash_objname, u16 ordinal) — one
                               per page (ordinal = device page id) plus any
                               custom-renamed component names
    [MainCode]                 compiled Program.s + the startup `page N`

Decoded + IL-cross-checked against ``source/nextion.hmi.tft``:

  * **Global memory directory** — a fixed TLV header.  For this project
    (4 pages, no renamed components) it is the constant 0x34-byte block:
    ``u32 0x48`` then ``(3,0x1c) (4,0x20) (0x10,0x30) (0x18,0)`` then
    zero-padding to 0x30 and a trailing ``u32 0``.  The TLV's name-hash
    offset/size fields and ``total_size`` grow with the name-hash entry
    count (16_loop, with renamed components, is +4); this module captures
    the no-renamed-components layout (the project case).
  * **Name-hash table** — ``hash_objname(page_name)`` (14-byte-padded CRC,
    see findings/component-name-hashes.md) → the device page id, sorted by
    hash ascending.  (Custom-renamed component names would add entries; the
    project never renamed any, so it is exactly the 4 page names.)
  * **MainCode** — each ``Program.s`` statement compiled to a
    length-prefixed block, then the implicit startup ``page N`` block (N
    from the source's own ``page`` directive) and a terminator.

Verified byte-exact (the project's whole header, 0x9e bytes).
"""
from __future__ import annotations

import re
import struct

from scripts.lib.page_crc import hash_objname
from scripts.lib.script_compiler import compile_handler, CompileContext


# Global memory directory for a project with no renamed-component name
# hashes (the project case): the constant 0x34-byte TLV block.  total_size
# and the name-hash offset field grow with extra (renamed-component) name
# entries; this is the 4-page / page-names-only layout.
GLOBAL_DIR = bytes.fromhex(
    "48000000"          # total_size
    "030000001c000000"  # (kind 3, 0x1c)
    "0400000020000000"  # (kind 4, 0x20)
    "1000000030000000"  # (kind 0x10, 0x30)  — name-hash region
    "1800000000000000"  # (kind 0x18, 0)
    "0000000000000000"  # padding
    "0000000000000000"  # padding + trailing u32 0 (name-hash base at +0x34)
)


def build_name_hash_table(pages) -> bytes:
    """Sorted ``(u32 hash_objname, u16 device_page_id)`` records, one per
    page.  ``pages`` is the device-id-ordered HMI page model list."""
    recs = sorted(
        ((hash_objname(p.name), p.page_id) for p in pages),
        key=lambda r: r[0],
    )
    out = bytearray()
    for h, ordinal in recs:
        out += struct.pack("<IH", h & 0xFFFFFFFF, ordinal & 0xFFFF)
    return bytes(out)


def build_maincode(program_s: str) -> bytes:
    """Compile ``Program.s`` to the MainCode bytecode chain.

    Each non-comment statement becomes a length-prefixed block; the
    implicit startup ``page N`` (N from the source's ``page`` directive)
    is appended, then a zero terminator."""
    ctx = CompileContext()
    out = bytearray()
    startup_page = None
    for raw in program_s.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^\s*page\s+(\d+)\s*$", line)
        if m:
            startup_page = m.group(1)
        body = compile_handler(line, ctx)
        if body:
            out += struct.pack("<I", len(body)) + body
    # Implicit startup page directive (the editor re-emits the start page).
    if startup_page is not None:
        startup = compile_handler(f"page {startup_page}", ctx)
        out += struct.pack("<I", len(startup)) + startup
    out += b"\x00\x00\x00\x00"
    return bytes(out)


def build_header(pages, program_s: str) -> bytes:
    """The whole usercode header: global dir + name-hash table + MainCode."""
    return GLOBAL_DIR + build_name_hash_table(pages) + build_maincode(program_s)


__all__ = [
    "GLOBAL_DIR",
    "build_name_hash_table",
    "build_maincode",
    "build_header",
]
