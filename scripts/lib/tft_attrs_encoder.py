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
# Per-component head fields
# ---------------------------------------------------------------------------
#
# Per ``findings/attribute-records.md``, ``mpage.refallatt()`` emits 8 head
# records per component (id, type, x, y, endx, endy, w, h) before the
# component's ``GetAtts_WithNoHead`` attrs. Each head field has a fixed
# (typevalue, datafenpei) and a fixed min/max value bound.
#
# The bounds were read off the ``17_more_components/17.tft`` fixture
# (every component's head records share identical max/min for these names).
# The numeric coordinate bounds (±6000 for x/y, ±2000 for w/h, ±32767 for
# endx/endy, 0..255 for id/type) come straight from the editor's compile
# pipeline, not from any per-display configuration.

HEAD_FIELDS: list[tuple[str, str, int, int]] = [
    # (name, type_name, num_minval, num_maxval)
    ("id",   "UU8", 0,      255),
    ("type", "UU8", 0,      255),
    ("x",    "x",   -6000,  6000),
    ("y",    "y",   -6000,  6000),
    ("endx", "SS16", -32768, 32767),
    ("endy", "SS16", -32768, 32767),
    ("w",    "w",   -2000,  2000),
    ("h",    "h",   -2000,  2000),
]


def _resolve_value(type_name: str, value, allocator: "LongAttrAllocator | None" = None,
                   *, attr_name: str = ""):
    """Pack ``value`` into the integer ``attmemorypos`` slot for ``type_name``.

    Numeric types pass through (signed → struct will handle); ``Sstr``
    accepts a bytes/str:

    * Length ≤ 4: stored inline in ``attmemorypos`` (little-endian packed).
    * Length > 4: requires ``allocator``. Returns a sentinel int that
      the caller (``build_component_records``) recognises as "patch
      this record later" — the allocator does the actual allocation in
      a single pass once every component's records have been built (so
      the cursor matches the editor's depth-first refallatt order; see
      ``findings/memory-allocation.md``).
    """
    if type_name == "Sstr":
        if value is None or value == 0:
            return 0
        if isinstance(value, str):
            value = value.encode("latin-1")
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError(
                f"Sstr value must be bytes/str, got {type(value).__name__}"
            )
        if len(value) <= 4:
            return int.from_bytes(bytes(value).ljust(4, b"\x00"),
                                  "little", signed=False)
        if allocator is None:
            raise ValueError(
                f"Sstr value {value!r} is {len(value)} bytes; pass a "
                f"`LongAttrAllocator` to `build_component_records` to "
                f"enable long-string allocation."
            )
        # Queue a long-string allocation. The allocator returns a
        # placeholder; the caller patches the record after the page's
        # records are emitted.
        return allocator.queue_sstr(attr_name, bytes(value))
    if value is None:
        return 0
    return int(value)


# ---------------------------------------------------------------------------
# Variable-length attribute allocator
# ---------------------------------------------------------------------------
#
# Strings longer than 4 bytes, curve buffers (``molloc``) and binary
# blobs (``binary``) don't fit inline in a binattinf's 4-byte
# ``attmemorypos`` slot. The editor allocates space for them in a
# separate memory region (per-page private memory, for the F-series
# path) and stores the **byte offset within that region** in
# ``attmemorypos``.  The companion ``<sstrname>_maxl`` Strlenth
# attribute's ``attmemorypos`` holds the allocated capacity (in
# bytes, including the NUL terminator for Sstr).
#
# See findings/memory-allocation.md for the editor's two-pass layout
# inside ``appbianyi.StructHtoL``.

# Sentinel placed in AttRecord.attmemorypos for queued allocations.
# Replaced by the allocator's pass-2 ``finalize`` step.
_PENDING_ALLOC_SENTINEL = -1


@dataclass
class _PendingAlloc:
    """One queued variable-length allocation."""
    kind: str              # "Sstr" | "molloc" | "binary"
    payload: bytes         # init bytes (Sstr only) or b"" for dynamic
    capacity: int          # bytes to reserve; ``len(payload)`` for Sstr
    attr_name: str         # for diagnostics
    pending_idx: int       # index into the allocator's pending list


