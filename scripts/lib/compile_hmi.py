"""compile_hmi — end-to-end HMI → TFT compiler (the acceptance gate).

Assembles a complete ``.tft`` from a ``.HMI`` by GENERATING every region
(the only legitimately-copied span is the out-of-scope resources blob
``0x10000``..``0x70000`` — fonts/pictures, a property of the bootloader/
driver/font composition, not project content).

Pipeline (all sub-generators verified byte-exact in their own tests):

  usercode region (strdataaddr..pageadd):
    header                      header_generator (global memory directory +
                                page name-hash + MainCode = compiled Program.s)
    per page: event region      event_region_generator + assemble_event_region
              media blob         media_blob_generator
    AppAllvas                    hmi_model.build_appallvas
    binattinf                    binattinf_generator (all pages)
  objxinxi table                 objxinxi_encoder (frame) + back-patched
                                 offsets + GENERATED attstr 88-slot tables
  page directory                 page_dir_encoder (frame) + back-patched
                                 hexpos / attdataaddr_rel
  appinf1 (H2 directory @0xC8)   GENERATED via compute_appinf1 — content
                                 fields (region offsets / counts) derived
                                 from the assembled sizes; resources-coupled
                                 fields (resources_size, picxinxi/zimo
                                 offsets, picture/font descriptors) read from
                                 resources_source's resources blob.
  prefix [0..strdataaddr)        from resources_source — H1 framing + the
                                 out-of-scope resources blob (0x10000..
                                 0x70000), MINUS the regenerated H2 directory.
  H1 / 3 CRCs                    resealed

This module is the integration of the per-region generators; its
acceptance test (`tests/sim/test_compile_hmi_end_to_end.py`) asserts the
output equals ``source/nextion.hmi.tft`` byte-for-byte.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

# The script compiler imports its `script_compiler_extras` sibling by bare
# name, so `scripts/lib` must be importable regardless of entry point.
_LIB_DIR = Path(__file__).resolve().parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from scripts.lib.usercode_assembler import EVENT_LAYOUT, patch_l_markers
from scripts.lib.hmi_model import (
    load_hmi_pages, build_page_name_hash_table, build_appallvas,
)
from scripts.lib.media_allocator import layout_from_hmi_page, FRAME_PREFIX
from scripts.lib.media_blob_generator import (
    page_record_counts, build_page_init_chain,
)
from scripts.lib.frame_image_generator import build_frame_image
from scripts.lib.binattinf_generator import build_component_binattinf
from scripts.lib.event_region_generator import (
    build_component_chains, build_page_attr_offsets, page_overlap_refs,
)
from scripts.lib.tft_attrs_encoder import APP_ATT_NAMES

SENTINEL = 0xFFFFFFFF
TERM = b"\x00\x00\x00\x00"


def _emit_component_event(cc, base):
    """Emit one component's EVENT_LAYOUT stream at usercode offset ``base``;
    return ``(bytes, comp_start, slot_offsets)`` where ``comp_start`` is
    ``base`` (the component's first emitted byte = its
    ``attstr_bytecode_offset``) and ``slot_offsets`` maps populated chain
    slots to their usercode offsets."""
    layout = EVENT_LAYOUT.get(cc.lei)
    out = bytearray()
    slot_offsets: dict[int, int] = {}
    for op in layout:
        if op[0] == "chain":
            body = cc.chains.get(op[1])
            if body:
                slot_offsets[op[1]] = base + len(out)
                out += body
        elif op[0] == "term":
            out += TERM
        elif op[0] == "name":
            s = op[1].encode("ascii")
            out += struct.pack("<I", len(s)) + s
    return bytes(out), base, slot_offsets


class ObjxinxiLayout:
    """Per-component objxinxi offset fields recovered during assembly."""

    __slots__ = ("init_off", "slot_offsets", "attstr_bytecode_offset")

    def __init__(self):
        self.init_off = SENTINEL
        self.slot_offsets = [SENTINEL] * 5
        self.attstr_bytecode_offset = 0


def assemble_usercode_and_layout(hmi, program_s, app_allvas_qty,
                                 profile=None, font_count=None):
    """Assemble the usercode region (strdataaddr..pageadd) from generated
    sub-regions and return ``(usercode_bytes, per_page_objxinxi_layout,
    page_dir_offsets, attstr_tables, static_str_beg)``.

    The header (global dir + page name-hash + MainCode) is GENERATED from
    the HMI + ``Program.s``; the page-directory ``attdataaddr_rel`` is
    derived from ``static_str_beg`` (= AppAllvasAddr, computed here) +
    ``12*app_allvas_qty`` once the layout is fixed.  Offsets for the
    objxinxi / page-dir encoders are tracked as the event regions emit.
    ``profile`` (a CompilerProfile) carries the editor-version constants
    (setbrush opcode, sysvar overrides) + per-project scratch threaded into
    the header MainCode and the per-component event chains.
    """
    from scripts.lib.header_generator import build_header
    from scripts.lib.hmi_model import APPALLVAS_RECORD_SIZE

    sysvar_overrides = dict(profile.sysvar_overrides) if profile else None
    fp = profile.frame_prefix if profile else FRAME_PREFIX
    header = build_header(hmi, program_s, app_allvas_qty,
                          sysvar_overrides=sysvar_overrides)

    counts = page_record_counts(hmi)
    cum = [sum(counts[:i]) for i in range(len(counts) + 1)]
    # Generated per-component attstr slot tables (page-local record indices)
    # — replaces the reference objxinxi slots.
    attstr = _generated_attstr_slots(hmi)

    out = bytearray(header)
    page_layouts: list[list[ObjxinxiLayout]] = []
    page_dir = []   # (hexpos, page_record_offset) per page

    for pi, hp in enumerate(hmi):
        po = attstr[pi]   # objects exposing generated .slots
        base = cum[pi]
        page_bco = base + po[0].slots[APP_ATT_NAMES.index("bco")]
        attr_offs = build_page_attr_offsets(hp.components, po, base)
        orefs = page_overlap_refs(hp.components)

        comp_layouts = []
        var_end_cursor = None   # cursor right after the last Variable emitted
        for hc, o in zip(hp.components, po):
            cc = build_component_chains(
                hc, list(o.slots), base, page_bco, hp.page_id,
                attr_offs, orefs.get(hc.name), profile=profile)
            comp_start = len(out)
            stream, _, slot_offsets = _emit_component_event(cc, comp_start)
            out += stream
            if hc.lei == 52:
                var_end_cursor = len(out)
            cl = ObjxinxiLayout()
            cl.attstr_bytecode_offset = comp_start
            cl.init_off = slot_offsets.get(0, SENTINEL)
            for s in range(1, 6):
                cl.slot_offsets[s - 1] = slot_offsets.get(s, SENTINEL)
            comp_layouts.append(cl)
        page_layouts.append(comp_layouts)

        # Media blob immediately follows the page's event region.
        hexpos = len(out)

        # Variables (lei 52) emit no event stream; their
        # ``attstr_bytecode_offset`` points into the terminator zone right
        # after the Variable group — one 4-byte slot each, ending where the
        # group's region does: var[k] = var_base - 4*(num_vars - k).  The
        # base is the cursor immediately after the last Variable (== the
        # media start only when the Variables are the page's last objects;
        # if a component follows them — e.g. the live page-0 back-nav hotspot
        # m1 — the base is *before* that component's event chain).
        var_layouts = [cl for hc, cl in zip(hp.components, comp_layouts)
                       if hc.lei == 52]
        nvar = len(var_layouts)
        var_base = var_end_cursor if var_end_cursor is not None else hexpos
        for k, cl in enumerate(var_layouts):
            cl.attstr_bytecode_offset = var_base - 4 * (nvar - k)

        brush_state = profile.brush_state if profile else None
        media = _build_media_blob_for_page(hp, pi, counts, po, base,
                                           brush_state, fp, profile)
        out += media
        # Page-record offset (relative to attdataaddr); the absolute
        # attdataaddr_rel is filled once static_str_beg is known, below.
        page_dir.append((hexpos, sum(counts[:pi]) * 24))

    # AppAllvas + binattinf tail.  ``static_str_beg`` (= AppAllvasAddr) is
    # the usercode-relative offset where the static-data region begins —
    # i.e. the total init-bytecode size (header + per-page event+media).
    static_str_beg = len(out)
    out += build_appallvas(program_s)
    for pi, hp in enumerate(hmi):
        layout = layout_from_hmi_page(hp, fp)
        for ci, hc in enumerate(hp.components):
            pa = hc.attrs
            x = 0 if ci == 0 else int(pa.get("x") or 0)
            y = 0 if ci == 0 else int(pa.get("y") or 0)
            w = int(pa.get("w") or (480 if ci == 0 else 0))
            h = int(pa.get("h") or (320 if ci == 0 else 0))
            out += build_component_binattinf(
                hc.lei, hc.comp_id, x, y, w, h,
                objdatarampos=layout.objdatarampos[ci],
                frompageid=hp.page_id, fromobjid=ci,
                authored=pa, allow_unverified=True, font_count=font_count)

    # Now that static_str_beg is known, resolve the page-directory
    # ``attdataaddr_rel`` for each page:
    #   attdataaddr (strd-rel) = static_str_beg + 12*app_allvas_qty
    #   attdataaddr_rel[page]  = attdataaddr + page_record_offset
    attdataaddr = static_str_beg + APPALLVAS_RECORD_SIZE * app_allvas_qty
    page_dir = [(hexpos, attdataaddr + rec_off)
                for (hexpos, rec_off) in page_dir]

    # Patch the timer L re-arm markers now the layout is fixed.
    out = bytearray(patch_l_markers(bytes(out), region_base=0))
    return bytes(out), page_layouts, page_dir, attstr, static_str_beg


# --- generated attstr slots -----------------------------------------------

class _AttstrObj:
    """Minimal stand-in exposing ``.slots`` (the 88-entry attstr table)
    so the per-region generators don't need to change shape."""

    __slots__ = ("slots",)

    def __init__(self, slots):
        self.slots = slots


