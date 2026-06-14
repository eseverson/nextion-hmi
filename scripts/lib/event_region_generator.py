"""event_region_generator — per-page usercode event region from the HMI.

The usercode region holds, per page (before the page's media blob), an
**event region**: each component's compiled event/init bytecode chains in
``EVENT_LAYOUT`` order.  This module GENERATES those chains from the HMI
model + the per-page binattinf record table, so the whole event region is
emitted rather than copied:

  * **slot 0 (init)** — the component's persistent draw bytecode, from
    :func:`tft_init_encoder.encode_init_block` with an ``attr_addr``
    resolver that returns the GLOBAL binattinf record index for each
    attribute (the value the editor's ``LOAD`` operand carries).
  * **slot 1 (auto-fill)** — for display components, the editor's
    background-restore ``fill x,y,w,h, LOAD(page_bco)``.  The Hotspot's
    slot 1 is the press-highlight fill at its own geometry.
  * **slots 1-5 (events)** — compiled ``codes*`` handlers (the Hotspot
    navigation is ``page N``; richer handlers go through
    :func:`script_compiler.compile_event_handler`).

The chains feed :class:`usercode_assembler.CompiledComponent` /
:func:`usercode_assembler.assemble_event_region`, which adds the per-type
``EVENT_LAYOUT`` scaffolding (terminators + event-NAME strings).

## attr_addr resolver — global record index + colour/picid aliasing

``LOAD`` operands are GLOBAL binattinf record indices: the page's
cumulative record count plus the attribute's page-local index (the value
its attstr slot holds).  The init templates request some attributes by an
alias the attstr table stores under a different name — ``picc``/``pic`` →
``bco``, ``picc2``/``pic2`` → ``bco2`` (the F-series collapses pic/picc/bco
onto one record).  :data:`ATTR_ALIASES` resolves those.
"""
from __future__ import annotations

import struct

from scripts.lib.tft_attrs_encoder import APP_ATT_NAMES
from scripts.lib.tft_init_encoder import encode_init_block, Component
from scripts.lib.usercode_assembler import CompiledComponent, PROJECT_S_BIAOJI
from scripts.lib.script_compiler import compile_event_handler, CompileContext

# The project TFT (nxt-1.65.1) emits setbrush as `09 1c 08`.
SETBRUSH_OVERRIDE = {"setbrush": bytes.fromhex("091c08")}

# Init-template attribute name → the attstr-table name that actually holds
# its record (F-series pic/picc/bco aliasing).
ATTR_ALIASES = {
    "picc": "bco", "pic": "bco",
    "picc2": "bco2", "pic2": "bco2",
    "bpic": "bco", "ppic": "pco",
}

# fill opcode (background restore) — `09 0d 04`.
FILL_OPCODE = bytes.fromhex("090d04")
# page opcode (hotspot nav) — `09 0b 04`.
PAGE_OPCODE = bytes.fromhex("090b04")
# ref opcode (refresh) — `09 03 04`.  When two visual components' bounding
# boxes overlap, each one's auto-fill (slot 1) gets a `ref <other_id>`
# appended so the editor redraws the overlapped neighbour after a
# background restore (z-order overlap resolution).
REF_OPCODE = bytes.fromhex("090304")

# Component classes that participate in overlap detection (have a box).
_VISUAL_LEIS = frozenset({59, 116, 106, 109, 1, 98})


def _box(a):
    x = int(a.get("x") or 0)
    y = int(a.get("y") or 0)
    w = int(a.get("w") or 0)
    h = int(a.get("h") or 0)
    return (x, y, x + w, y + h)