@dataclass
class LongAttrAllocator:
    """Two-pass allocator for variable-length attribute storage.

    Mirrors ``hmitype.appbianyi.mollocmemory_add`` + the two layout
    passes in ``appbianyi.StructHtoL`` (see findings/memory-allocation.md):

      * Pass 1 — every component's records are built; each Sstr long
        string / molloc / binary attribute calls ``queue_*``, getting
        back a sentinel that is stored in the record's
        ``attmemorypos`` field (alongside the corresponding
        ``<name>_maxl`` Strlenth's ``attmemorypos`` set to the queued
        index — see ``link_companion``).
      * Pass 2 — ``finalize`` walks the queue in queue order
        (init-data first, then dynamic), assigning a region-relative
        byte offset to each entry and back-patching the records.

    The allocator does not itself track which records to patch — the
    caller is expected to call ``patch_records`` with the final
    record list once every component on the page has been processed.

    The allocator's cursor counts bytes within the per-page private
    memory region. The caller supplies the starting cursor (typically
    0 for a from-scratch page).
    """
    starting_offset: int = 0

    def __post_init__(self) -> None:
        self._pending: list[_PendingAlloc] = []
        self._finalized: bool = False
        # Records to patch: list of (record_ref, field, pending_idx,
        # write_kind) tuples. ``record_ref`` is the AttRecord; ``write_kind``
        # is one of "offset" (write decpos) or "capacity" (write mollocsize).
        self._patches: list[tuple["AttRecord", int, str]] = []
        # Total bytes consumed by init-data pass (for the "noinit
        # follows initialised region" rule).
        self.init_bytes: int = 0
        self.dynamic_bytes: int = 0

    @staticmethod
    def _round4(n: int) -> int:
        return (n + 3) & ~3

    def queue_sstr(self, attr_name: str, value: bytes) -> int:
        """Queue a long-string allocation. Returns the sentinel to
        write into the record's ``attmemorypos`` (the caller MUST
        register the record via :meth:`link_record` before
        :meth:`finalize`)."""
        # capacity = len(value) + 1 NUL, rounded up to 4 bytes.
        raw = len(value) + 1
        capacity = self._round4(raw)
        pa = _PendingAlloc(kind="Sstr", payload=value + b"\x00",
                           capacity=capacity, attr_name=attr_name,
                           pending_idx=len(self._pending))
        self._pending.append(pa)
        return pa.pending_idx

    def queue_molloc(self, attr_name: str, capacity: int) -> int:
        """Queue a dynamic ``molloc`` (curve / waveform) buffer."""
        capacity = self._round4(capacity)
        pa = _PendingAlloc(kind="molloc", payload=b"",
                           capacity=capacity, attr_name=attr_name,
                           pending_idx=len(self._pending))
        self._pending.append(pa)
        return pa.pending_idx

    def queue_binary(self, attr_name: str, capacity: int) -> int:
        """Queue a dynamic ``binary`` buffer."""
        capacity = self._round4(capacity)
        pa = _PendingAlloc(kind="binary", payload=b"",
                           capacity=capacity, attr_name=attr_name,
                           pending_idx=len(self._pending))
        self._pending.append(pa)
        return pa.pending_idx

    def link_record(self, record: "AttRecord", pending_idx: int,
                    *, write_kind: str = "offset") -> None:
        """Register a record to be patched by :meth:`finalize`.

        Args:
            record: AttRecord whose ``attmemorypos`` should be replaced.
            pending_idx: Index returned by ``queue_*``.
            write_kind: ``"offset"`` writes the assigned byte offset
                (the Sstr / molloc / binary record's own pointer).
                ``"capacity"`` writes the allocated size in bytes —
                used for the paired ``<name>_maxl`` Strlenth record.
        """
        if write_kind not in ("offset", "capacity"):
            raise ValueError(f"unknown write_kind: {write_kind!r}")
        self._patches.append((record, pending_idx, write_kind))

    def finalize(self) -> bytes:
        """Run the two-pass layout, patch all linked records, and
        return the concatenated memory-region bytes (initialised
        portion + dynamic portion zeros — the dynamic tail is left
        zero-filled because the runtime doesn't depend on its
        contents).
        """
        if self._finalized:
            raise RuntimeError("finalize() called twice")
        cursor = self.starting_offset
        offsets: list[int] = [0] * len(self._pending)
        out = bytearray()
        # Pass 1: init-data (Sstr) entries.
        for pa in self._pending:
            if pa.kind != "Sstr":
                continue
            offsets[pa.pending_idx] = cursor
            # Write the payload, pad with zeros to capacity.
            pad = pa.capacity - len(pa.payload)
            out += pa.payload + b"\x00" * pad
            cursor += pa.capacity
        # Pad cursor to 4 (already aligned because capacities are
        # rounded, but the StructHtoL pass does an explicit align here).
        while cursor & 3:
            out += b"\x00"
            cursor += 1
        self.init_bytes = cursor - self.starting_offset
        # Pass 2: dynamic (molloc, binary).
        for pa in self._pending:
            if pa.kind == "Sstr":
                continue
            offsets[pa.pending_idx] = cursor
            cursor += pa.capacity
        self.dynamic_bytes = (cursor - self.starting_offset) - self.init_bytes
        # Apply patches.
        for record, pending_idx, write_kind in self._patches:
            if write_kind == "offset":
                record.attmemorypos = offsets[pending_idx]
            else:  # "capacity"
                record.attmemorypos = self._pending[pending_idx].capacity
        self._finalized = True
        return bytes(out)

    @property
    def total_bytes(self) -> int:
        if not self._finalized:
            raise RuntimeError("finalize() first")
        return self.init_bytes + self.dynamic_bytes


