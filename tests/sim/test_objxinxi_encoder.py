"""Byte-exact objxinxi-entry generation (work-item #2).

Locks ``scripts/lib/objxinxi_encoder`` against the live project TFT.  The
encoder GENERATES the HMI-derivable fields of every objxinxi entry (lei,
id, the two header constants, the per-class type-flag, and the geometry
x/y/w/h/endx/endy).  The allocator / back-patch fields (``init_off``, the
5 event-slot offsets, ``objdatarampos``, and the 88-entry attstr slot
table) are still sourced from the TFT — generating *them* needs the
per-page media allocator (objdatarampos), the second-pass offset
back-patcher (init_off + slots, work-item #3), and the binattinf
generator (attstr table, work-item #2c).

A green run proves the entry *frame* is reconstructed byte-for-byte from
HMI geometry + a per-class flag, isolating exactly which fields remain
allocator/back-patch dependent.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.lib.tft_attrs import (  # noqa: E402
    parse_appinf1_corrected, parse_objxinxi, parse_page_directory,
    ATTSTRPIANYI_OFFSET, ATTSTRPIANYI_SLOT_COUNT,
)
from scripts.lib.h2_cipher import encrypt as h2_decrypt  # noqa: E402
from scripts.lib.objxinxi_encoder import (  # noqa: E402
    encode_objxinxi_entry, ObjxinxiInputs, type_flag, geometry,
    OBJXINXI_ENTRY_SIZE,
)
from scripts.lib.hmi_model import load_hmi_pages  # noqa: E402

H2_START, H2_END = 0xC8, 0xC8 + 196
HMI_PATH = REPO_ROOT / "source" / "nextion.hmi.HMI"
TFT_PATH = REPO_ROOT / "source" / "nextion.hmi.tft"


def _load():
    data = TFT_PATH.read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[H2_START:H2_END], model_crc))
    return data, a1


def _hmi_components_in_objxinxi_order():
    """Flatten the HMI into (lei, id, x, y, w, h) tuples in objxinxi order
    (page pseudo-component first per page, then user components)."""
    out = []
    for hp in load_hmi_pages(HMI_PATH):
        page_attrs = hp.page_attrs
        out.append((
            121, 0,
            0, 0,
            int(page_attrs.get("w") or 480),
            int(page_attrs.get("h") or 320),
        ))
        for hc in hp.components[1:]:  # skip the synthesised page pseudo
            a = hc.attrs
            out.append((
                hc.lei, hc.comp_id,
                int(a.get("x") or 0), int(a.get("y") or 0),
                int(a.get("w") or 0), int(a.get("h") or 0),
            ))
    return out


def _tft_inputs_for_entry(entry) -> ObjxinxiInputs:
    """Recover the allocator/back-patch fields of one TFT objxinxi entry."""
    slots = list(struct.unpack_from("<6I", entry.raw, 4))
    attstr = list(struct.unpack_from(
        f"<{ATTSTRPIANYI_SLOT_COUNT}H", entry.raw,
        ATTSTRPIANYI_OFFSET + 4,
    ))
    return ObjxinxiInputs(
        init_off=slots[0],
        slot_offsets=slots[1:],
        objdatarampos=entry.objdatarampos,
        attstr_bytecode_offset=entry.attstr_bytecode_offset,
        attstr_slots=attstr,
    )


def test_type_flag_matches_tft():
    """The +3 type-flag byte the encoder emits matches every entry."""
    data, a1 = _load()
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    for o in objs:
        assert type_flag(o.lei) == o.raw[3], (
            f"lei {o.lei}: flag 0x{type_flag(o.lei):02x} != 0x{o.raw[3]:02x}"
        )


def test_geometry_matches_tft():
    """Generated geometry matches the entry's x/y/w/h/endx/endy."""
    data, a1 = _load()
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi = _hmi_components_in_objxinxi_order()
    assert len(hmi) == len(objs)
    for (lei, cid, x, y, w, h), o in zip(hmi, objs):
        gx, gy, gw, gh, endx, endy = geometry(lei, x, y, w, h)
        tft_x = struct.unpack_from("<H", o.raw, 40)[0]
        tft_y = struct.unpack_from("<H", o.raw, 42)[0]
        assert (gx, gy, gw, gh, endx, endy) == (
            tft_x, tft_y, o.w, o.h, o.endx, o.endy
        ), f"lei {lei} id {cid} geometry mismatch"


def test_objxinxi_entries_byte_exact():
    """Every objxinxi entry reconstructs byte-for-byte: HMI-derived
    fields generated, allocator/back-patch fields fed from the TFT."""
    data, a1 = _load()
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi = _hmi_components_in_objxinxi_order()
    assert len(hmi) == len(objs)

    for (lei, cid, x, y, w, h), o in zip(hmi, objs):
        inputs = _tft_inputs_for_entry(o)
        got = encode_objxinxi_entry(lei, cid, x, y, w, h, inputs)
        assert len(got) == OBJXINXI_ENTRY_SIZE
        assert got == o.raw, (
            f"lei {lei} id {cid}: objxinxi entry mismatch\n"
            f"  got  {got.hex()}\n  live {o.raw.hex()}"
        )


def test_attstr_bytecode_offset_equals_init_when_slot0_present():
    """Invariant the offset back-patcher (work-item #3) relies on: when a
    component has a slot-0 init block, ``+52`` (attstr_bytecode_offset)
    equals ``init_off`` (+4).  When slot 0 is absent they diverge."""
    data, a1 = _load()
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    for o in objs:
        init_off = struct.unpack_from("<I", o.raw, 4)[0]
        if init_off != 0xFFFFFFFF:
            assert init_off == o.attstr_bytecode_offset, (
                f"lei {o.lei} id {o.id}: slot-0 present but +52 "
                f"{o.attstr_bytecode_offset:#x} != init_off {init_off:#x}"
            )


def test_whole_objxinxi_table_byte_exact():
    """The concatenated objxinxi table reconstructs byte-for-byte."""
    data, a1 = _load()
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi = _hmi_components_in_objxinxi_order()

    out = b"".join(
        encode_objxinxi_entry(lei, cid, x, y, w, h, _tft_inputs_for_entry(o))
        for (lei, cid, x, y, w, h), o in zip(hmi, objs)
    )
    live = data[a1["objxinxiadd"]:a1["objxinxiadd"] + len(objs) * OBJXINXI_ENTRY_SIZE]
    assert out == live, "objxinxi table mismatch"
