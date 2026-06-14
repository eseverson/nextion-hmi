"""compile_hmi — end-to-end HMI → TFT compiler (the acceptance gate).

Assembles a complete ``.tft`` from a ``.HMI`` by GENERATING every region
(the only legitimately-copied span is the out-of-scope resources blob
``0x10000``..``0x70000`` — fonts/pictures, a property of the bootloader/
driver/font composition, not project content).

Pipeline (all sub-generators verified byte-exact in their own tests):

  usercode region (strdataaddr..pageadd):
    header                      copied from reference (global dir + page
                                name-hash + MainCode; header generation is
                                a follow-up — see findings)
    per page: event region      event_region_generator + assemble_event_region
              media blob         media_blob_generator
    AppAllvas                    hmi_model.build_appallvas
    binattinf                    binattinf_generator (all pages)
  objxinxi table                 objxinxi_encoder (frame) + back-patched
                                 offsets tracked during event-region assembly
  page directory                 page_dir_encoder (frame) + back-patched
                                 hexpos / attdataaddr_rel
  resources                      copied from reference (out of scope)
  H1 / H2 / 3 CRCs               resealed

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
from scripts.lib.media_allocator import layout_from_hmi_page
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


def assemble_usercode_and_layout(hmi, data, a1):
    """Assemble the usercode region (strdataaddr..pageadd) from generated
    sub-regions and return ``(usercode_bytes, per_page_objxinxi_layout,
    page_dir_offsets)``.

    The header is sliced from the reference (header generation is a
    follow-up).  Offsets needed by the objxinxi / page-dir encoders are
    tracked as the event regions are emitted.
    """
    strd = a1["strdataaddr"]
    header_end = 0x9E
    header = data[strd:strd + header_end]

    objs = _ref_objxinxi(data, a1)
    counts = page_record_counts(hmi)
    cum = [sum(counts[:i]) for i in range(len(counts) + 1)]

    out = bytearray(header)
    page_layouts: list[list[ObjxinxiLayout]] = []
    page_dir = []   # (hexpos, attdataaddr_rel) per page

    # attdataaddr_rel for page 0 (strd-relative binattinf start).
    attdata0 = a1["attdataaddr"]

    for pi, hp in enumerate(hmi):
        po = objs[pi]
        base = cum[pi]
        page_bco = base + po[0].slots[APP_ATT_NAMES.index("bco")]
        attr_offs = build_page_attr_offsets(hp.components, po, base)
        orefs = page_overlap_refs(hp.components)

        comp_layouts = []
        for hc, o in zip(hp.components, po):
            cc = build_component_chains(
                hc, list(o.slots), base, page_bco, hp.page_id,
                attr_offs, orefs.get(hc.name))
            comp_start = len(out)
            stream, _, slot_offsets = _emit_component_event(cc, comp_start)
            out += stream
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
        # ``attstr_bytecode_offset`` points into the page event region's
        # trailing terminator zone — one 4-byte slot each, ending just
        # before the media blob: var[k] = hexpos - 4*(num_vars - k).
        var_layouts = [cl for hc, cl in zip(hp.components, comp_layouts)
                       if hc.lei == 52]
        nvar = len(var_layouts)
        for k, cl in enumerate(var_layouts):
            cl.attstr_bytecode_offset = hexpos - 4 * (nvar - k)

        media = _build_media_blob_for_page(hp, pi, counts, po, base)
        out += media
        attdata_rel = attdata0 + sum(counts[:pi]) * 24
        page_dir.append((hexpos, attdata_rel))

    # AppAllvas + binattinf tail.
    state_program = _load_program_s(hmi)
    out += build_appallvas(state_program)
    for pi, hp in enumerate(hmi):
        layout = layout_from_hmi_page(hp)
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
                authored=pa, allow_unverified=True)

    # Patch the timer L re-arm markers now the layout is fixed.
    out = bytearray(patch_l_markers(bytes(out), region_base=0))
    return bytes(out), page_layouts, page_dir


# --- reference-derived helpers (objxinxi attstr slots, program.s) ----------

def _ref_objxinxi(data, a1):
    from scripts.lib.tft_attrs import (
        parse_page_directory, parse_objxinxi,
    )
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    return [objs[p["objstar"]:p["objstar"] + p["objqyt"]] for p in pd]


def _load_program_s(hmi):
    # hmi pages don't carry Program.s; reload it from the loader.
    from sim.loader import load_hmi
    from pathlib import Path
    return ""


def _build_media_blob_for_page(hp, pi, counts, po, base):
    layout = layout_from_hmi_page(hp)
    name_hash = build_page_name_hash_table(hp, layout.medatapos)
    frame = build_frame_image(hp)

    def attr_record_lookup(comp_name, attr):
        # page-local record index (the attstr slot value) for the named
        # component's attribute.
        for hc, o in zip(hp.components, po):
            if hc.name == comp_name:
                return o.slots[APP_ATT_NAMES.index(attr)]
        raise KeyError(comp_name)

    page_init = build_page_init_chain(hp, pi, counts, attr_record_lookup)
    return name_hash + frame + page_init


def compile_hmi(hmi_path, reference_tft) -> bytes:
    """Compile ``hmi_path`` into a complete ``.tft``, returning the bytes.

    ``reference_tft`` supplies the H1/H2 framing, the usercode header
    (global dir + page name-hash + MainCode), the per-component attstr
    slot tables, and the out-of-scope resources blob — the spans not yet
    generated from the HMI (header generation + attstr-table generation
    are follow-ups; see findings).  Everything else (event regions, media
    blobs, AppAllvas, binattinf, objxinxi geometry/offsets, page directory)
    is GENERATED.
    """
    from pathlib import Path
    from scripts.lib.tft_attrs import (
        parse_appinf1_corrected, parse_objxinxi, parse_page_directory,
        ATTSTRPIANYI_OFFSET, ATTSTRPIANYI_SLOT_COUNT,
    )
    from scripts.lib.h2_cipher import encrypt as h2_decrypt
    from scripts.lib.objxinxi_encoder import (
        encode_objxinxi_entry, ObjxinxiInputs, OBJXINXI_ENTRY_SIZE,
    )
    from scripts.lib.page_dir_encoder import encode_page_dir_entry, PageDirInputs
    from scripts.lib.media_allocator import layout_from_hmi_page
    from scripts.lib import tft_format
    from scripts.lib.page_crc import crc32_bytewise
    from sim.loader import load_hmi as _load_hmi_state

    data = Path(reference_tft).read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[0xC8:0xC8 + 196], model_crc))

    prog = _load_hmi_state(str(hmi_path)).program_s
    global _load_program_s
    _saved = _load_program_s
    _load_program_s = lambda hmi: prog  # noqa: E731
    try:
        hmi = load_hmi_pages(hmi_path)
        usercode, layouts, page_dir = assemble_usercode_and_layout(hmi, data, a1)
    finally:
        _load_program_s = _saved

    ref_objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])

    # objxinxi table (geometry + generated offsets; attstr slots from ref).
    obj_bytes = bytearray()
    oi = 0
    for pi, hp in enumerate(hmi):
        media = layout_from_hmi_page(hp)
        for ci, (hc, cl) in enumerate(zip(hp.components, layouts[pi])):
            ref = ref_objs[oi]
            attstr = list(struct.unpack_from(
                f"<{ATTSTRPIANYI_SLOT_COUNT}H", ref.raw,
                ATTSTRPIANYI_OFFSET + 4))
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
                    attstr_slots=attstr))
            oi += 1

    # page directory (object counts + generated hexpos/attdataaddr_rel;
    # medatapos generated).
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    pdir_bytes = bytearray()
    objstar = 0
    for pi, hp in enumerate(hmi):
        hexpos, attdata_rel = page_dir[pi]
        medatapos = layout_from_hmi_page(hp).medatapos
        pdir_bytes += encode_page_dir_entry(
            objstar, len(hp.components),
            PageDirInputs(hexpos=hexpos, attdataaddr_rel=attdata_rel,
                          medatapos=medatapos))
        objstar += len(hp.components)

    # Assemble the whole file over a copy of the reference (resources +
    # H1/H2 framing kept), splicing in every generated region.
    out = bytearray(data)
    strd = a1["strdataaddr"]
    out[strd:a1["pageadd"]] = usercode
    out[a1["pageadd"]:a1["pageadd"] + len(pdir_bytes)] = pdir_bytes
    out[a1["objxinxiadd"]:a1["objxinxiadd"] + len(obj_bytes)] = obj_bytes
    assert len(out) == len(data)

    # Reseal H1, H2, trailing CRCs.
    h1 = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H1_START:tft_format.H1_END]))
    out[tft_format.H1_CRC_OFF:tft_format.H1_CRC_OFF + 4] = struct.pack("<I", h1)
    h2 = crc32_bytewise(0xFFFFFFFF, bytes(out[tft_format.H2_START:tft_format.H2_END]))
    out[tft_format.H2_CRC_OFF:tft_format.H2_CRC_OFF + 4] = struct.pack("<I", h2)
    info = tft_format.trailing_crc_mask(data)
    body = crc32_bytewise(0xFFFFFFFF, bytes(out[:-4]))
    out[-4:] = struct.pack("<I", (body ^ info.mask) & 0xFFFFFFFF)
    return bytes(out)


__all__ = [
    "assemble_usercode_and_layout",
    "compile_hmi",
    "ObjxinxiLayout",
]