def build_component_records(
    class_name: str,
    *,
    objdatarampos: int,
    frompageid: int,
    fromobjid: int,
    component_id: int,
    component_type: int,
    x: int,
    y: int,
    w: int,
    h: int,
    authored: dict | None = None,
    str_encodeh_star: int | None = None,
    resource_counts: dict[str, int] | None = None,
    allocator: "LongAttrAllocator | None" = None,
) -> list[AttRecord]:
    """Build the full ``refallatt`` record list for a single component.

    Emits 8 head records (id, type, x, y, endx, endy, w, h) followed by one
    record per attribute declared in the F-series ``GetAtts_WithNoHead`` for
    ``class_name``. The output is the per-component contribution to the
    page-wide ``allattbytes`` table, in ``refallatt`` order.

    ``authored`` provides values for declared attrs; missing attrs get value
    0 (the binattinf is still emitted — the page-wide table is dense in
    ``refallatt`` order, with absent values stored as zeros).

    The per-page record index for each output record is *not* set here; it
    is the position of the record within the assembled page-wide list,
    which the caller produces by concatenating ``build_component_records``
    outputs in object order.

    Head records use ``change=False`` (head fields are immutable at
    runtime). Declared records use ``change=True`` by default. ``datafrom``,
    ``ispv``, ``pp`` use the editor's common defaults (all True), which
    match every record in the ``17_more_components`` fixture.

    ``resource_counts`` lets the encoder auto-fill ``num_maxval`` for
    resource-id types. Expected keys (any subset is fine):

        {"Picid": picqyt, "Fontid": zimoqyt, "Videoid": videoqyt,
         "Gmovid": gmovqyt, "Audioid": audioqyt, "Pageid": pageqyt}

    For each declared attr of one of those types, if its ``__max`` isn't
    explicitly set in ``authored``, ``num_maxval`` is set to
    ``resource_counts[type_name] - 1``.

    ``allocator`` enables long-string / molloc / binary allocation. When
    an Sstr attribute has a value longer than 4 bytes, the encoder
    queues an allocation request with ``allocator`` and links both the
    Sstr record (write_kind="offset") AND its paired
    ``<sstrname>_maxl`` Strlenth record (write_kind="capacity"). The
    caller must invoke ``allocator.finalize()`` once every component on
    the page has been processed to materialise the byte offsets and
    back-patch the records.

    Without an allocator, attempting an Sstr value > 4 bytes raises
    ValueError.

    ``allocator`` also handles ``molloc`` (curve channel buffer) and
    ``binary`` attrs: when a declared attr is of type ``molloc`` or
    ``binary`` and ``authored`` provides ``<name>__capacity``, the
    encoder queues a dynamic (no-init) reservation of that many bytes.
    """
    try:
        from .tft_attrs_schemas import TYPE_SCHEMAS
    except ImportError:
        from tft_attrs_schemas import TYPE_SCHEMAS
    if class_name not in TYPE_SCHEMAS:
        raise KeyError(f"unknown component class {class_name!r}")
    schema = TYPE_SCHEMAS[class_name]
    authored = dict(authored or {})

    if str_encodeh_star is None:
        str_encodeh_star = objdatarampos & 0xFF

    endx = x + w - 1
    endy = y + h - 1
    head_values = {"id": component_id, "type": component_type,
                   "x": x, "y": y, "endx": endx, "endy": endy,
                   "w": w, "h": h}

    records: list[AttRecord] = []
    for name, type_name, lo, hi in HEAD_FIELDS:
        tv, df = ATTSHULEI_BY_NAME[type_name]
        records.append(AttRecord(
            name=name,
            attmemorypos=_resolve_value(type_name, head_values[name]),
            num_maxval=hi,
            num_minval=lo,
            objdatarampos=objdatarampos,
            frompageid=frompageid,
            fromobjid=fromobjid,
            str_encodeh_star=str_encodeh_star,
            att_changeid=0,
            typevalue=tv,
            datafenpei=df,
            change=False,
            datafrom=True,
            ispv=True,
            pp=True,
        ))

    _RESOURCE_ID_TYPES = {"Picid", "Fontid", "Videoid", "Gmovid",
                          "Audioid", "Pageid"}
    # Track Sstr alloc queue indices so we can link the paired
    # ``<name>_maxl`` Strlenth record when we encounter it later in
    # the schema.
    sstr_pending: dict[str, int] = {}  # base_name -> pending_idx
    # Track records by attr name for back-references (Strlenth pairing).
    declared_records: dict[str, AttRecord] = {}

    for attr_name, _attpos, type_name in schema:
        tv, df = ATTSHULEI_BY_NAME[type_name]
        value = authored.get(attr_name, 0)
        max_key = f"{attr_name}__max"
        if max_key in authored:
            max_val = authored[max_key]
        elif resource_counts and type_name in _RESOURCE_ID_TYPES \
                and type_name in resource_counts:
            max_val = max(0, resource_counts[type_name] - 1)
        else:
            max_val = 0
        min_val = authored.get(f"{attr_name}__min", 0)

        # Variable-length attribute handling.
        attmemorypos_value: int
        if type_name == "Sstr":
            attmemorypos_value = _resolve_value(type_name, value, allocator,
                                                attr_name=attr_name)
            # If the resolver queued an allocation, remember the
            # pending index so we can link the paired Strlenth below.
            if allocator is not None and isinstance(value, (bytes, str)):
                bytes_val = value.encode("latin-1") if isinstance(value, str) else value
                if len(bytes_val) > 4:
                    sstr_pending[attr_name] = attmemorypos_value
        elif type_name in ("binary", "BinyiANYTYPE") and allocator is not None \
                and f"{attr_name}__capacity" in authored:
            cap = int(authored[f"{attr_name}__capacity"])
            attmemorypos_value = allocator.queue_binary(attr_name, cap)
            sstr_pending[attr_name] = attmemorypos_value
        else:
            attmemorypos_value = _resolve_value(type_name, value)

        rec = AttRecord(
            name=attr_name,
            attmemorypos=attmemorypos_value,
            num_maxval=max_val,
            num_minval=min_val,
            objdatarampos=objdatarampos,
            frompageid=frompageid,
            fromobjid=fromobjid,
            str_encodeh_star=str_encodeh_star,
            att_changeid=0,
            typevalue=tv,
            datafenpei=df,
            change=True,
            datafrom=True,
            ispv=True,
            pp=True,
        )
        records.append(rec)
        declared_records[attr_name] = rec

        # If this record needs the allocator's offset, link it now.
        if attr_name in sstr_pending and type_name in ("Sstr", "binary",
                                                      "BinyiANYTYPE"):
            # Reset attmemorypos to 0 (it currently holds the pending
            # index; the allocator will write the real offset in
            # finalize()).
            rec.attmemorypos = 0
            assert allocator is not None
            allocator.link_record(rec, sstr_pending[attr_name],
                                  write_kind="offset")

        # Pair Strlenth records to their queued Sstr.
        # Convention: paired name is ``<sstrname>_maxl`` (per
        # findings/attribute-records.md). Some classes use ``_m``
        # variant (path_m in GText/Gmov) — match either.
        if type_name == "Strlenth":
            base_name = None
            for suffix in ("_maxl", "_m"):
                if attr_name.endswith(suffix):
                    candidate = attr_name[: -len(suffix)]
                    if candidate in sstr_pending:
                        base_name = candidate
                        break
            if base_name is not None and allocator is not None:
                rec.attmemorypos = 0
                allocator.link_record(rec, sstr_pending[base_name],
                                      write_kind="capacity")

    return records