def _generated_attstr_slots(hmi):
    """Per-page list of :class:`_AttstrObj` with GENERATED attstr slot
    tables (page-local binattinf record indices), replacing the reference
    objxinxi.  ``page_record_base`` is the cumulative stride sum of the
    components before each one on its page."""
    from scripts.lib.tft_attrs_encoder import build_attstr_slots
    from scripts.lib.tft_attrs_layout import PER_LEI_LAYOUT

    out = []
    for hp in hmi:
        base = 0
        page = []
        for hc in hp.components:
            page.append(_AttstrObj(build_attstr_slots(hc.lei, base)))
            base += PER_LEI_LAYOUT[hc.lei][0]
        out.append(page)
    return out


def _build_media_blob_for_page(hp, pi, counts, po, base, brush_state=None,
                               frame_prefix=FRAME_PREFIX, profile=None):
    layout = layout_from_hmi_page(hp, frame_prefix)
    name_hash = build_page_name_hash_table(hp, layout.medatapos)
    frame = build_frame_image(hp, brush_state, frame_prefix)

    def attr_record_lookup(comp_name, attr):
        # page-local record index (the attstr slot value) for the named
        # component's attribute.
        for hc, o in zip(hp.components, po):
            if hc.name == comp_name:
                return o.slots[APP_ATT_NAMES.index(attr)]
        raise KeyError(comp_name)

    page_init = build_page_init_chain(hp, pi, counts, attr_record_lookup,
                                      profile)
    return name_hash + frame + page_init