def _overlaps(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def page_overlap_refs(components):
    """Return ``{comp_name: [other_id, ...]}`` — for each visual component,
    the ids of the other visual components whose bounding box overlaps it
    (in component order).  Each such id is appended as a ``ref`` to the
    component's auto-fill."""
    visual = [c for c in components if c.lei in _VISUAL_LEIS]
    boxes = {c.name: _box(c.attrs) for c in visual}
    out = {}
    for c in visual:
        refs = []
        for other in visual:
            if other.name == c.name:
                continue
            if _overlaps(boxes[c.name], boxes[other.name]):
                refs.append(int(other.attrs.get("id") or 0))
        if refs:
            out[c.name] = refs
    return out

_SLOT_NAME = APP_ATT_NAMES  # index → attribute name


def make_attr_resolver(attstr_slots, page_record_base, page_bco_global):
    """Build an ``attr_addr(name) -> global_record_index`` resolver.

    ``attstr_slots`` is the component's 88-entry attstr slot table (the
    page-local record index per AppAttNames slot, 0xffff if absent);
    ``page_record_base`` is the cumulative record count of all prior pages;
    ``page_bco_global`` is the page component's bco record (already global).
    """

    def attr_addr(name: str) -> int:
        if name == "page_bco":
            return page_bco_global
        local = attstr_slots[APP_ATT_NAMES.index(name)]
        if local == 0xFFFF and name in ATTR_ALIASES:
            local = attstr_slots[APP_ATT_NAMES.index(ATTR_ALIASES[name])]
        return page_record_base + local

    return attr_addr


def _entry(payload: bytes) -> bytes:
    return struct.pack("<I", len(payload)) + payload


def _fill_chain(x, y, w, h, bco_record) -> bytes:
    """``fill x,y,w,h, LOAD(bco_record)`` — one length-prefixed block."""
    body = (FILL_OPCODE + f"{x},{y},{w},{h},".encode("ascii")
            + b"\x01" + struct.pack("<I", bco_record))
    return _entry(body)


def _page_chain(target_page_id: int) -> bytes:
    """``page N`` — the hotspot navigation block."""
    body = PAGE_OPCODE + str(target_page_id).encode("ascii")
    return _entry(body)


# HMI event-script slot mapping per component type (codes* key → slot).
_EVENT_SLOTS = {
    1:   {"codesdown": 2, "codesup": 3, "codesslide": 4},   # Slider
    51:  {"codestimer": 4},                                  # Timer
}


def build_page_attr_offsets(components, per_page_objs, page_record_base):
    """Build a ``{"<comp>.<attr>": global_record_index}`` map for every
    component-attribute ref an event script on the page might use.

    ``components`` is the HMI page's component list; ``per_page_objs`` the
    matching objxinxi entries (for the attstr slot tables); the indices
    align.  Event scripts reference attributes by their GLOBAL binattinf
    record index (the value the ``LOAD`` operand carries).
    """
    offs = {}
    for hc, o in zip(components, per_page_objs):
        for ai, attr in enumerate(APP_ATT_NAMES):
            rec = o.slots[ai]
            if rec != 0xFFFF:
                offs[f"{hc.name}.{attr}"] = page_record_base + rec
    return offs


def _compile_event(source, attr_offsets, *, with_s_marker=False):
    """Compile an HMI event script to its chain body, resolving
    ``<comp>.<attr>`` refs to global record indices.  ``with_s_marker``
    prepends the project's captured ``S`` suspend marker (Timer)."""
    ctx = CompileContext()
    ctx.component_offsets = dict(attr_offsets)
    ctx.globals = {"sys0": 0, "sys1": 4, "sys2": 8}
    body = compile_event_handler(source, ctx)
    if with_s_marker:
        s_entry = _entry(b"\x53\x20\x03" + struct.pack("<I", PROJECT_S_BIAOJI))
        return s_entry + body
    return body


def _fill_with_refs(x, y, w, h, bco_record, overlap_ids):
    """Auto-fill block + a ``ref <id>`` entry for each overlapping
    neighbour."""
    out = _fill_chain(x, y, w, h, bco_record)
    for oid in overlap_ids:
        out += _entry(REF_OPCODE + str(oid).encode("ascii"))
    return out


def build_component_chains(hc, attstr_slots, page_record_base,
                           page_bco_global, page_id,
                           attr_offsets=None, overlap_ids=None) -> CompiledComponent:
    """Build one component's per-slot chains for the event region.

    ``hc`` is the HMI component model; ``attstr_slots`` its attstr slot
    table; ``page_record_base`` the cumulative prior-page record count;
    ``page_bco_global`` the page's bco record (global); ``page_id`` the
    device page id (for the page fill geometry / hotspot nav).
    ``attr_offsets`` is the page-wide ``{"<comp>.<attr>": global_record}``
    map used to compile event-script slots (Timer/Slider).  ``overlap_ids``
    are the ids of visual neighbours whose box overlaps this component (a
    ``ref <id>`` is appended to the auto-fill for each).
    """
    lei = hc.lei
    a = hc.attrs
    x = int(a.get("x") or 0)
    y = int(a.get("y") or 0)
    w = int(a.get("w") or 0)
    h = int(a.get("h") or 0)
    resolver = make_attr_resolver(attstr_slots, page_record_base, page_bco_global)
    attr_offsets = attr_offsets or {}
    overlap_ids = overlap_ids or []
    chains: dict[int, bytes] = {}

    if lei == 121:
        # Page pseudo-component: slot 0 = full-page fill (the page itself
        # has no media; its fill covers the whole screen using page_bco).
        pw = int(a.get("w") or 480)
        ph = int(a.get("h") or 320)
        chains[0] = _fill_chain(0, 0, pw, ph, page_bco_global)
        return CompiledComponent(lei=lei, chains=chains)

    if lei == 109:
        # Hotspot: slot 1 = press-highlight fill (own geometry, page_bco)
        # + overlap refs; slot 2 = navigation compiled from codesdown.
        chains[1] = _fill_with_refs(x, y, w, h, page_bco_global, overlap_ids)
        nav = hc.events.get("codesdown", "").strip()
        if nav.startswith("page "):
            chains[2] = _page_chain(int(nav.split()[1]))
        return CompiledComponent(lei=lei, chains=chains)

    if lei == 52:
        # Variable: no chains.
        return CompiledComponent(lei=lei, chains={})

    if lei == 51:
        # Timer: slot 4 = the codestimer handler (with the S marker).
        src = hc.events.get("codestimer", "").strip()
        if src:
            chains[4] = _compile_event(src, attr_offsets, with_s_marker=True)
        return CompiledComponent(lei=lei, chains=chains)

    if lei == 1:
        # Slider: slot 1 = auto-fill; slots 2/3/4 = codesdown/up/slide.
        chains[1] = _fill_chain(x, y, w, h, page_bco_global)
        for key, slot in _EVENT_SLOTS[1].items():
            src = hc.events.get(key, "").strip()
            if src:
                chains[slot] = _compile_event(src, attr_offsets)
        return CompiledComponent(lei=lei, chains=chains)

    # Display / interactive components: slot 0 = init draw, slot 1 =
    # auto-fill (background restore at own geometry using page_bco).
    comp = Component(
        comp_type=lei, comp_id=int(a.get("id") or 0), page_id=page_id,
        x=x, y=y, w=w, h=h, attrs=a,
    )
    init = encode_init_block(comp, resolver, opcode_override=SETBRUSH_OVERRIDE)
    if init:
        chains[0] = init
    # The auto-fill exists for the display components that declare slot 1
    # in EVENT_LAYOUT (XFloat/Text/Prog/Button), plus overlap refs.
    chains[1] = _fill_with_refs(x, y, w, h, page_bco_global, overlap_ids)
    return CompiledComponent(lei=lei, chains=chains)


__all__ = [
    "SETBRUSH_OVERRIDE",
    "ATTR_ALIASES",
    "make_attr_resolver",
    "build_page_attr_offsets",
    "page_overlap_refs",
    "build_component_chains",
]