# ---------------------------------------------------------------------------
# Layout-aware page-block builder
# ---------------------------------------------------------------------------

def _empty_record() -> "AttRecord":
    """Return an AttRecord whose encoded form is 24 zero bytes."""
    return AttRecord(
        name="", attmemorypos=0, num_maxval=0, num_minval=0,
        objdatarampos=0, frompageid=0, fromobjid=0,
        str_encodeh_star=0, att_changeid=0,
        typevalue=0, datafenpei=0,
        change=True, datafrom=False, ispv=True, pp=True,
    )


def build_component_block(
    lei: int,
    page_record_base: int,
    records_by_name: dict[str, "AttRecord"],
    *,
    bytecode_offset: int = 0,
) -> tuple[list["AttRecord"], bytes]:
    """Place ``records_by_name`` at their layout-defined relative offsets
    inside a stride-sized record block, with empty records filling the
    gaps. Also build the matching 180-byte Attstrpianyi block.

    Args:
        lei: Component type code (see ``tft_attrs_layout.LEI_TO_CLASS``).
        page_record_base: Absolute index of this block's first record in
            the page-wide allattbytes table. Slot values in the returned
            Attstrpianyi are ``page_record_base + relative_offset``.
        records_by_name: ``{attr_name: AttRecord}`` for every attribute
            this component should populate. Names absent from the layout
            for this lei are silently ignored.
        bytecode_offset: u32 stored at the head of Attstrpianyi
            (= component's init-bytecode offset within strdata).

    Returns:
        ``(block_records, attstrpianyi_bytes)``. ``block_records`` has
        length = layout stride for ``lei``; ``attstrpianyi_bytes`` is
        always 180 bytes.

    Raises:
        KeyError: ``lei`` is not in ``PER_LEI_LAYOUT`` (the layout table
            covers 19 of 49 classes; missing classes need fixture data).
    """
    try:
        from .tft_attrs_layout import PER_LEI_LAYOUT
    except ImportError:
        from tft_attrs_layout import PER_LEI_LAYOUT

    stride, offset_map = PER_LEI_LAYOUT[lei]
    block: list[AttRecord] = [_empty_record() for _ in range(stride)]
    placed: list[tuple[str, int]] = []
    for name, rec in records_by_name.items():
        if name not in offset_map:
            continue
        off = offset_map[name]
        block[off] = rec
        placed.append((name, page_record_base + off))

    pianyi = bytearray(ATTSTRPIANYI_SIZE)
    struct.pack_into("<I", pianyi, 0, bytecode_offset & 0xFFFFFFFF)
    # Default every slot to 0xffff (no record).
    for n in range(ATTSTRPIANYI_SLOTS):
        struct.pack_into("<H", pianyi, 4 + n * 2, 0xFFFF)
    for name, rec_index in placed:
        slot = _APP_ATT_NAMES_INDEX.get(name)
        if slot is None or slot >= ATTSTRPIANYI_SLOTS:
            continue
        struct.pack_into("<H", pianyi, 4 + slot * 2, rec_index & 0xFFFF)
    return block, bytes(pianyi)


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


