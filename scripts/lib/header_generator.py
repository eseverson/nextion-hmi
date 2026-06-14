"""header_generator — the usercode region header (global dir + name-hash + MainCode).

The first bytes of the usercode region (``strdataaddr``) are:

    [global memory directory]  TLV: u32 total_size + (kind,val) u32 pairs
    [page name-hash table]     sorted (u32 hash_objname, u16 ordinal) — one
                               per page (ordinal = device page id) plus any
                               custom-renamed component names
    [MainCode]                 compiled Program.s + the startup `page N`

Decoded + IL-cross-checked against the project fixtures:

  * **Global memory directory** — a TLV header followed by three regions:
    ``u32 total_size`` then ``u32 region_count(=3)`` then three
    ``(offset, size)`` u32 pairs, zero-padded to ``0x20``:
      - region[0] @0x1c, 4 bytes = ``u32`` (= region[2] size, always 24);
      - region[1] @0x20 = the AppAllvas-values area
        (``16 + 4*max(0, AppAllvasQty-3)`` zero bytes) followed by one
        24-byte **global-scope record per vscope page-instance**;
      - region[2] @(0x20 + region1_size) = the page name-hash table.
    For a project with no globals beyond sys0/1/2 and no vscope components
    (the source fixture) this is the constant 0x34-byte block + name-hash;
    the live project has 2 vscope instances (an ``x1`` shown on 2 pages),
    so region[1] grows by 48 bytes and ``total_size`` / the name-hash
    offset shift accordingly.
  * **Global-scope record** (vscope) — 24 bytes:
    ``u32 1 | u8 0 | u8 ordinal(1-based) | u16 bco | u16 pco | u8 xcen |
    u8 ycen | u32 0 | u32 0 | u32 1``.  One per page-instance of each
    ``vscope=1`` component, in page order; holds the component's render
    colours/centring so the firmware re-draws the global on each page.
  * **Name-hash table** — ``hash_objname(page_name)`` (14-byte-padded CRC,
    see findings/component-name-hashes.md) → the device page id, sorted by
    hash ascending, preceded by a leading ``u32 0``.  (Custom-renamed
    component names would add entries; neither project renames any.)
  * **MainCode** — each ``Program.s`` statement compiled to a
    length-prefixed block, then the implicit startup ``page N`` block (N
    from the source's own ``page`` directive) and a terminator.

Verified byte-exact against both the source and live project headers.
"""
from __future__ import annotations

import re
import struct

from scripts.lib.page_crc import hash_objname
from scripts.lib.script_compiler import compile_handler, CompileContext


# Region[1] (AppAllvas values) base size: 16 bytes covers the implicit
# sys0/sys1/sys2 globals (+ padding); grows 4 bytes per additional global.
_REGION1_BASE = 16
# Region[2] (name-hash table) header size field — always 24 in the TLV
# (the editor records the 4-page name-hash block size here).
_REGION2_SIZE_FIELD = 24
# The TLV directory body (count + 3 (offset,size) pairs) is padded to 0x20.
_TLV_BODY_END = 0x20


def vscope_record(ordinal, bco, pco, xcen, ycen) -> bytes:
    """One 24-byte global-scope (vscope) record for a component
    page-instance (see module docstring)."""
    return struct.pack(
        "<IBBHHBBIII",
        1, 0, ordinal & 0xFF,
        bco & 0xFFFF, pco & 0xFFFF, xcen & 0xFF, ycen & 0xFF,
        0, 0, 1,
    )


def _vscope_instances(pages):
    """Every ``vscope=1`` component page-instance, in page order."""
    out = []
    for hp in pages:
        for c in hp.components:
            if c.attrs.get("vscope") == 1:
                out.append(c)
    return out