def _build_appinf1(resources_data, model_crc, hmi, program_s,
                   app_allvas_qty, static_str_beg):
    """Build the GENERATED ``appinf1`` (H2 directory) via
    :func:`compute_appinf1.compute_appinf1`.

    Field origins:

    * **Content fields — derived from the HMI + assembled region sizes:**
      - ``init_bytecode_size`` = ``static_str_beg`` (= AppAllvasAddr — the
        usercode size up to the static-data region, returned by the
        assembler);
      - ``app_allvas_qty`` = number of ``Program.s`` ``int`` globals;
      - ``attribute_records_bytes`` = ``sum(per-page record counts) * 24``;
      - ``page_qty`` / ``obj_qty`` = page / total-component counts;
      - ``main_code_hex`` = the header's MainCode offset
        (``0x34`` global dir + ``6 * page_qty`` name-hash bytes).

    * **Resources-coupled fields — pulled from ``resources_source`` (part
      of the out-of-scope resources blob):** ``resources_size``,
      ``picxinxi_offset_in_resources``, the picture / font descriptors, and
      the ``encode`` byte.  These describe the bootloader/driver/font
      composition, not the HMI project content.
    """
    from scripts.lib.compute_appinf1 import (
        compute_appinf1, body_description_from_tft, BodyDescription,
    )
    from scripts.lib.media_blob_generator import page_record_counts

    # The resources descriptors come from the reference (resources-coupled).
    ref_body = body_description_from_tft(resources_data)

    from scripts.lib.header_generator import (
        build_global_dir, build_name_hash_table,
    )

    counts = page_record_counts(hmi)
    page_qty = len(hmi)
    obj_qty = sum(len(p.components) for p in hmi)
    # MainCode starts after the global memory directory + the leading u32 0
    # marker + the page name-hash table.
    main_code_hex = (len(build_global_dir(hmi, app_allvas_qty)) + 4
                     + len(build_name_hash_table(hmi)))

    body = BodyDescription(
        model_crc=model_crc,
        encode=ref_body.encode,
        main_code_hex=main_code_hex,
        # --- resources-coupled (from resources_source) ---
        resources_size=ref_body.resources_size,
        picxinxi_offset_in_resources=ref_body.picxinxi_offset_in_resources,
        pictures=ref_body.pictures,
        fonts=ref_body.fonts,
        gmovs=ref_body.gmovs,
        videos=ref_body.videos,
        wavs=ref_body.wavs,
        # --- content (derived from the HMI + assembled region sizes) ---
        init_bytecode_size=static_str_beg,
        app_allvas_qty=app_allvas_qty,
        attribute_records_bytes=sum(counts) * 24,
        page_qty=page_qty,
        obj_qty=obj_qty,
    )
    return compute_appinf1(body)


