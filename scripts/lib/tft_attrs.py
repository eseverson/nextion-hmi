"""tft_attrs — per-component attribute record decoder.

Each component on a TFT page has a list of attributes (bco, pco, val,
txt, font, ...). The compiled values live in a uniform-stride 24-byte
record table at ``strdata + pagexinxi.attdataaddr``. Per-component
``PianyiData`` stores u16 back-references to which record holds each
attribute's value. The bytecode ``LOAD u16 k`` operand is that index.

This module:

- Walks the per-page 24-byte record table.
- Decodes the bit-packed type/length/flags field at +20.
- Surfaces ``(objpos, type, length, value, max, min)`` for every record.
- Optionally resolves the value bytes for inline-numeric attributes
  (Color / UU8 / UU16 / SS16 / SS32 / x / y / w / h / Picid / Fontid /
  Pageid / Gmovid / Videoid / Audioid / Hex16 / Strlenth).

For ``Sstr`` types the embedded 4-byte value is returned raw; resolving
strings longer than 4 bytes requires dereferencing into the global
memory area and isn't done here yet.

See ``nextion/findings/attribute-records.md`` for the algorithm
derivation.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass


BINATTINF_SIZE = 24


# (typevalue, datafenpei) extracted from hmitype.dll!attshulei::.cctor.
# Indexed by full typevalue (high nibble = kind, low nibble = storage form).
ATTSHULEI = {
    0x12: ("Color", 2),
    0x22: ("Picid", 2),
    0x31: ("Fontid", 1),
    0x42: ("Strlenth", 2),
    0x51: ("Select", 1),
    0x61: ("Type", 1),
    0x71: ("key", 1),
    0x82: ("Videoid", 2),
    0x92: ("Gmovid", 2),
    0xa2: ("Audioid", 2),
    0xa1: ("Pageid", 1),
    0xb2: ("Hex16", 2),
    0x01: ("UU8", 1),
    0x02: ("UU16", 2),
    0x03: ("UU32", 4),
    0x07: ("UU8_L", 1),
    0x08: ("SS16", 2),
    0x09: ("SS32", 4),
    0x19: ("binary", 4),
    0x0b: ("x", 2),
    0x0c: ("y", 2),
    0x0d: ("w", 2),
    0x0e: ("h", 2),
    0x0f: ("Sstr", 4),
    0xfe: ("BinyiANYTYPE", 4),
}

# Reverse lookup: low nibble -> {high-nibble: name}. The on-disk packed
# field only stores the low nibble (attlei & 0xF), so multiple semantic
# kinds map to the same record type. To recover the kind, the
# attribute's *name* is required (see ``attrs-raw.txt``).
LOW_NIBBLE_TO_FORMS = {
    0x0: [],  # zero -> empty / inactive
    0x1: ["UU8", "Pageid"],
    0x2: ["UU16", "Color", "Picid", "Strlenth", "Videoid", "Gmovid", "Audioid", "Hex16"],
    0x3: ["UU32"],
    0x7: ["UU8_L"],
    0x8: ["SS16"],
    0x9: ["SS32", "binary"],
    0xa: ["Pageid"],  # also Audioid (0xa2)
    0xb: ["x"],
    0xc: ["y"],
    0xd: ["w"],
    0xe: ["h", "BinyiANYTYPE"],
    0xf: ["Sstr"],
}


@dataclass
class BinAttRecord:
    """One decoded 24-byte attribute record."""
    index: int                    # position in the page's record table
    objdatarampos: int            # offset of owner's objdata_Ram in page media
    attmemorypos: int             # value or pointer-to-value
    num_maxval: int
    num_minval: int
    frompageid: int
    fromobjid: int
    str_encodeh_star: int
    att_changeid: int
    attlei: int                   # low 4 bits of typevalue
    merrylenth: int               # byte length (≈ datafenpei)
    flag_change: bool             # True if attribute can change at runtime
    flag_datafrom: bool           # True if attposup > -1 or == -2
    flag_ispv: bool               # True if page-volatile
    flag_pp: bool                 # True if permanent
    raw: bytes                    # original 24-byte slice

    def value_inline(self) -> int | bytes | None:
        """For numeric inline types, return the value of `attmemorypos`
        interpreted as the storage form. Returns None for types whose
        value lives elsewhere (`Sstr` >4 bytes, dynamically-allocated
        arrays).

        For `Sstr` returns the raw 4 bytes (the caller decides whether
        to decode as ASCII or follow as a pointer)."""
        if self.attlei == 0xf:
            # Sstr: 4 raw bytes embedded in attmemorypos
            return self.raw[4:8]
        if self.attlei == 0x0 or self.merrylenth == 0:
            return None
        # Read as little-endian signed / unsigned per the merrylenth.
        val_bytes = self.raw[4:4 + min(self.merrylenth, 4)]
        if self.attlei in (0x8, 0x9):
            # Signed
            n = struct.unpack(f"<{'i' if len(val_bytes) == 4 else 'h' if len(val_bytes) == 2 else 'b'}", val_bytes.ljust(4, b'\x00') if self.attlei == 0x9 else val_bytes)[0]
            return n
        # Unsigned default
        n = int.from_bytes(val_bytes, "little", signed=False)
        return n


def parse_records(data: bytes, attdataaddr_abs: int, n_records: int) -> list[BinAttRecord]:
    """Decode `n_records` consecutive 24-byte records starting at
    `attdataaddr_abs` (absolute file offset)."""
    out: list[BinAttRecord] = []
    for k in range(n_records):
        off = attdataaddr_abs + k * BINATTINF_SIZE
        if off + BINATTINF_SIZE > len(data):
            break
        rec = data[off:off + BINATTINF_SIZE]
        objpos = struct.unpack_from("<I", rec, 0)[0]
        memptr = struct.unpack_from("<i", rec, 4)[0]
        maxv = struct.unpack_from("<i", rec, 8)[0]
        minv = struct.unpack_from("<i", rec, 12)[0]
        fpid = rec[16]
        foid = rec[17]
        encode = rec[18]
        chid = rec[19]
        packed = struct.unpack_from("<I", rec, 20)[0]
        attlei = packed & 0xF
        flag_change = not bool((packed >> 4) & 1)
        flag_datafrom = bool((packed >> 5) & 1)
        flag_ispv = not bool((packed >> 6) & 1)
        flag_pp = not bool((packed >> 7) & 1)
        merrylenth = packed >> 8
        out.append(BinAttRecord(
            index=k, objdatarampos=objpos, attmemorypos=memptr,
            num_maxval=maxv, num_minval=minv,
            frompageid=fpid, fromobjid=foid,
            str_encodeh_star=encode, att_changeid=chid,
            attlei=attlei, merrylenth=merrylenth,
            flag_change=flag_change, flag_datafrom=flag_datafrom,
            flag_ispv=flag_ispv, flag_pp=flag_pp, raw=rec,
        ))
    return out


def parse_page_directory(data: bytes, pageadd: int, pageqyt: int) -> list[dict]:
    """Decode the page directory at file offset `pageadd`."""
    out = []
    for i in range(pageqyt):
        off = pageadd + i * 16
        rec = data[off:off + 16]
        out.append({
            "objstar": struct.unpack_from("<H", rec, 0)[0],
            "objqyt": rec[2],
            "res0": rec[3],
            "hexpos": struct.unpack_from("<I", rec, 4)[0],
            "attdataaddr_rel": struct.unpack_from("<I", rec, 8)[0],
            "medatapos": struct.unpack_from("<I", rec, 12)[0],
        })
    return out


OBJXINXI_ENTRY_SIZE = 232
ATTSTRPIANYI_OFFSET = 52
ATTSTRPIANYI_SIZE = 180
ATTSTRPIANYI_SLOT_COUNT = 88


@dataclass
class ObjxinxiEntry:
    """One decoded 232-byte objxinxi entry.

    Layout (see ``findings/attribute-records.md`` for the derivation):

    - +0   u8  ``lei`` (component type code, e.g. 121 = GuiObjPage, 59 = XFloat)
    - +1   u8  ``id``  (instance id)
    - +2   u16 (constant 0x3700; meaning unknown)
    - +4   u32 ``init_off`` (strdata-relative offset of init bytecode;
                            mirrored at +52)
    - +28  u32 ``objdatarampos`` (offset into page media blob)
    - +44  u16 ``w``
    - +46  u16 ``h``
    - +48  u16 ``endx`` (= x + w − 1)
    - +50  u16 ``endy`` (= y + h − 1)
    - +52  u32 ``attstr_bytecode_offset`` (Attstrpianyi.bytecode_offset;
                                          equals ``init_off``)
    - +56..+231: 88 × u16 ``slots`` indexed by AppAttNames[N]; each slot
       holds the record_index into the page-wide allattbytes table, or
       0xffff for "no record".
    """
    lei: int
    id: int
    init_off: int
    objdatarampos: int
    w: int
    h: int
    endx: int
    endy: int
    attstr_bytecode_offset: int
    slots: list[int]                 # 88 entries; 0xffff for unused
    raw: bytes                       # original 232-byte slice

    def populated_slots(self) -> list[tuple[int, int]]:
        """Return ``[(slot_index, record_index), ...]`` for filled slots."""
        return [(n, v) for n, v in enumerate(self.slots) if v != 0xFFFF]


def parse_objxinxi_entry(entry: bytes) -> ObjxinxiEntry:
    """Decode one 232-byte objxinxi entry."""
    if len(entry) < OBJXINXI_ENTRY_SIZE:
        raise ValueError(f"need {OBJXINXI_ENTRY_SIZE} bytes, got {len(entry)}")
    lei = entry[0]
    obj_id = entry[1]
    init_off = struct.unpack_from("<I", entry, 4)[0]
    objpos = struct.unpack_from("<I", entry, 28)[0]
    w, h, endx, endy = struct.unpack_from("<HHHH", entry, 44)
    bcode = struct.unpack_from("<I", entry, ATTSTRPIANYI_OFFSET)[0]
    slots = list(struct.unpack_from(
        f"<{ATTSTRPIANYI_SLOT_COUNT}H",
        entry,
        ATTSTRPIANYI_OFFSET + 4,
    ))
    return ObjxinxiEntry(
        lei=lei, id=obj_id, init_off=init_off, objdatarampos=objpos,
        w=w, h=h, endx=endx, endy=endy,
        attstr_bytecode_offset=bcode, slots=slots,
        raw=bytes(entry[:OBJXINXI_ENTRY_SIZE]),
    )


def parse_objxinxi(data: bytes, objxinxiadd: int, objqyt: int) -> list[ObjxinxiEntry]:
    """Decode ``objqyt`` consecutive objxinxi entries starting at file
    offset ``objxinxiadd``."""
    out: list[ObjxinxiEntry] = []
    for i in range(objqyt):
        off = objxinxiadd + i * OBJXINXI_ENTRY_SIZE
        out.append(parse_objxinxi_entry(data[off:off + OBJXINXI_ENTRY_SIZE]))
    return out


# Field layout for the 76-byte ``appinf1`` plaintext (F-series).
# See ``findings/attribute-records.md#corrections-to-other-findings``
# and ``findings/main-hmi-config.md``.
APPINF1_LAYOUT: list[tuple[str, str, int]] = [
    ("staticstrBeg", "<I", 0x00),
    ("AppAllvasAddr", "<I", 0x04),
    ("AppAllvasQty", "<I", 0x08),
    ("attdataaddr", "<I", 0x0c),
    ("resourcesfileddr", "<I", 0x10),
    ("strdataaddr", "<I", 0x14),
    ("pageadd", "<I", 0x18),
    ("objxinxiadd", "<I", 0x1c),
    ("picxinxiadd", "<I", 0x20),
    ("gmovxinxiadd", "<I", 0x24),
    ("videoxinxiadd", "<I", 0x28),
    ("wavxinxiadd", "<I", 0x2c),
    ("zimoxinxiadd", "<I", 0x30),
    ("MainCodeHex", "<I", 0x34),
    ("pageqyt", "<H", 0x38),
    ("objqyt", "<H", 0x3a),
    ("picqyt", "<H", 0x3c),
    ("gmovqyt", "<H", 0x3e),
    ("videoqyt", "<H", 0x40),
    ("wavqyt", "<H", 0x42),
    ("zimoqyt", "<H", 0x44),
]


def pack_appinf1(orig_h2_plain: bytes, fields: dict) -> bytes:
    """Re-pack the 76-byte ``appinf1`` plaintext with new field values.

    ``orig_h2_plain`` must be the H2 plaintext (196 bytes); only the
    first 76 are the structured ``appinf1`` — the rest is ``0xff``
    padding (see ``findings/h2-trailing.md``) and is preserved.

    ``fields`` provides the new values; every key in :data:`APPINF1_LAYOUT`
    must be present.
    """
    out = bytearray(orig_h2_plain)
    for name, fmt, off in APPINF1_LAYOUT:
        if name not in fields:
            raise KeyError(f"missing appinf1 field {name!r}")
        struct.pack_into(fmt, out, off, fields[name])
    return bytes(out)


def parse_appinf1_corrected(plain_h2: bytes) -> dict:
    """Decode the F-series appinf1 (76-byte plaintext H2) using the
    real field offsets recovered from hmitype.dll.

    The legacy decoder in ``scripts/lib/tft_format.py`` returns garbled
    offsets for several fields (it uses the TFTTool T0/K0 schema). This
    helper returns the correct F-series view.
    """
    fields = [
        ("staticstrBeg", "<I", 0x00),
        ("AppAllvasAddr", "<I", 0x04),
        ("AppAllvasQty", "<I", 0x08),
        ("attdataaddr", "<I", 0x0c),
        ("resourcesfileddr", "<I", 0x10),
        ("strdataaddr", "<I", 0x14),
        ("pageadd", "<I", 0x18),
        ("objxinxiadd", "<I", 0x1c),
        ("picxinxiadd", "<I", 0x20),
        ("gmovxinxiadd", "<I", 0x24),
        ("videoxinxiadd", "<I", 0x28),
        ("wavxinxiadd", "<I", 0x2c),
        ("zimoxinxiadd", "<I", 0x30),
        ("MainCodeHex", "<I", 0x34),
        ("pageqyt", "<H", 0x38),
        ("objqyt", "<H", 0x3a),
        ("picqyt", "<H", 0x3c),
        ("gmovqyt", "<H", 0x3e),
        ("videoqyt", "<H", 0x40),
        ("wavqyt", "<H", 0x42),
        ("zimoqyt", "<H", 0x44),
    ]
    return {name: struct.unpack_from(fmt, plain_h2, off)[0]
            for (name, fmt, off) in fields}


def extract_all_attrs(data: bytes) -> dict:
    """High-level: decrypt H2, list each page's record table, return:

        {
            "appinf1": {<corrected appinf1 fields>},
            "pages": [
                {"page_idx": 0, "objqyt": 30, "attdataaddr_abs": 0x82808,
                 "n_records": 240, "records": [BinAttRecord, ...]},
                ...
            ]
        }

    `n_records` per page is computed from the gap between adjacent
    `attdataaddr_rel` values; the last page extends to the next region
    (strdatasize end), so it's approximated as 24-aligned to objqyt × 32.
    """
    # Importable both as a script (no parent package) and as a module.
    # NB: in this repo h2_cipher.encrypt is the asm-verbatim routine that
    # actually decrypts H2; see sim/tft_loader.py:54 for the same alias.
    try:
        from .h2_cipher import encrypt as h2_decrypt
        from .tft_format import APPINF0_MODELCRC_OFF, H2_START, H2_END
    except ImportError:
        from h2_cipher import encrypt as h2_decrypt
        from tft_format import APPINF0_MODELCRC_OFF, H2_START, H2_END

    model_crc = struct.unpack_from("<I", data, APPINF0_MODELCRC_OFF)[0]
    h2_plain = h2_decrypt(data[H2_START:H2_END], model_crc)
    a1 = parse_appinf1_corrected(h2_plain)
    pages = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    out_pages = []
    strd = a1["strdataaddr"]
    for i, p in enumerate(pages):
        abs_addr = strd + p["attdataaddr_rel"]
        # Count = (next page's attdataaddr_rel - this page's) / 24; for
        # the last page extend to (pageadd - this page's abs)/24.
        if i + 1 < len(pages):
            end_rel = pages[i + 1]["attdataaddr_rel"]
        else:
            end_rel = a1["pageadd"] - strd
        n_records = (end_rel - p["attdataaddr_rel"]) // BINATTINF_SIZE
        recs = parse_records(data, abs_addr, n_records)
        out_pages.append({
            "page_idx": i,
            "objstar": p["objstar"],
            "objqyt": p["objqyt"],
            "attdataaddr_abs": abs_addr,
            "n_records": n_records,
            "records": recs,
        })
    return {"appinf1": a1, "pages": out_pages}


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) != 2:
        print("usage: tft_attrs.py <file.tft>")
        sys.exit(1)
    data = open(sys.argv[1], "rb").read()
    result = extract_all_attrs(data)
    a1 = result["appinf1"]
    print(f"appinf1: pageqyt={a1['pageqyt']} objqyt={a1['objqyt']}")
    print(f"         attdataaddr=0x{a1['attdataaddr']:x}")
    for pg in result["pages"]:
        print(f"page {pg['page_idx']}: objqyt={pg['objqyt']} "
              f"records={pg['n_records']} @ 0x{pg['attdataaddr_abs']:x}")
        # Group records by objpos
        from collections import defaultdict
        by_obj = defaultdict(list)
        for r in pg["records"]:
            if r.attlei != 0:
                by_obj[r.objdatarampos].append(r)
        for op, recs in sorted(by_obj.items())[:10]:
            attlei_names = ",".join(f"{r.attlei:#x}" for r in recs[:8])
            print(f"  objpos={op:5d}: {len(recs):3d} attrs  "
                  f"(types: {attlei_names}{'...' if len(recs) > 8 else ''})")