def build_global_dir(pages, app_allvas_qty) -> bytes:
    """The global memory directory: TLV header + region[0] (a u32) +
    region[1] (AppAllvas-values zeros + one vscope record per global-scope
    page-instance).  The name-hash table follows (region[2])."""
    region1_size = _REGION1_BASE + 4 * max(0, app_allvas_qty - 3)
    instances = _vscope_instances(pages)
    region1_size += len(instances) * 24

    region2_off = _TLV_BODY_END + region1_size
    total_size = region2_off + _REGION2_SIZE_FIELD

    out = bytearray()
    # TLV header (8 u32s = 0x20 bytes): total_size, region_count=3, then
    # three (offset, size) pairs.  The last u32 (at +0x1c) is region[0]'s
    # 4-byte body = the region[2] (name-hash) size.
    out += struct.pack(
        "<IIIIIIII",
        total_size, 3,
        0x1c, 4,                       # region[0] (offset, size)
        0x20, region1_size,            # region[1] (offset, size)
        region2_off, _REGION2_SIZE_FIELD,  # region[2] (offset, size)
    )
    assert len(out) == _TLV_BODY_END
    # region[1] @0x20: AppAllvas-values zeros + one vscope record per
    # global-scope page-instance.
    #
    # Ordinal rule (me/miata-dash#37): the record ordinal is the 1-based
    # page-instance index of a *multi-page* global (one whose objname appears
    # on >1 page — e.g. the project's shared ``x1`` on main+gauge gets 1, 2).
    # A global that lives on a single page gets ordinal 0.  (Validated across
    # the live fixture + the 01_multi-global oracles: x1 → 1,2; the gauge-only
    # g0/g1/g2/t0 → 0.)
    from collections import Counter
    name_pages = Counter(c.name for c in instances)
    per_name_seq: dict = {}
    region1 = bytearray(b"\x00" * (_REGION1_BASE + 4 * max(0, app_allvas_qty - 3)))
    for c in instances:
        a = c.attrs
        if name_pages[c.name] > 1:
            per_name_seq[c.name] = per_name_seq.get(c.name, 0) + 1
            ordinal = per_name_seq[c.name]
        else:
            ordinal = 0
        region1 += vscope_record(
            ordinal,
            int(a.get("bco") or 0), int(a.get("pco") or 0),
            int(a.get("xcen") or 0), int(a.get("ycen") or 0),
        )
    out += region1
    return bytes(out)


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


def build_maincode(program_s: str, sysvar_overrides=None,
                   startup_page_id=None) -> bytes:
    """Compile ``Program.s`` to the MainCode bytecode chain.

    Each non-comment statement becomes a length-prefixed block (including
    any explicit ``page N`` directive in the source), then the editor's
    implicit startup ``page`` block is appended, then a zero terminator.

    The implicit startup block is **always the device home page** (page id
    0 / the first page), *not* a copy of the source's ``page`` directive:
    the editor re-emits the project's configured power-on start page, which
    is the home page regardless of any in-script navigation.  (In the source
    fixture the script's trailing ``page 0`` coincides with the home page,
    which masked this; the live project navigates ``page 1`` in-script but
    the editor still re-emits ``page 0``.)  ``startup_page_id`` overrides the
    default home page id 0 if a project ever configures a different one.

    ``sysvar_overrides`` carries any editor-version sysvar-index overrides
    (e.g. ``baud`` on newer builds)."""
    ctx = CompileContext(sysvar_overrides=dict(sysvar_overrides or {}))
    out = bytearray()
    saw_page = False
    for raw in program_s.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        if re.match(r"^\s*page\s+(\d+)\s*$", line):
            saw_page = True
        body = compile_handler(line, ctx)
        if body:
            out += struct.pack("<I", len(body)) + body
    # Implicit startup page block (the editor re-emits the power-on start
    # page = the home page, id 0 by default).  Emitted whenever the script
    # has any `page` directive (every real project's Program.s does).
    if saw_page:
        home = 0 if startup_page_id is None else startup_page_id
        startup = compile_handler(f"page {home}", ctx)
        out += struct.pack("<I", len(startup)) + startup
    out += b"\x00\x00\x00\x00"
    return bytes(out)


def build_header(pages, program_s, app_allvas_qty=3,
                 sysvar_overrides=None) -> bytes:
    """The whole usercode header: global memory directory (TLV + region[1]
    incl. any vscope records) + a leading ``u32 0`` + the page name-hash
    table + MainCode.  The implicit startup page block is the home page
    (``pages[0].page_id``)."""
    startup_page_id = pages[0].page_id if pages else 0
    return (build_global_dir(pages, app_allvas_qty)
            + b"\x00\x00\x00\x00"           # region[2] leading marker
            + build_name_hash_table(pages)
            + build_maincode(program_s, sysvar_overrides,
                             startup_page_id=startup_page_id))


__all__ = [
    "vscope_record",
    "build_global_dir",
    "build_name_hash_table",
    "build_maincode",
    "build_header",
]
