"""hmi_model — bridge the parsed HMI into the usercode assembler's model.

The whole-file round-trip (``tests/sim/test_usercode_assembler.py``)
historically recovered every per-component event/init chain straight
from the *TFT* (``_chain_body``) and fed it to
:class:`usercode_assembler.CompiledComponent`.  That made the TFT, not
the HMI, the source of truth.

This module reads the **HMI** (via :func:`sim.loader.load_hmi`) and
produces the same per-page component model the assembler consumes — the
component *order*, each component's ``lei`` (type code), the set of
*active event slots*, and the page/component event-NAME strings — driven
purely from the HMI's declared pages, components, and event scripts.

It is the realisation of work-item #1 ("read the .HMI as the source of
truth; drive the event region from it").  The chain *bodies* themselves
(the compiled bytecode) still depend on the per-page attribute-record
table for their ``LOAD`` operand addresses — that table is generated in
work-item #2.  Until then, :func:`build_page_models` accepts a
``chain_source`` callback so a caller can supply the bodies (e.g. from
the verified TFT) while the *structure* around them is HMI-derived.

## HMI event → compiled slot mapping

Each component type registers a fixed ordered list of event slots in the
compiled TFT.  Slot 0 is always the component's *init* (the "Ref" event —
the persistent draw bytecode).  The remaining slots map to the HMI's
``codes*`` event keys:

  * ``codesdown``   → "Touch Press"   slot
  * ``codesup``     → "Touch Release" slot
  * ``codesslide``  → "Slide"         slot (Slider only)
  * ``codestimer``  → "Timer"         slot (Timer only)

The mapping is *type-specific* and captured in :data:`SLOT_MAP` below —
it mirrors the slot indices the editor writes into each objxinxi entry.

### Hotspot navigation quirk

A Hotspot (lei 109) declares its navigation in the HMI under
``codesdown`` (the "Touch Press Event" text box).  The editor, however,
compiles that source into the **release** slot (slot 2) and synthesises
a press-highlight ``fill`` into the press slot (slot 1).  So a Hotspot's
HMI ``codesdown`` body is the user's navigation script, but it lands in
the compiled *up* slot; the *down* slot is auto-generated.  See
``findings/next-steps.md`` and the byte dump in the assembler corpus.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from scripts.lib.usercode_assembler import CompiledComponent, EVENT_LAYOUT


# ---------------------------------------------------------------------------
# Per-type HMI-event → compiled-slot mapping
# ---------------------------------------------------------------------------
#
# Each entry maps an HMI ``codes*`` event key to the objxinxi slot index
# the editor compiles it into.  Slot 0 (init / Ref) is never in this map
# — it is synthesised by the init encoder, not from a ``codes*`` script.
#
# Derived by correlating the HMI event scripts against the per-slot
# chains in source/nextion.hmi.tft (see module docstring and the
# objxinxi dump in findings/next-steps.md STEP 4):
#
#   * XFloat/Text/Prog (59/116/106): codesdown→1, codesup→2 (rarely used).
#   * Hotspot (109): codesdown→2 (NAV goes in the RELEASE slot!); the
#     press slot (1) is an auto-generated highlight fill, not a
#     codes* script.
#   * Slider (1): codesdown→1, codesup→2, codesslide→3.
#   * Button (98): codesdown→1, codesup→2.
#   * Timer (51): codestimer→4.
#   * Page (121): codesload / codesunload are page-level events, handled
#     separately (they do not map to a per-component objxinxi slot here).

SLOT_MAP: dict[int, dict[str, int]] = {
    59:  {"codesdown": 1, "codesup": 2},                 # XFloat
    116: {"codesdown": 1, "codesup": 2},                 # Text
    106: {"codesdown": 1, "codesup": 2},                 # Prog
    109: {"codesdown": 2, "codesup": 2},                 # Hotspot (nav→release)
    1:   {"codesdown": 1, "codesup": 2, "codesslide": 3},  # Slider
    98:  {"codesdown": 1, "codesup": 2},                 # Button
    51:  {"codestimer": 4},                              # Timer
    52:  {},                                             # Variable (no events)
    121: {},                                             # Page (page-level events)
}

# Component types that always carry an auto-generated init (slot 0) in
# the compiled TFT, even with no HMI event script.  Everything with a
# non-empty Ref-event template (see tft_init_encoder.EMPTY_INIT_TYPES).
INIT_SLOT = 0


@dataclass
class HmiComponentModel:
    """One HMI component in objxinxi (= declaration) order."""

    lei: int
    name: str
    comp_id: int
    attrs: dict
    events: dict[str, str]   # codes* key → source script (non-empty only)

    def layout_slots(self) -> set[int]:
        """The set of compiled-chain slot indices this component's *type*
        emits, read straight off the assembler's ``EVENT_LAYOUT``.

        In the project TFT the populated objxinxi slot set is constant per
        ``lei`` (verified across all 39 components): the editor synthesises
        every layout slot for a given type regardless of which HMI
        ``codes*`` scripts are present.  The HMI events change a slot's
        *content* (e.g. the hotspot release slot's navigation, the timer
        slot's cascade) — not which slots exist.  So the structure is a
        pure function of ``lei``.
        """
        layout = EVENT_LAYOUT.get(self.lei)
        if layout is None:
            raise KeyError(f"no EVENT_LAYOUT for lei={self.lei} ({self.name!r})")
        return {op[1] for op in layout if op[0] == "chain"}

    def event_slot(self, codes_key: str) -> Optional[int]:
        """Map an HMI ``codes*`` key to its compiled slot index, or
        ``None`` if this type does not route that event to a slot."""
        return SLOT_MAP.get(self.lei, {}).get(codes_key)


@dataclass
class HmiPageModel:
    """One HMI page in device-id order, with its page pseudo-component
    (lei 121) prepended to the user components — matching objxinxi order."""

    page_id: int
    name: str
    page_attrs: dict
    page_events: dict[str, str]
    components: list[HmiComponentModel]


def load_hmi_pages(hmi_path: str | Path) -> list[HmiPageModel]:
    """Parse the HMI into device-id-ordered page models.

    The page pseudo-component (lei 121) is synthesised as component 0 of
    each page's component list (it owns slot-0 init = the page background
    fill, plus the page-level codesload/codesunload events).
    """
    from sim.loader import load_hmi

    state = load_hmi(str(hmi_path))
    pages: list[HmiPageModel] = []
    for p in sorted(state.pages.values(), key=lambda pg: pg.id):
        comps: list[HmiComponentModel] = []
        # Page pseudo-component first (objxinxi slot for the page itself).
        comps.append(HmiComponentModel(
            lei=121, name=p.name, comp_id=0,
            attrs=dict(p.attrs),
            events={k: v for k, v in p.events.items() if v and v.strip()},
        ))
        for c in p.components:
            comps.append(HmiComponentModel(
                lei=c.type, name=c.name, comp_id=c.id,
                attrs=dict(c.attrs),
                events={k: v for k, v in c.events.items() if v and v.strip()},
            ))
        pages.append(HmiPageModel(
            page_id=p.id, name=p.name,
            page_attrs=dict(p.attrs),
            page_events={k: v for k, v in p.events.items() if v and v.strip()},
            components=comps,
        ))
    return pages


# Callback resolving (page_index, component_index, slot_index) → chain
# body bytes (length-prefixed cglist entries WITHOUT the trailing zero
# terminator). Returns b"" for an empty slot.
ChainSource = Callable[[int, int, int], bytes]


def build_compiled_components(
    page: HmiPageModel,
    page_index: int,
    chain_source: ChainSource,
) -> list[CompiledComponent]:
    """Build the assembler's :class:`CompiledComponent` list for one page
    from the HMI model.

    The component *order*, ``lei``, and which slots are active come from
    the HMI.  Each active slot's chain *body* is fetched via
    ``chain_source`` (work-item #2 will replace that callback with a real
    HMI-driven bytecode compiler once the attribute-record table is
    generated).  A component contributes slot 0 (init) whenever its type
    has a non-empty event layout that includes a slot-0 chain.
    """
    comps: list[CompiledComponent] = []
    for ci, hc in enumerate(page.components):
        # Every slot the type's layout declares gets a body; the editor
        # synthesises all of them (init draw, background fill, button
        # states, user event scripts).  Their *content* is what is HMI-
        # vs-TFT-driven — work-item #2 replaces ``chain_source`` with a
        # real bytecode compiler once the attribute table is generated.
        chains: dict[int, bytes] = {}
        for slot in hc.layout_slots():
            body = chain_source(page_index, ci, slot)
            if body:
                chains[slot] = body
        comps.append(CompiledComponent(lei=hc.lei, chains=chains))
    return comps


# ---------------------------------------------------------------------------
# Compiling chain sources (HMI bytecode, where no attribute table is needed)
# ---------------------------------------------------------------------------
#
# A few slots compile from HMI source with NO dependency on the per-page
# attribute-record table (whose generation is work-item #2): their
# bytecode references only the ``page`` opcode and integer/page-id
# literals, never a component-attribute ``LOAD`` operand.  Those slots can
# already be GENERATED from the HMI rather than copied from the TFT.
#
#   * Hotspot (lei 109) navigation — the HMI ``codesdown`` script ("page
#     N") compiles into the RELEASE slot (slot 2).  ``page N`` lowers to
#     ``09 0b 04 <ascii N>`` wrapped in a length prefix; N is the device
#     page id, which the HMI source already carries.
#
# Everything else (auto-fill background restores, the timer cascade, the
# button press/release states, init draws) needs LOAD operands resolved
# through the attribute table and is therefore still sourced from the
# verified TFT until work-item #2 lands.


# Slots that can be compiled directly from an HMI ``codes*`` script with
# no attribute-table dependency, keyed by (lei, slot) → codes* key.
HMI_COMPILABLE_SLOTS: dict[tuple[int, int], str] = {
    (109, 2): "codesdown",   # Hotspot navigation → release slot
}


def compile_slot_from_hmi(comp: HmiComponentModel, slot: int):
    """Compile one slot's chain body from the HMI event source if it is in
    :data:`HMI_COMPILABLE_SLOTS`, else return ``None`` (caller falls back
    to another source).

    Returns the length-prefixed cglist body WITHOUT the trailing zero
    terminator (the assembler's layout adds it), matching the
    :class:`usercode_assembler.CompiledComponent` contract.
    """
    key = HMI_COMPILABLE_SLOTS.get((comp.lei, slot))
    if key is None:
        return None
    src = comp.events.get(key)
    if not src or not src.strip():
        return None
    # Local import keeps the (heavy) instruction-set tables out of the
    # module-load path for callers that only need the structural model.
    from scripts.lib.script_compiler import compile_event_handler, CompileContext
    return compile_event_handler(src, CompileContext())


def hmi_first_chain_source(pages: list[HmiPageModel], fallback: ChainSource) -> ChainSource:
    """Wrap a ``fallback`` chain source so that any slot listed in
    :data:`HMI_COMPILABLE_SLOTS` is GENERATED from the HMI script, and all
    other slots defer to ``fallback`` (typically the verified TFT bodies).

    ``pages`` is the same device-id-ordered list passed to
    :func:`build_compiled_components`, so the (page_index, comp_index)
    coordinates line up.
    """

    def source(page_index: int, comp_index: int, slot: int) -> bytes:
        comp = pages[page_index].components[comp_index]
        body = compile_slot_from_hmi(comp, slot)
        if body is not None:
            return body
        return fallback(page_index, comp_index, slot)

    return source


__all__ = [
    "SLOT_MAP",
    "INIT_SLOT",
    "HMI_COMPILABLE_SLOTS",
    "HmiComponentModel",
    "HmiPageModel",
    "load_hmi_pages",
    "build_compiled_components",
    "compile_slot_from_hmi",
    "hmi_first_chain_source",
    "ChainSource",
]