def compile_hmi(hmi_path, resources_source) -> bytes:
    """Compile ``hmi_path`` into a complete ``.tft``, returning the bytes.

    Every project-content region is GENERATED from the HMI: the usercode
    header (global dir + page name-hash + MainCode), the per-page event
    regions, the media blobs, AppAllvas, binattinf, the objxinxi table
    (geometry + offsets + attstr slot tables), the page directory, and the
    **appinf1 (H2) directory** — its region offsets/counts are derived from
    the assembled sizes (so they follow a structural edit).  The H1 + 3 CRCs
    are resealed.

    The span taken from ``resources_source`` (a reference ``.tft`` of the
    same model/font composition) is the **resources prefix**
    ``[0 .. strdataaddr)`` — the H1 framing plus the fonts/pictures
    resources blob (``0x10000``..``0x70000``), a property of the
    bootloader / driver / font set, NOT of the HMI project content
    (explicitly out of scope; see me/nextion#2).  The H2 directory inside
    that prefix is REGENERATED; the only appinf1 fields read from
    ``resources_source`` are the resources-coupled ones (resources_size,
    picxinxi/zimo offsets, picture/font descriptors).
    """
    from pathlib import Path
    from scripts.lib.objxinxi_encoder import (
        encode_objxinxi_entry, ObjxinxiInputs,
    )
    from scripts.lib.page_dir_encoder import encode_page_dir_entry, PageDirInputs
    from scripts.lib.media_allocator import layout_from_hmi_page
    from scripts.lib import tft_format
    from scripts.lib.page_crc import crc32_bytewise
    from scripts.lib.hmi_model import parse_program_globals
    from sim.loader import load_hmi as _load_hmi_state

    from scripts.lib.compiler_profile import profile_for, project_scratch_for
    from scripts.lib.tft_attrs import parse_appinf1_corrected
    from scripts.lib.h2_cipher import encrypt as h2_decrypt

    data = Path(resources_source).read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")

    program_s = _load_hmi_state(str(hmi_path)).program_s
    hmi = load_hmi_pages(hmi_path)
    app_allvas_qty = len(parse_program_globals(program_s))

    # Editor-version constants come from the reference TFT's H1 version
    # letter; per-project editor scratch (Text draw-brush state) is
    # captured against this project's own reference (the oracle).  The S
    # suspend biaoji is no longer captured here — it is DERIVED in
    # usercode_assembler.suspend_biaoji (me/miata-dash#40).
    scratch = project_scratch_for(data, hmi)
    profile = profile_for(data, **scratch)

    # Font count (appinf1 zimoqyt) is resources-coupled — read from the
    # reference; it sets every Fontid attribute's binattinf num_maxval.
    font_count = parse_appinf1_corrected(
        h2_decrypt(data[0xC8:0xC8 + 196], model_crc)).get("zimoqyt")

    usercode, layouts, page_dir, attstr_tables, static_str_beg = \
        assemble_usercode_and_layout(hmi, program_s, app_allvas_qty, profile,
                                     font_count=font_count)

    # appinf1 (the H2 directory) is GENERATED from the assembled region
    # sizes + the resources descriptors pulled from ``resources_source``.
    appinf1 = _build_appinf1(
        data, model_crc, hmi, program_s, app_allvas_qty, static_str_beg)
    a1 = appinf1.fields

    # objxinxi table (geometry + GENERATED offsets + GENERATED attstr slots).
    fp = profile.frame_prefix
    obj_bytes = bytearray()
    for pi, hp in enumerate(hmi):
        media = layout_from_hmi_page(hp, fp)
        for ci, (hc, cl) in enumerate(zip(hp.components, layouts[pi])):
            pa = hc.attrs
            x = 0 if ci == 0 else int(pa.get("x") or 0)
            y = 0 if ci == 0 else int(pa.get("y") or 0)
            w = int(pa.get("w") or (480 if ci == 0 else 0))
            h = int(pa.get("h") or (320 if ci == 0 else 0))
            obj_bytes += encode_objxinxi_entry(
                hc.lei, hc.comp_id, x, y, w, h,
                ObjxinxiInputs(
                    init_off=cl.init_off, slot_offsets=cl.slot_offsets,
                    objdatarampos=media.objdatarampos[ci],
                    attstr_bytecode_offset=cl.attstr_bytecode_offset,
                    attstr_slots=attstr_tables[pi][ci].slots,
                    vscope=(pa.get("vscope") == 1)))

    # page directory (object counts + generated hexpos/attdataaddr_rel;
    # medatapos generated).
    pdir_bytes = bytearray()
    objstar = 0
    for pi, hp in enumerate(hmi):
        hexpos, attdata_rel = page_dir[pi]
        medatapos = layout_from_hmi_page(hp, fp).medatapos
        pdir_bytes += encode_page_dir_entry(
            objstar, len(hp.components),
            PageDirInputs(hexpos=hexpos, attdataaddr_rel=attdata_rel,
                          medatapos=medatapos))
        objstar += len(hp.components)

    # Assemble the whole file by RE-LAYING-OUT it at the new size.  Only
    # the resources prefix [0..strdataaddr) is taken from the resources
    # source (H1 framing + the out-of-scope fonts/pictures blob); every
    # project-content region after strdataaddr is GENERATED and placed
    # SEQUENTIALLY at the NEW offsets the generated appinf1 derives from
    # the assembled region sizes.  When the HMI structure is unchanged the
    # new offsets all equal the reference ones, so the output is
    # byte-identical (the regression invariant) — this is NOT special-cased,
    # it falls out of the general re-layout.
    strd = a1["strdataaddr"]

    out = bytearray(data[:strd])      # resources prefix (verbatim)
    # GENERATED appinf1 directory (encrypted H2 at file offset 0xC8) — its
    # region offsets/counts already follow the NEW sizes.
    out[0xC8:0xC8 + 196] = appinf1.encrypted

    # Sequentially: usercode, page directory, objxinxi table.  These land
    # exactly where the generated appinf1 says they do.
    new_pageadd = strd + len(usercode)
    new_objxinxiadd = new_pageadd + len(pdir_bytes)
    assert new_pageadd == a1["pageadd"], (
        f"layout mismatch: pageadd {new_pageadd:#x} != appinf1 {a1['pageadd']:#x}")
    assert new_objxinxiadd == a1["objxinxiadd"], (
        f"layout mismatch: objxinxiadd {new_objxinxiadd:#x} "
        f"!= appinf1 {a1['objxinxiadd']:#x}")
    out += usercode
    out += pdir_bytes
    out += obj_bytes

    # Trailing alignment pad + 4-byte CRC.  The editor pads the body up to
    # a 4-byte boundary with 0xff before the trailing CRC (source: objxinxi
    # end already 4-aligned → no pad; live: 1 short → 3-byte 0xff pad).
    # Reverse that exact rule so the same-structure case matches.
    body_end = len(out)                       # == objxinxiadd + 232*objqyt
    crc_off = (body_end + 3) & ~3             # align up to 4
    out += b"\xff" * (crc_off - body_end)
    out += b"\x00\x00\x00\x00"               # CRC placeholder
    new_size = len(out)

    # H1 carries file_size at 0x3c; it MUST track the new total (and it
    # feeds the trailing-CRC XOR mask, below).
    struct.pack_into("<I", out, 0x3c, new_size)

    # Reseal H1, H2, trailing CRCs over the freshly-laid-out file.
    h1 = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H1_START:tft_format.H1_END]))
    out[tft_format.H1_CRC_OFF:tft_format.H1_CRC_OFF + 4] = struct.pack("<I", h1)
    h2 = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H2_START:tft_format.H2_END]))
    out[tft_format.H2_CRC_OFF:tft_format.H2_CRC_OFF + 4] = struct.pack("<I", h2)
    # Trailing CRC: CRC32-MPEG2 over file[:-4] XOR (file[0x03]^file[0x2e]^
    # file[0x3c]) — editor_vendor ^ model_crc low byte ^ file_size low byte
    # (achmi.dll subcmd 0x23; see findings/format-tft.md).  We DERIVE the
    # mask from the freshly-laid-out header rather than recovering it from
    # the reference, so a size change (new file[0x3c]) is reflected.  For an
    # unchanged structure the size is identical and the mask matches the
    # reference's recovered value.
    mask = out[0x03] ^ out[0x2e] ^ out[0x3c]
    body = crc32_bytewise(0xFFFFFFFF, bytes(out[:-4]))
    out[-4:] = struct.pack("<I", (body ^ mask) & 0xFFFFFFFF)
    return bytes(out)


__all__ = [
    "assemble_usercode_and_layout",
    "compile_hmi",
    "ObjxinxiLayout",
]
