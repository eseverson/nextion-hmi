"""media_blob_generator — the whole per-page media blob (``hexpos`` image).

Assembles a page's media blob from its three generated sections:

    [name-hash table]   build_page_name_hash_table   (hmi_model)
    [frame image]       build_frame_image            (frame_image_generator)
    [page-init chain]   build_page_init_*            (page_init_generator)

The page-init chain references component attributes by their **global**
binattinf record index (cumulative record count of all prior pages + the
attribute's page-local record index); :func:`global_record_index` computes
it.  The page-0 timer ``L`` re-arm marker is emitted with a placeholder
address that ``usercode_assembler.patch_l_markers`` fills once the whole
usercode region is laid out.

Verified byte-for-byte against ``source/nextion.hmi.tft`` (pages 1/2/3
exact; page 0 exact except the back-patched L address).
"""
from __future__ import annotations

from scripts.lib.hmi_model import build_page_name_hash_table
from scripts.lib.media_allocator import layout_from_hmi_page
from scripts.lib.frame_image_generator import build_frame_image
from scripts.lib.page_init_generator import (
    build_constant_page_init, build_page_init_with_codesload,
    build_page_init_with_timer, DOEVENTS_BLOCK, TIMERSET_OPCODE,
)
from scripts.lib.script_compiler import compile_event_handler, CompileContext
from scripts.lib.tft_attrs_encoder import APP_ATT_NAMES


def page_record_counts(pages) -> list[int]:
    """Number of binattinf records each page contributes (the per-page
    block stride sum), in page order — used to map page-local record
    indices to global ones."""
    from scripts.lib.tft_attrs_layout import PER_LEI_LAYOUT
    counts = []
    for hp in pages:
        n = 0
        for hc in hp.components:
            n += PER_LEI_LAYOUT[hc.lei][0]
        counts.append(n)
    return counts


def global_record_index(page_counts, page_index, page_local_index) -> int:
    """Global binattinf record index = cumulative count of prior pages +
    the attribute's page-local record index."""
    return sum(page_counts[:page_index]) + page_local_index


def _attr_record_local_index(component_attstr_slots, attr_name, block_base) -> int:
    """The page-local binattinf record index for ``attr_name`` on a
    component, given its attstr slot table and its block's base record
    index within the page."""
    slot = APP_ATT_NAMES.index(attr_name)
    return component_attstr_slots[slot]


def build_page_init_chain(page, page_index, page_counts, attr_record_lookup,
                          profile=None):
    """Build one page's page-init chain.

    ``attr_record_lookup(comp_name, attr) -> page_local_record_index``
    resolves a component attribute to its page-local binattinf record
    index (the value the attstr slot holds); this module converts it to a
    global index for the bytecode ``LOAD`` operands.  ``profile`` carries the
    editor-version opcode heads (``doevents`` / ``timerset``) + sysvar
    overrides.
    """
    doevents = profile.doevents_opcode if profile else DOEVENTS_BLOCK
    timerset = profile.timerset_opcode if profile else TIMERSET_OPCODE
    sysvar_overrides = dict(profile.sysvar_overrides) if profile else {}

    # Page with a codesload event → draw + compiled handler.
    codesload = page.page_events.get("codesload")
    if codesload:
        ctx = CompileContext(sysvar_overrides=sysvar_overrides)
        # Resolve every <comp>.<attr> ref in the handler to its global
        # record index.
        import re
        refs = set(re.findall(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)", codesload))
        offsets = {}
        for comp, attr in refs:
            local = attr_record_lookup(comp, attr)
            offsets[f"{comp}.{attr}"] = global_record_index(
                page_counts, page_index, local)
        ctx.component_offsets = offsets
        body = compile_event_handler(codesload, ctx)
        return build_page_init_with_codesload(body, doevents)

    # Page with a Timer → arm it.
    timer = next((c for c in page.components if c.lei == 51), None)
    if timer is not None:
        tim_local = attr_record_lookup(timer.name, "tim")
        en_local = attr_record_lookup(timer.name, "en")
        tim_g = global_record_index(page_counts, page_index, tim_local)
        en_g = global_record_index(page_counts, page_index, en_local)
        return build_page_init_with_timer(tim_g, en_g, int(timer.attrs["id"]),
                                          doevents, timerset)

    return build_constant_page_init(doevents)


def build_media_blob(page, page_index, page_counts, attr_record_lookup) -> bytes:
    """Assemble the whole media blob for one page.

    The page-0 timer ``L`` marker address is a placeholder here; the
    usercode assembler back-patches it.
    """
    layout = layout_from_hmi_page(page)
    name_hash = build_page_name_hash_table(page, layout.medatapos)
    frame = build_frame_image(page)
    page_init = build_page_init_chain(page, page_index, page_counts,
                                      attr_record_lookup)
    return name_hash + frame + page_init


__all__ = [
    "page_record_counts",
    "global_record_index",
    "build_page_init_chain",
    "build_media_blob",
]
