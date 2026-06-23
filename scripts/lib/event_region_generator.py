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
from scripts.lib.usercode_assembler import CompiledComponent, suspend_biaoji
from scripts.lib.script_compiler import compile_event_handler, CompileContext

# Default ``setbrush`` opcode head (nxt-1.65.1 / the source fixture); newer
# editor builds shift it (see compiler_profile).  Used only when no
# CompilerProfile is supplied.
_DEFAULT_SETBRUSH_OPCODE = bytes.fromhex("091c08")
SETBRUSH_OVERRIDE = {"setbrush": _DEFAULT_SETBRUSH_OPCODE}


def _setbrush_override(profile):
    """The ``encode_init_block`` opcode override for this profile's
    editor-version-dependent opcode heads (``setbrush`` and the Number
    ``nstr`` draw), defaulting to the source-fixture opcodes."""
    head = profile.setbrush_opcode if profile else _DEFAULT_SETBRUSH_OPCODE
    ov = {"setbrush": head}
    if profile is not None:
        ov["nstr"] = profile.nstr_opcode
    return ov

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
# me/miata-dash#38 adds the remaining visual classes: Curve(0), Number(54),
# GText(55), CheckBox(56), Radio(57), Qrcode(58), Pic(112), Picc(113),
# Zhizhen/Gauge(122).  Touchcap(5) is non-visual (no box) and is excluded.
_VISUAL_LEIS = frozenset({
    59, 116, 106, 109, 1, 98,
    0, 53, 54, 55, 56, 57, 58, 112, 113, 122,
})


def _box(a):
    x = int(a.get("x") or 0)
    y = int(a.get("y") or 0)
    w = int(a.get("w") or 0)
    h = int(a.get("h") or 0)
    return (x, y, x + w, y + h)