def _self_test_build_component_records() -> None:
    """Smoke-test build_component_records: verify shape, head fields, and
    that encode_binattinf produces 24 bytes per record."""
    try:
        from .tft_attrs_schemas import TYPE_SCHEMAS
    except ImportError:
        from tft_attrs_schemas import TYPE_SCHEMAS

    cases = [
        ("GuiObjPage",   8 + 7),
        ("GuiObjPic",    8 + 1),
        ("GuiObjQrcode", 8 + 7),
        ("GuiObjXfloat", 8 + 18),
        ("GuiObjButton", 8 + 21),
    ]
    for cls, want_len in cases:
        recs = build_component_records(
            cls,
            objdatarampos=0x100,
            frompageid=0,
            fromobjid=1,
            component_id=42,
            component_type=59,
            x=10, y=20, w=160, h=50,
            authored={},
        )
        if len(recs) != want_len:
            raise AssertionError(
                f"{cls}: expected {want_len} records, got {len(recs)}"
            )
        # Head must come first in canonical order.
        head_names = [r.name for r in recs[:8]]
        if head_names != [n for n, *_ in HEAD_FIELDS]:
            raise AssertionError(f"{cls}: head order wrong: {head_names}")
        # endx/endy derived correctly.
        endx = next(r for r in recs if r.name == "endx").attmemorypos
        endy = next(r for r in recs if r.name == "endy").attmemorypos
        if (endx, endy) != (10 + 160 - 1, 20 + 50 - 1):
            raise AssertionError(f"{cls}: endx/endy wrong: {endx},{endy}")
        # Every record encodes to exactly 24 bytes.
        for r in recs:
            blob = encode_binattinf(r)
            if len(blob) != 24:
                raise AssertionError(f"{cls}: record {r.name} not 24 bytes")
    print(f"PASS: build_component_records over {len(cases)} types "
          f"({len(TYPE_SCHEMAS)} total schemas in table)")


