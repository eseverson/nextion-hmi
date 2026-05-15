"""tft_attrs_encoder — encoder for per-component attribute records.

Produces the two binary structures written during TFT compilation:

1. **allattbytes**: concatenation of 24-byte ``binattinf`` records,
   one per attribute per component, stored at
   ``strdata + pagexinxi.attdataaddr``.

2. **Attstrpianyi** (180 bytes, F-series): per-component back-reference
   block with a 4-byte init-bytecode offset followed by 88 u16 slots,
   one per position in the global attribute-name table
   (``xiliexinxitype.AppAttNames``).

See ``nextion/findings/attribute-records.md`` for the full derivation.
Verified against ``tests/editor outputs/17_more_components/17.tft``.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Global attribute-name table
# ---------------------------------------------------------------------------

# AppAttNames: the ordered string array from ``xiliexinxitype.AppAttNames``
# (lines ~215669-216001 of /tmp/hmitype_all.il, method Attmake::attinit_T0).
# Index == the AppAttNames slot number; that slot number is also the .id
# field in attstr32/attstr64, and the multiplier for addressing Attstrpianyi:
#   Attstrpianyi[slot * 2 + 4]  ← u16 record index for attribute "name"
APP_ATT_NAMES: list[str] = [
    # 0..9
    "lei", "vscope", "sta", "ch", "bco", "picc", "pic", "gdc", "gdw", "gdh",
    # 10..19
    "pco0", "pco1", "pco2", "pco3", "mode", "psta", "pco", "pic2", "wid", "hig",
    # 20..29
    "val", "maxval", "minval", "picc2", "bco2", "font", "xcen", "ycen", "txt", "txt_maxl",
    # 30..39
    "dez", "bpic", "ppic", "tim", "en", "picc0", "picc1", "bco0", "bco1", "pic0",
    # 40..49
    "pic1", "lenth", "x", "y", "endx", "endy", "w", "h", "dir", "id",
    # 50..59
    "vvs0", "vvs1", "vvs2", "vvs3", "isbr", "dis", "spax", "spay", "pw", "style",
    # 60..69
    "borderc", "borderw", "type", "key", "format", "vid", "qty", "fps", "loop", "drag",
    # 70..79
    "aph", "effect", "stim", "first", "up", "down", "left", "right", "time", "disup",
    # 80..81
    "insert", "clear",
]

# ATTSTR32_TABLE: attstr32 array index for short attribute names (≤4 chars).
# Extracted from ``xiliexinxitype.attstr32`` initialization in
# ``Attmake::attinit_T0`` (IL lines 216007-217670).
#
# NOTE: The VALUE here is the attstr32 ARRAY index (0..63), NOT the
# AppAttNames slot.  Attmake_GetAttindex returns the .id field, which IS
# the AppAttNames slot.  Use name_to_slot() to get the AppAttNames slot.
# This table is provided for reference / completeness; callers should use
# name_to_slot() for Attstrpianyi addressing.
ATTSTR32_TABLE: dict[str, int] = {
    # name: attstr32_array_idx   (.id / AppAttNames_slot)
    "h":        0,   # → AppAttNames[47]
    "w":        1,   # → AppAttNames[46]
    "x":        2,   # → AppAttNames[42]
    "y":        3,   # → AppAttNames[43]
    "id":       4,   # → AppAttNames[49]
    "ch":       5,   # → AppAttNames[3]
    "en":       6,   # → AppAttNames[34]
    "up":       7,   # → AppAttNames[74]
    "pw":       8,   # → AppAttNames[58]
    "sta":      9,   # → AppAttNames[2]
    "gdc":     10,   # → AppAttNames[7]
    "pic":     11,   # → AppAttNames[6]
    "vid":     12,   # → AppAttNames[65]
    "wid":     13,   # → AppAttNames[18]
    "hig":     14,   # → AppAttNames[19]
    "gdh":     15,   # → AppAttNames[9]
    "aph":     16,   # → AppAttNames[70]
    "lei":     17,   # → AppAttNames[0]
    "val":     18,   # → AppAttNames[20]
    "tim":     19,   # → AppAttNames[33]
    "bco":     20,   # → AppAttNames[4]
    "pco":     21,   # → AppAttNames[16]
    "dir":     22,   # → AppAttNames[48]
    "dis":     23,   # → AppAttNames[55]
    "fps":     24,   # → AppAttNames[67]
    "txt":     25,   # → AppAttNames[28]
    "gdw":     26,   # → AppAttNames[8]
    "key":     27,   # → AppAttNames[63]
    "qty":     28,   # → AppAttNames[66]
    "dez":     29,   # → AppAttNames[30]
    "pic0":    30,   # → AppAttNames[39]
    "bco0":    31,   # → AppAttNames[37]
    "pco0":    32,   # → AppAttNames[10]
    "vvs0":    33,   # → AppAttNames[50]
    "pic1":    34,   # → AppAttNames[40]
    "bco1":    35,   # → AppAttNames[38]
    "pco1":    36,   # → AppAttNames[11]
    "vvs1":    37,   # → AppAttNames[51]
    "pic2":    38,   # → AppAttNames[17]
    "bco2":    39,   # → AppAttNames[24]
    "pco2":    40,   # → AppAttNames[12]
    "vvs2":    41,   # → AppAttNames[52]
    "pco3":    42,   # → AppAttNames[13]
    "vvs3":    43,   # → AppAttNames[53]
    "psta":    44,   # → AppAttNames[15]
    "picc":    45,   # → AppAttNames[5]
    "bpic":    46,   # → AppAttNames[31]
    "ppic":    47,   # → AppAttNames[32]
    "mode":    48,   # → AppAttNames[14]
    "time":    49,   # → AppAttNames[78]
    "type":    50,   # → AppAttNames[62]
    "drag":    51,   # → AppAttNames[69]
    "stim":    52,   # → AppAttNames[72]
    "xcen":    53,   # → AppAttNames[26]
    "ycen":    54,   # → AppAttNames[27]
    "down":    55,   # → AppAttNames[75]
    "loop":    56,   # → AppAttNames[68]
    "isbr":    57,   # → AppAttNames[54]
    "left":    58,   # → AppAttNames[76]
    "font":    59,   # → AppAttNames[25]
    "spax":    60,   # → AppAttNames[56]
    "endx":    61,   # → AppAttNames[44]
    "spay":    62,   # → AppAttNames[57]
    "endy":    63,   # → AppAttNames[45]
}

# attstr64 entries: long names (5-8 chars) stored in the separate attstr64
# array (16 elements, array indices 0..15).  The .id field of each entry is
# the AppAttNames slot (same meaning as attstr32).
# Extracted from IL lines 217670-218122.
# Mapping: name → attstr64 array index (0-based).
# Use name_to_slot() to get the AppAttNames slot (= Attstrpianyi multiplier).
ATTSTR64_TABLE: dict[str, int] = {
    "picc0":    0,    # .id=0x23=35  (AppAttNames[35])
    "picc1":    1,    # .id=0x24=36  (AppAttNames[36])
    "picc2":    2,    # .id=0x17=23  (AppAttNames[23])
    "style":    3,    # .id=0x3b=59  (AppAttNames[59])
    "lenth":    4,    # .id=0x29=41  (AppAttNames[41])
    "disup":    5,    # .id=0x4f=79  (AppAttNames[79])
    "right":    6,    # .id=0x4d=77  (AppAttNames[77])
    "first":    7,    # .id=0x49=73  (AppAttNames[73])
    "vscope":   8,    # .id=0x01=1   (AppAttNames[1])
    "minval":   9,    # .id=0x16=22  (AppAttNames[22])
    "maxval":  10,    # .id=0x15=21  (AppAttNames[21])
    "format":  11,    # .id=0x40=64  (AppAttNames[64])
    "effect":  12,    # .id=0x47=71  (AppAttNames[71])
    "borderc": 13,    # .id=0x3c=60  (AppAttNames[60])
    "borderw": 14,    # .id=0x3d=61  (AppAttNames[61])
    "txt_maxl":15,    # .id=0x1d=29  (AppAttNames[29])
}

# Combined name → AppAttNames slot lookup.
# Attmake_GetAttindex returns .id (the AppAttNames index), which is the
# slot multiplier for Attstrpianyi.  For attstr32 entries the array index
# in attstr32 (0..63) is NOT the AppAttNames slot.
def name_to_slot(name: str) -> int:
    """Return the AppAttNames slot index for attribute *name*.

    This is the value returned by ``Attmake_GetAttindex`` at runtime.
    Returns the slot used to address Attstrpianyi:
        Attstrpianyi[slot * 2 + 4]  ← u16 record index

    Raises KeyError if the name is not in AppAttNames.
    """
    # Build reverse lookup from AppAttNames (authoritative ordering)
    slot = _APP_ATT_NAMES_INDEX.get(name)
    if slot is None:
        raise KeyError(f"Attribute name {name!r} not found in AppAttNames")
    return slot


# Pre-built reverse-lookup: APP_ATT_NAMES[slot] → slot
_APP_ATT_NAMES_INDEX: dict[str, int] = {n: i for i, n in enumerate(APP_ATT_NAMES)}


# ---------------------------------------------------------------------------
# Attribute type catalog
# ---------------------------------------------------------------------------

# (typevalue full byte, datafenpei bytes).
# From hmitype.dll!attshulei::.cctor — see tft_attrs.py for comments.
ATTSHULEI: dict[int, tuple[str, int]] = {
    0x12: ("Color",         2),
    0x22: ("Picid",         2),
    0x31: ("Fontid",        1),
    0x42: ("Strlenth",      2),
    0x51: ("Select",        1),
    0x61: ("Type",          1),
    0x71: ("key",           1),
    0x82: ("Videoid",       2),
    0x92: ("Gmovid",        2),
    0xa2: ("Audioid",       2),
    0xa1: ("Pageid",        1),
    0xb2: ("Hex16",         2),
    0x01: ("UU8",           1),
    0x02: ("UU16",          2),
    0x03: ("UU32",          4),
    0x07: ("UU8_L",         1),
    0x08: ("SS16",          2),
    0x09: ("SS32",          4),
    0x19: ("binary",        4),
    0x0b: ("x",             2),
    0x0c: ("y",             2),
    0x0d: ("w",             2),
    0x0e: ("h",             2),
    0x0f: ("Sstr",          4),
    0xfe: ("BinyiANYTYPE",  4),
}

# Reverse: type name → (typevalue, datafenpei)
ATTSHULEI_BY_NAME: dict[str, tuple[int, int]] = {
    name: (tv, df) for tv, (name, df) in ATTSHULEI.items()
}


# ---------------------------------------------------------------------------
# AttRecord — one attribute declaration for one component
# ---------------------------------------------------------------------------

@dataclass
class AttRecord:
    """One attribute declaration for one component.

    Mirrors the fields consumed by ``Attmake::attinfUpToBin``.

    Attributes:
        name:           Attribute name (e.g. "val", "bco").  Must be in
                        AppAttNames.
        attmemorypos:   Value (inline numeric) or heap pointer.  For most
                        numeric types this IS the attribute value.
        num_maxval:     Upper bound for the attribute.
        num_minval:     Lower bound (0 for resource-id types; set
                        automatically for Picid/Fontid/Videoid/Gmovid/
                        Audioid by the encoder if you pass ``num_maxval``
                        as the resource count - 1).
        objdatarampos:  Byte offset of the owning component's
                        ``objdata_Ram`` within the page's media blob.
        frompageid:     Page index of the owning component.
        fromobjid:      Object index of the owning component on its page.
        str_encodeh_star: Low byte of ``mobj.objdataram.memorypos``
                        (= ``objdatarampos & 0xFF`` for simple components).
        att_changeid:   From ``Upatt0.attchangeid``; 0 for most attrs.
        typevalue:      Full typevalue byte from ATTSHULEI (hi nibble = kind,
                        lo nibble = storage form).
        datafenpei:     Byte stride of the type (from ATTSHULEI).
        change:         True if the attribute can change at runtime
                        (``objchangetype.yes``).  Head attrs (id, type, x,
                        y, endx, endy, w, h) use change=False.
        datafrom:       True if the attribute has a RAM-backing position
                        (``attposup > -1`` or ``attposup == -2``).
        ispv:           True if this attribute is "page-volatile" (resets
                        on page change).
        pp:             True if this attribute is "permanent" (survives
                        page transitions).
    """
    name: str
    attmemorypos: int
    num_maxval: int
    num_minval: int
    objdatarampos: int
    frompageid: int
    fromobjid: int
    str_encodeh_star: int
    att_changeid: int
    typevalue: int          # full byte; hi nibble = kind, lo nibble = storage
    datafenpei: int         # byte stride
    change: bool = True
    datafrom: bool = True
    ispv: bool = True
    pp: bool = True

    @classmethod
    def from_type_name(
        cls,
        name: str,
        type_name: str,
        attmemorypos: int,
        num_maxval: int,
        num_minval: int,
        objdatarampos: int,
        frompageid: int,
        fromobjid: int,
        str_encodeh_star: int = 0,
        att_changeid: int = 0,
        change: bool = True,
        datafrom: bool = True,
        ispv: bool = True,
        pp: bool = True,
    ) -> "AttRecord":
        """Convenience constructor: look up typevalue/datafenpei by type name."""
        tv, df = ATTSHULEI_BY_NAME[type_name]
        return cls(
            name=name,
            attmemorypos=attmemorypos,
            num_maxval=num_maxval,
            num_minval=num_minval,
            objdatarampos=objdatarampos,
            frompageid=frompageid,
            fromobjid=fromobjid,
            str_encodeh_star=str_encodeh_star,
            att_changeid=att_changeid,
            typevalue=tv,
            datafenpei=df,
            change=change,
            datafrom=datafrom,
            ispv=ispv,
            pp=pp,
        )


# ---------------------------------------------------------------------------
# binattinf encoder
# ---------------------------------------------------------------------------

BINATTINF_SIZE = 24
ATTSTRPIANYI_SIZE = 180
ATTSTRPIANYI_SLOTS = (ATTSTRPIANYI_SIZE - 4) // 2  # = 88


def encode_binattinf(rec: AttRecord) -> bytes:
    """Produce 24 bytes of binattinf for one attribute record.

    Wire layout (little-endian):
        +0  u32  objdatarampos
        +4  s32  attmemorypos
        +8  s32  num_maxval
        +12 s32  num_minval
        +16 u8   frompageid
        +17 u8   fromobjid
        +18 u8   str_encodeh_star
        +19 u8   att_changeid
        +20 u32  packed (attlei & 0xF | ~change<<4 | datafrom<<5 |
                         ~ispv<<6 | ~pp<<7 | merrylenth*2<<8)

    The packed word is built in ``Attmake::attinfUpToBin`` by:
        V = merrylenth          # = datafenpei (byte stride)
        V <<= 1
        if not pp:  V += 1      # ~pp
        V <<= 1
        if not ispv: V += 1     # ~ispv
        V <<= 1
        if datafrom: V += 1     # datafrom
        V <<= 1
        if not change: V += 1   # ~change
        V <<= 4
        V += (typevalue & 0xF)  # attlei (low nibble of typevalue)
    """
    merrylenth = rec.datafenpei

    # Build packed word following the IL bit-packing sequence exactly.
    v = merrylenth
    v <<= 1
    if not rec.pp:
        v += 1      # ~pp
    v <<= 1
    if not rec.ispv:
        v += 1      # ~ispv
    v <<= 1
    if rec.datafrom:
        v += 1      # datafrom
    v <<= 1
    if not rec.change:
        v += 1      # ~change
    v <<= 4
    v += (rec.typevalue & 0xF)  # attlei

    return struct.pack(
        "<IiiiBBBBI",
        rec.objdatarampos & 0xFFFFFFFF,
        rec.attmemorypos,
        rec.num_maxval,
        rec.num_minval,
        rec.frompageid & 0xFF,
        rec.fromobjid & 0xFF,
        rec.str_encodeh_star & 0xFF,
        rec.att_changeid & 0xFF,
        v & 0xFFFFFFFF,
    )


def build_allattbytes(records: list[AttRecord]) -> bytes:
    """Concatenate 24-byte binattinf records for a whole page.

    Returns ``len(records) * 24`` bytes, ready to append to strdata at
    ``pagexinxi.attdataaddr``.
    """
    return b"".join(encode_binattinf(r) for r in records)


# ---------------------------------------------------------------------------
# Attstrpianyi encoder
# ---------------------------------------------------------------------------

def build_attstrpianyi(
    component_records: list[AttRecord],
    page_record_base: int,
    bytecode_offset: int = 0,
) -> bytes:
    """Build 180-byte Attstrpianyi for one component.

    The Attstrpianyi block is the per-component lookup table that maps
    each global attribute slot (from AppAttNames) to a u16 record index
    in the page-wide allattbytes table.  ``mobj.attpianyiset`` writes:

        Attstrpianyi[slot * 2 + 4] = u16(record_index_in_page_table)

    where ``slot = Attmake_GetAttindex(name)`` = the AppAttNames index.

    Args:
        component_records:  Attribute records for THIS component only,
                            in the order they appear in the page-wide
                            allattbytes table.
        page_record_base:   Index of the first of this component's records
                            in the page-wide table (i.e. the u16 value
                            written into the slot for component_records[0]).
        bytecode_offset:    Byte offset of this component's init bytecode
                            within strdata.  0 if unknown / placeholder;
                            must be patched in later.

    Returns:
        180 bytes: u32 bytecode_offset | 88 × u16 record_indexes.
        Slots not referenced by any of component_records remain 0x0000.
    """
    out = bytearray(ATTSTRPIANYI_SIZE)
    # First 4 bytes: init-bytecode offset
    struct.pack_into("<I", out, 0, bytecode_offset & 0xFFFFFFFF)

    for i, rec in enumerate(component_records):
        slot = name_to_slot(rec.name)
        record_idx = page_record_base + i
        if slot < ATTSTRPIANYI_SLOTS:
            struct.pack_into("<H", out, 4 + slot * 2, record_idx & 0xFFFF)

    return bytes(out)


# ---------------------------------------------------------------------------
# Self-test against fixture
# ---------------------------------------------------------------------------

def _self_test() -> None:
    """Verify encode_binattinf against the 17_more_components/17.tft fixture.

    Reads each 24-byte record from the fixture using tft_attrs.py, then
    re-encodes it and checks byte-for-byte equality.  This validates the
    packed-word formula and the field layout.
    """
    import pathlib
    import sys

    scripts_dir = pathlib.Path(__file__).resolve().parents[1]
    fixture = (
        scripts_dir.parent
        / "tests"
        / "editor outputs"
        / "_old"
        / "17_more_components"
        / "17.tft"
    )
    if not fixture.exists():
        print(f"SKIP: fixture not found at {fixture}")
        return

    sys.path.insert(0, str(scripts_dir))
    from tft_attrs import extract_all_attrs

    data = fixture.read_bytes()
    result = extract_all_attrs(data)

    errors = 0
    total = 0

    for pg in result["pages"]:
        for r in pg["records"]:
            total += 1
            # Reconstruct an AttRecord from the decoded BinAttRecord fields.
            # We reverse-engineer typevalue from attlei (lo nibble only) and
            # datafenpei from merrylenth.  For types where multiple typevalues
            # share the same low nibble, the exact high nibble is lost in the
            # binary record (that's by design — we use the lo nibble only).
            # We reconstruct with typevalue = attlei (lo nibble only, high=0),
            # which gives the same lo nibble, so the packed word will match.
            attlei = r.attlei
            datafenpei = r.merrylenth  # merrylenth = datafenpei in the binary

            # Reconstruct change/datafrom/ispv/pp from flags
            change = r.flag_change
            datafrom = r.flag_datafrom
            ispv = r.flag_ispv
            pp = r.flag_pp

            rec = AttRecord(
                name="",               # not needed for encode_binattinf
                attmemorypos=r.attmemorypos,
                num_maxval=r.num_maxval,
                num_minval=r.num_minval,
                objdatarampos=r.objdatarampos,
                frompageid=r.frompageid,
                fromobjid=r.fromobjid,
                str_encodeh_star=r.str_encodeh_star,
                att_changeid=r.att_changeid,
                typevalue=attlei,      # use lo nibble as full typevalue
                datafenpei=datafenpei,
                change=change,
                datafrom=datafrom,
                ispv=ispv,
                pp=pp,
            )

            encoded = encode_binattinf(rec)
            if encoded != r.raw:
                errors += 1
                if errors <= 5:
                    print(f"FAIL page={pg['page_idx']} rec={r.index}")
                    print(f"  expected: {r.raw.hex()}")
                    print(f"  got:      {encoded.hex()}")

    if errors == 0:
        print(f"PASS: {total} records re-encoded correctly")
    else:
        print(f"FAIL: {errors}/{total} records did not match")
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