def _overlaps(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def page_overlap_refs(components):
    """Return ``{comp_name: [other_id, ...]}`` — for each visual component
    that shares an overlap *cluster*, the ids of the OTHER cluster members
    (ascending), appended as ``ref`` blocks to its auto-fill.

    The editor groups overlapping components into connected components of
    the bounding-box overlap graph (transitive closure): if A overlaps a
    shared neighbour C and B also overlaps C, then A, B and C form one
    cluster and each references the other two — even where A and B don't
    directly overlap (e.g. the two bottom-corner hotspots that both sit on
    the full-width status bar reference each other through it).  A redraw of
    any cluster member then refreshes the whole group, resolving z-order.
    """
    visual = [c for c in components if c.lei in _VISUAL_LEIS]
    boxes = {c.name: _box(c.attrs) for c in visual}

    # Union-find over the direct-overlap graph → overlap clusters.
    parent = {c.name: c.name for c in visual}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    def union(a, b):
        parent[find(a)] = find(b)

    for i, c in enumerate(visual):
        for other in visual[i + 1:]:
            if _overlaps(boxes[c.name], boxes[other.name]):
                union(c.name, other.name)

    clusters: dict[str, list] = {}
    for c in visual:
        clusters.setdefault(find(c.name), []).append(c)

    out = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        ids = {m.name: int(m.attrs.get("id") or 0) for m in members}
        for m in members:
            others = sorted(ids[o.name] for o in members if o.name != m.name)
            out[m.name] = others
    return out


def page_global_text_refs(components):
    """Return ``{comp_name: [other_id, ...]}`` — the EXTRA refresh-ref prefix
    a component's auto-fill gets *ahead of* its transitive-cluster refs
    (:func:`page_overlap_refs`) when a **global Text** is in play.

    Reverse-engineered from the ``global_mixed`` oracle (me/miata-dash#35/#37):
    for each NON-global component ``N`` whose direct-overlap group ``{N} ∪
    {boxes that overlap N}`` contains a ``vscope=1`` Text, every NON-Text
    member ``m`` of that group gets a prefix ``group\\{m}`` (ids ascending)
    prepended to its auto-fill refs.  This is the editor's extra z-order pass
    for a global Text sharing space with numeric components; it fires ONLY
    when a global Text overlaps a non-global, so single/zero-global pages and
    all-numeric multi-global pages (source/live/nextion/3global) are
    unaffected (empty result).
    """
    visual = [c for c in components if c.lei in _VISUAL_LEIS]
    boxes = {c.name: _box(c.attrs) for c in visual}
    ids = {c.name: int(c.attrs.get("id") or 0) for c in visual}
    is_global = {c.name: c.attrs.get("vscope") == 1 for c in visual}
    is_text = {c.name: c.lei == 116 for c in visual}
    is_global_text = {c.name: is_text[c.name] and is_global[c.name]
                      for c in visual}

    out: dict[str, list] = {}
    for n in visual:
        if is_global[n.name]:
            continue
        group = {n.name} | {o.name for o in visual
                            if o.name != n.name
                            and _overlaps(boxes[n.name], boxes[o.name])}
        if not any(is_global_text[m] for m in group):
            continue
        for m in group:
            if is_text[m]:
                continue
            out[m] = sorted(ids[o] for o in group if o != m)
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


# Opcodes for the Checkbox/Radio/Button_T slot-2 toggle handler.
_CLICK_OPCODE = bytes.fromhex("090004")   # touch / click-redraw op
_TJMP_OPCODE = bytes.fromhex("542003")    # the `T` toggle-jump marker


def _load(rec: int) -> bytes:
    return b"\x01" + struct.pack("<I", rec)


def _long(value: int) -> bytes:
    return b"\x03" + struct.pack("<I", value & 0xFFFFFFFF)


# GText scroll-arming opcodes (slot-0 trailing blocks).
_SCROLL_INIT_OPCODE = bytes.fromhex("092408")   # arm the scroll motion
_MOTION_JMP_OPCODE = bytes.fromhex("4c2003")     # `L` motion-jump marker


def _gtext_scroll_blocks(en_rec: int, tim_rec: int, step: int,
                         motion_addr: int) -> bytes:
    """GText's two trailing slot-0 blocks: ``09 24 08 LOAD(en),<step>,
    LOAD(tim),0`` then ``4c 20 03 <motion_addr>``.

    ``step`` (the scroll-step literal) and ``motion_addr`` (the `L` label
    biaoji) are both editor scratch — non-content-derivable; supplied by the
    caller (the profile ``gtext_scroll`` scratch).  Byte-verified against the
    g0 init in 03_untested-components (step 11, addr 0x2814) and
    17_more_components (step 2, addr 0x2476)."""
    scroll = (_SCROLL_INIT_OPCODE + _load(en_rec) + b","
              + str(step).encode("ascii") + b"," + _load(tim_rec) + b",0")
    motion = _MOTION_JMP_OPCODE + struct.pack("<I", motion_addr)
    return _entry(scroll) + _entry(motion)


def _toggle_down_chain(val_rec: int, comp_id: int, *, self_ref: bool) -> bytes:
    """The Checkbox/Radio/Button_T slot-2 touch handler — toggles ``val``.

    Byte-verified against the c0 / r0 / bt0 down handlers in
    ``03_untested-components`` and ``17_more_components``::

        click LOAD(val), 1, 1, long(0x16)     (09 00 04 …)
        LOAD(val) = 0                          (cjmp val==0)
        T long(0x0b)                           (54 20 03 — toggle jump)
        LOAD(val) = 1
        [Button_T only] ref <id>+long(65535)   (self-redraw)

    The ``long(0x16)`` page-record ref and the ``T`` jump address ``0x0b``
    are constants (identical across both fixtures); the ``val`` record is
    page-resolved."""
    out = bytearray()
    out += _entry(_CLICK_OPCODE + _load(val_rec) + b",1,1," + _long(0x16))
    out += _entry(_load(val_rec) + b"=0")
    # `54 20 03 <u32 addr>` — the toggle-jump marker carries a raw 4-byte
    # address (like the `4c 20 03` L marker), NOT a long literal.
    out += _entry(_TJMP_OPCODE + struct.pack("<I", 0x0B))
    out += _entry(_load(val_rec) + b"=1")
    if self_ref:
        out += _entry(REF_OPCODE + str(comp_id).encode("ascii")
                      + b"+" + _long(0xFFFF))
    return bytes(out)


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


def _compile_event(source, attr_offsets, *, with_s_marker=False, profile=None):
    """Compile an HMI event script to its chain body, resolving
    ``<comp>.<attr>`` refs to global record indices.  ``with_s_marker``
    prepends the ``S`` suspend marker (Timer); its biaoji is DERIVED via
    :func:`suspend_biaoji` (the fixed editor seed for the first suspend
    marker — our only case), and any sysvar-index overrides come from
    ``profile``."""
    overrides = dict(profile.sysvar_overrides) if profile else {}
    ctx = CompileContext(sysvar_overrides=overrides)
    ctx.component_offsets = dict(attr_offsets)
    ctx.globals = {"sys0": 0, "sys1": 4, "sys2": 8}
    body = compile_event_handler(source, ctx)
    if with_s_marker:
        # Our compiler emits exactly one suspend marker (the single Timer);
        # its biaoji is the derived index-0 seed, not a captured per-project
        # constant (me/miata-dash#40).
        biaoji = suspend_biaoji(0)
        s_entry = _entry(b"\x53\x20\x03" + struct.pack("<I", biaoji))
        return s_entry + body
    return body


def _fill_with_refs(x, y, w, h, bco_record, overlap_ids, prefix_ids=()):
    """Auto-fill block + a ``ref <id>`` entry for each neighbour.

    ``prefix_ids`` (the global-Text z-order prefix, see
    :func:`page_global_text_refs`) are emitted FIRST, then the transitive
    overlap-cluster ``overlap_ids``."""
    out = _fill_chain(x, y, w, h, bco_record)
    for oid in tuple(prefix_ids) + tuple(overlap_ids):
        out += _entry(REF_OPCODE + str(oid).encode("ascii"))
    return out


def build_component_chains(hc, attstr_slots, page_record_base,
                           page_bco_global, page_id,
                           attr_offsets=None, overlap_ids=None,
                           profile=None, prefix_ids=None) -> CompiledComponent:
    """Build one component's per-slot chains for the event region.

    ``hc`` is the HMI component model; ``attstr_slots`` its attstr slot
    table; ``page_record_base`` the cumulative prior-page record count;
    ``page_bco_global`` the page's bco record (global); ``page_id`` the
    device page id (for the page fill geometry / hotspot nav).
    ``attr_offsets`` is the page-wide ``{"<comp>.<attr>": global_record}``
    map used to compile event-script slots (Timer/Slider).  ``overlap_ids``
    are the ids of visual neighbours whose box overlaps this component (a
    ``ref <id>`` is appended to the auto-fill for each); ``prefix_ids`` are
    the global-Text z-order refs emitted *ahead* of them (see
    :func:`page_global_text_refs`).  ``profile`` carries the editor-version
    constants (setbrush opcode, sysvar overrides) and per-project scratch
    (the Timer ``S`` biaoji).
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
    prefix_ids = prefix_ids or []
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
        chains[1] = _fill_with_refs(x, y, w, h, page_bco_global, overlap_ids, prefix_ids)
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
            chains[4] = _compile_event(src, attr_offsets, with_s_marker=True,
                                       profile=profile)
        return CompiledComponent(lei=lei, chains=chains)

    if lei == 1:
        # Slider: slot 1 = auto-fill; slots 2/3/4 = codesdown/up/slide.
        chains[1] = _fill_with_refs(x, y, w, h, page_bco_global, overlap_ids, prefix_ids)
        for key, slot in _EVENT_SLOTS[1].items():
            src = hc.events.get(key, "").strip()
            if src:
                chains[slot] = _compile_event(src, attr_offsets,
                                              profile=profile)
        return CompiledComponent(lei=lei, chains=chains)

    if lei == 98:
        # Button: slot 0 = init (cjmp press/release), slot 1 = auto-fill,
        # slot 2 = press (val=1 + self-ref + user codesdown), slot 3 =
        # release (val=0 + self-ref).
        comp = Component(comp_type=lei, comp_id=int(a.get("id") or 0),
                         page_id=page_id, x=x, y=y, w=w, h=h, attrs=a)
        init = encode_init_block(comp, resolver,
                                 opcode_override=_setbrush_override(profile))
        if init:
            chains[0] = init
        chains[1] = _fill_with_refs(x, y, w, h, page_bco_global, overlap_ids, prefix_ids)
        bid = int(a.get("id") or 0)
        val_ref = f"{hc.name}.val"
        # Self-refresh block: `ref <id>+long(65535)` (the button's redraw).
        self_ref = _entry(REF_OPCODE + str(bid).encode("ascii")
                          + b"+" + b"\x03" + struct.pack("<I", 0xFFFF))
        down = _compile_event(f"{val_ref}=1", attr_offsets,
                              profile=profile) + self_ref
        user = hc.events.get("codesdown", "").strip()
        if user:
            down += _compile_event(user, attr_offsets, profile=profile)
        chains[2] = down
        chains[3] = _compile_event(f"{val_ref}=0", attr_offsets,
                                   profile=profile) + self_ref
        return CompiledComponent(lei=lei, chains=chains)

    # Display / interactive components: slot 0 = init draw, slot 1 =
    # auto-fill (background restore at own geometry using page_bco).
    comp = Component(
        comp_type=lei, comp_id=int(a.get("id") or 0), page_id=page_id,
        x=x, y=y, w=w, h=h, attrs=a,
    )
    init = encode_init_block(comp, resolver,
                             opcode_override=_setbrush_override(profile))
    if init:
        chains[0] = init
    # GText(55) appends two scroll-arming blocks to its init (slot 0): the
    # `09 24 08` scroll-init call + the `4c 20 03` motion-jump marker.  Both
    # the step literal and the motion-label address are non-content-derivable
    # editor scratch (they differ between two saves of an IDENTICAL g0), so
    # they are supplied via the profile's ``gtext_scroll`` scratch
    # (me/miata-dash#38) — like the Timer `S` biaoji.  Absent scratch → the
    # scroll blocks are omitted (the rest of the init is still byte-exact).
    if lei == 55 and init:
        scratch = (profile.gtext_scroll if profile else None) or {}
        params = scratch.get(hc.name) or scratch.get("*")
        if params is not None:
            step, motion_addr = params
            chains[0] = init + _gtext_scroll_blocks(
                resolver("en"), resolver("tim"), step, motion_addr)
    # The auto-fill exists for the display components that declare slot 1
    # in EVENT_LAYOUT (XFloat/Text/Prog/Button), plus overlap refs.
    chains[1] = _fill_with_refs(x, y, w, h, page_bco_global, overlap_ids, prefix_ids)
    # Checkbox(56) / Radio(57) / Button_T(53) carry a slot-2 touch handler
    # that toggles ``val`` (me/miata-dash#38).
    if lei in (53, 56, 57):
        chains[2] = _toggle_down_chain(resolver("val"), int(a.get("id") or 0),
                                       self_ref=(lei == 53))
    return CompiledComponent(lei=lei, chains=chains)


__all__ = [
    "SETBRUSH_OVERRIDE",
    "ATTR_ALIASES",
    "make_attr_resolver",
    "build_page_attr_offsets",
    "page_overlap_refs",
    "build_component_chains",
]