def _self_test_layout_roundtrip() -> None:
    """End-to-end fixture round-trip: for every object in
    ``17_more_components/17.tft``, re-pack its allattbytes block using
    ``build_component_block`` (fed with the records decoded from the
    fixture, keyed by Attstrpianyi back-references) and byte-compare to
    the original. Same for the 180-byte Attstrpianyi slot table.
    """
    import pathlib
    import struct
    import sys
    scripts_dir = pathlib.Path(__file__).resolve().parents[1]
    fixture = (scripts_dir.parent / "tests" / "editor outputs" / "_old"
               / "17_more_components" / "17.tft")
    if not fixture.exists():
        print(f"SKIP: fixture not found at {fixture}")
        return
    sys.path.insert(0, str(scripts_dir))
    from lib.tft_attrs import (extract_all_attrs, parse_appinf1_corrected,
                               parse_objxinxi, OBJXINXI_ENTRY_SIZE,
                               ATTSTRPIANYI_OFFSET, ATTSTRPIANYI_SIZE as _ASZ)
    from lib.tft_attrs_layout import PER_LEI_LAYOUT
    from lib.h2_cipher import encrypt as h2_decrypt
    from lib.tft_format import APPINF0_MODELCRC_OFF, H2_START, H2_END

    data = fixture.read_bytes()
    model_crc = struct.unpack_from("<I", data, APPINF0_MODELCRC_OFF)[0]
    plain = h2_decrypt(data[H2_START:H2_END], model_crc)
    a1 = parse_appinf1_corrected(plain)
    entries = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    result = extract_all_attrs(data)

    # Build per-page record-index → AttRecord-built-from-decoded-fields map
    # so build_component_block can be fed actual records.
    page_dirs = []
    for i in range(a1["pageqyt"]):
        off = a1["pageadd"] + i * 16
        page_dirs.append((struct.unpack_from("<H", data, off)[0],
                          data[off + 2]))

    def to_attrec(decoded) -> AttRecord:
        # Reconstruct AttRecord from BinAttRecord fields (lossless for
        # encode_binattinf — typevalue's high nibble is collapsed into
        # the low nibble, but that's what the on-wire format stores).
        return AttRecord(
            name="",
            attmemorypos=decoded.attmemorypos,
            num_maxval=decoded.num_maxval,
            num_minval=decoded.num_minval,
            objdatarampos=decoded.objdatarampos,
            frompageid=decoded.frompageid,
            fromobjid=decoded.fromobjid,
            str_encodeh_star=decoded.str_encodeh_star,
            att_changeid=decoded.att_changeid,
            typevalue=decoded.attlei,
            datafenpei=decoded.merrylenth,
            change=decoded.flag_change,
            datafrom=decoded.flag_datafrom,
            ispv=decoded.flag_ispv,
            pp=decoded.flag_pp,
        )

    block_fail = 0
    pianyi_fail = 0
    blocks_checked = 0
    for obj_i, entry in enumerate(entries):
        page_idx = next(i for i, (s, q) in enumerate(page_dirs)
                        if s <= obj_i < s + q)
        page_recs = result["pages"][page_idx]["records"]
        slots = entry.populated_slots()
        if not slots:
            continue
        base = min(v for _, v in slots)
        stride, _ = PER_LEI_LAYOUT[entry.lei]

        # Build records_by_name from Attstrpianyi back-refs.
        records_by_name: dict[str, AttRecord] = {}
        for slot_n, rec_idx in slots:
            if slot_n >= len(APP_ATT_NAMES):
                continue
            name = APP_ATT_NAMES[slot_n]
            records_by_name[name] = to_attrec(page_recs[rec_idx])

        block, pianyi = build_component_block(
            entry.lei,
            page_record_base=base,
            records_by_name=records_by_name,
            bytecode_offset=entry.attstr_bytecode_offset,
        )
        # Encode the block and compare to the fixture's raw bytes for
        # records [base..base+stride).
        encoded = b"".join(encode_binattinf(r) for r in block)
        expected = b"".join(page_recs[base + i].raw for i in range(stride))
        if encoded != expected:
            block_fail += 1
            if block_fail <= 3:
                # Find first differing record offset for diagnostics.
                for i in range(stride):
                    if encoded[i*24:(i+1)*24] != expected[i*24:(i+1)*24]:
                        print(f"  obj{obj_i} lei={entry.lei} block diff at "
                              f"rec_off={i}:")
                        print(f"    got:      {encoded[i*24:(i+1)*24].hex()}")
                        print(f"    expected: {expected[i*24:(i+1)*24].hex()}")
                        break

        # Compare Attstrpianyi.
        actual_pianyi = entry.raw[ATTSTRPIANYI_OFFSET:
                                   ATTSTRPIANYI_OFFSET + _ASZ]
        if pianyi != actual_pianyi:
            pianyi_fail += 1
            if pianyi_fail <= 3:
                # Diagnostic: print first differing slot.
                for n in range(88):
                    g = struct.unpack_from("<H", pianyi, 4 + n * 2)[0]
                    e = struct.unpack_from("<H", actual_pianyi, 4 + n * 2)[0]
                    if g != e:
                        nm = APP_ATT_NAMES[n] if n < len(APP_ATT_NAMES) else f"?{n}"
                        print(f"  obj{obj_i} lei={entry.lei} pianyi slot"
                              f"[{n}={nm}]: got=0x{g:04x} expected=0x{e:04x}")
                        break
        blocks_checked += 1

    if block_fail or pianyi_fail:
        print(f"FAIL: {block_fail} block mismatches, "
              f"{pianyi_fail} Attstrpianyi mismatches "
              f"(out of {blocks_checked} objs)")
        raise SystemExit(1)
    print(f"PASS: layout round-trip {blocks_checked} objs "
          f"({sum(PER_LEI_LAYOUT[e.lei][0] for e in entries if e.populated_slots())} "
          f"records reconstructed byte-identical)")


def _self_test_long_attr_allocator() -> None:
    """Verify LongAttrAllocator's two-pass layout against the rules in
    findings/memory-allocation.md.

    1. Short Sstr (≤4 bytes) stays inline — no allocation.
    2. Long Sstr (>4 bytes) → ``mollocsize = len(value) + 1`` rounded up
       to 4. Record's attmemorypos gets the assigned offset; paired
       ``<name>_maxl`` Strlenth record gets the rounded capacity.
    3. molloc / binary requests with no init data land in the dynamic
       tail (after the Sstr init region), in queue order.
    """
    alloc = LongAttrAllocator(starting_offset=0)

    # Build a GuiObjText with a long txt string.
    long_value = b"Hello, Nextion!"   # 15 bytes; needs +1 NUL → 16 (round4)
    recs = build_component_records(
        "GuiObjText",
        objdatarampos=0x100,
        frompageid=0,
        fromobjid=2,
        component_id=2,
        component_type=116,
        x=0, y=69, w=160, h=31,
        authored={"txt": long_value, "txt_maxl": 50},
        allocator=alloc,
    )
    # Find the txt and txt_maxl records.
    txt_rec = next(r for r in recs if r.name == "txt")
    maxl_rec = next(r for r in recs if r.name == "txt_maxl")

    # Before finalize, both should still hold their pre-patch values (we
    # zeroed attmemorypos when we queued/linked).
    assert txt_rec.attmemorypos == 0
    assert maxl_rec.attmemorypos == 0

    # Now add a Variable with a *short* string (≤4 bytes) — should NOT
    # allocate.
    short_recs = build_component_records(
        "GuiObjVari",
        objdatarampos=0x200,
        frompageid=0,
        fromobjid=23,
        component_id=23,
        component_type=52,
        x=0, y=0, w=1, h=1,
        authored={"txt": b"AB", "val": 0},
        allocator=alloc,
    )
    short_txt = next(r for r in short_recs if r.name == "txt")
    # Short Sstr packs into attmemorypos directly: b"AB\0\0" → 0x00004241
    assert short_txt.attmemorypos == 0x00004241, (
        f"short Sstr should inline; got 0x{short_txt.attmemorypos:08x}"
    )

    mem_bytes = alloc.finalize()

    # Expected: long_value + b"\0" (NUL) + b"\0" (padding to 16 bytes).
    expected_init = long_value + b"\x00" * (16 - len(long_value))
    assert mem_bytes == expected_init, (
        f"alloc bytes: {mem_bytes!r} vs expected {expected_init!r}"
    )

    # After finalize: txt record points at offset 0; maxl record holds
    # the rounded capacity (16).
    assert txt_rec.attmemorypos == 0, (
        f"txt offset {txt_rec.attmemorypos}, expected 0"
    )
    assert maxl_rec.attmemorypos == 16, (
        f"txt_maxl capacity {maxl_rec.attmemorypos}, expected 16"
    )

    # Now exercise molloc + binary in a second allocator.
    alloc2 = LongAttrAllocator(starting_offset=0x40)
    s1 = alloc2.queue_sstr("foo", b"hi!")          # 3 bytes + NUL → 4
    s2 = alloc2.queue_sstr("bar", b"longer string") # 13 + NUL → 16
    m1 = alloc2.queue_molloc("curve_a", 100)        # → 100 (already 4-aligned)
    b1 = alloc2.queue_binary("blob", 13)            # 13 → 16
    alloc2.finalize()
    # init region:    foo @ 0x40 (4), bar @ 0x44 (16)  → 20 bytes init
    # dynamic region: curve_a @ 0x58 (100), blob @ 0xbc (16)
    # Check the queue stored offsets by re-running through a dummy
    # AttRecord. We don't have a record path here, so just inspect the
    # private state via re-queuing for testing.
    # The init_bytes/dynamic_bytes split is the public verification.
    assert alloc2.init_bytes == 4 + 16, alloc2.init_bytes
    assert alloc2.dynamic_bytes == 100 + 16, alloc2.dynamic_bytes
    assert alloc2.total_bytes == 4 + 16 + 100 + 16

    print(f"PASS: LongAttrAllocator (init={alloc2.init_bytes} "
          f"dynamic={alloc2.dynamic_bytes})")


if __name__ == "__main__":
    _self_test()
    _self_test_build_component_records()
    _self_test_layout_roundtrip()
    _self_test_long_attr_allocator()
