"""compute_appinf1 — derive the H2 ``appinf1`` directory from a body
description.

The 76-byte ``appinf1`` struct (file offset 0xC8, H2-encrypted) holds the
master directory of body section offsets and counts. Earlier we had a
research-only helper that just *packed* whatever the caller pre-computed;
this module **derives** every offset/count from a higher-level
``BodyDescription`` that describes the body contents (page count, picture
data blocks, font headers, AppAllvas entries, total attribute-record
bytes, etc.).

See ``findings/format-tft.md`` for the underlying layout and
``findings/attribute-records.md`` for the per-page record table.

Body layout (F-series, as derived empirically from the editor outputs in
``tests/editor outputs/_old/``):

  resources region (absolute file offsets):
    +0x10000 = resourcesfileddr  — fixed
    ...bootloader / drivers / fonts-in-resources...
    +picxinxiadd                 — Picturexinxi records (24 B each)
    +picxinxiadd + 24·picqyt     — picture pixel data, contiguous
    +zimoxinxiadd                — ZI font headers (44 B each) + names
                                    + glyph data, contiguous
    ...optionally padding to strdataaddr...
    +strdataaddr = resourcesfileddr + resources_size

  strdata region (offsets relative to strdataaddr, except where noted):
    +0                            — init bytecode region
    +staticstrBeg = AppAllvasAddr — start of static-data: AppAllvas table
    +AppAllvasAddr + 12·AppAllvasQty
                = attdataaddr     — per-page 24-byte attribute records
    +attdataaddr + sum_records   — end of strdata

  trailing tables (absolute file offsets):
    +pageadd                      — page directory (16 B each, pageqyt entries)
    +objxinxiadd = pageadd + 16·pageqyt
                                  — component directory (232 B each, objqyt)
    +objxinxiadd + 232·objqyt     — end of body
    +file_size − 4                — trailing CRC

The picxinxiadd / zimoxinxiadd / gmov / video / wav addresses are
**absolute file offsets** within the resources blob.  attdataaddr,
AppAllvasAddr, staticstrBeg are **strdata-relative**.

Constants the caller still has to supply:

  * ``picxinxi_offset_in_resources`` — where the picture directory sits
    inside the resources blob.  In every editor-emitted fixture observed
    so far this is ``0x48b5d`` (so picxinxiadd = 0x58b5d), determined by
    the bootloader / driver / font-table composition of the resources
    section.  When picqyt == 0 the same value is used for
    gmovxinxiadd / videoxinxiadd / wavxinxiadd / zimoxinxiadd if those
    are also empty.

The reserved fields (``res1``, ``res2``, ``res3``) and the trailing 120
bytes of the 196-byte H2 region (``H2[0x4c..0xc4]``) are filled with
``0xff`` (see ``findings/h2-trailing.md``).
"""
from __future__ import annotations
import struct
from dataclasses import dataclass, field

from scripts.lib.tft_attrs import APPINF1_LAYOUT


# Constants confirmed against every fixture in
# ``tests/editor outputs/_old/``.
RESOURCESFILEDDR: int = 0x10000   # appinf1.resourcesfileddr (constant)
APPINF1_PLAIN_SIZE: int = 0x4c    # 76 bytes
H2_PLAIN_SIZE: int = 0xc4         # 196 bytes (encrypted block size)
APPALLVAS_ENTRY_SIZE: int = 12
PAGEXINXI_ENTRY_SIZE: int = 16
OBJXINXI_ENTRY_SIZE: int = 232
BINATTINF_RECORD_SIZE: int = 24
PICXINXI_RECORD_SIZE: int = 24
ZIMOXINXI_HEADER_SIZE: int = 44


@dataclass
class PictureDesc:
    """One picture record (24-byte index entry + pixel-data block)."""
    pictureid: int            # u16 — picture id used by Picture component
    width: int                # u16
    height: int               # u16
    data_size: int            # u32 — bytes including any per-picture header
                              # (alpha mask metadata + W·H·2 RGB565 pixels).
                              # This is the value stored in the index
                              # record's `imgbytesize` field.
    qumo: int = 0             # +0  u8
    quality: int = 0          # +1  u8
    alphaen: int = 0          # +2  u8
    picdatatype: int = 3      # +3  u8 (3 = RGB565 + alpha header)
    encodeen: int = 0         # +6  u8
    alphaaddr: int = 0        # +20 i32 (0 if not used)


@dataclass
class FontDesc:
    """One ZI font header (44 B) plus its inline name + glyph data
    block (read end-to-end from the bytes immediately following all
    fonts' 44-byte header rows).

    For derivation purposes we only need the *total bytes* that this
    font's name + glyph block occupies, since
    ``zimoxinxiadd + total_font_payload`` is the end of the font region.
    """
    name_and_glyph_bytes: int   # size of <name + glyph data> block


@dataclass
class BodyDescription:
    """Caller-supplied description of the body contents.

    Pre-computed payload sizes are sufficient — the caller doesn't need
    to assemble the actual bytes here, only describe their sizes so the
    helper can lay them out.
    """
    model_crc: int

    # H2 fields.
    encode: int = 3
    main_code_hex: int = 0x4c

    # ── Resources region ────────────────────────────────────────────────
    resources_size: int = 0x70000
    """Total bytes of the resources blob (``strdataaddr - 0x10000``).
    Default 0x70000 matches the F-series 4.3" baseline."""

    picxinxi_offset_in_resources: int = 0x48b5d
    """Byte offset of the Picturexinxi directory inside the resources
    blob (so absolute picxinxiadd = resourcesfileddr + this)."""

    pictures: list[PictureDesc] = field(default_factory=list)
    fonts: list[FontDesc] = field(default_factory=list)
    gmovs: list[int] = field(default_factory=list)   # data block sizes
    videos: list[int] = field(default_factory=list)
    wavs: list[int] = field(default_factory=list)

    # ── strdata region ──────────────────────────────────────────────────
    init_bytecode_size: int = 0
    """Bytes of the init-bytecode region at the start of strdata; sets
    ``staticstrBeg`` (= ``AppAllvasAddr``)."""

    app_allvas_qty: int = 3
    """Number of AppAllvas entries (12 B each). Always at least 3 for
    the implicit ``sys0/sys1/sys2`` globals in real projects."""

    attribute_records_bytes: int = 0
    """Total bytes of the concatenated per-page 24-byte attribute
    records (``binattinf``)."""

    # ── Trailing directories ────────────────────────────────────────────
    page_qty: int = 0
    obj_qty: int = 0
    """Total component count across all pages (sum of each page's
    objqyt)."""


@dataclass
class ComputedAppinf1:
    """Result of :func:`compute_appinf1`.

    Provides both the structured field dict (handy for callers that
    want to introspect) and the encrypted 196-byte H2 payload ready to
    write at file offset 0xC8."""
    fields: dict           # all appinf1 fields, derived
    plaintext: bytes       # 196 bytes (76 struct + 120 × 0xff padding)
    encrypted: bytes       # 196 bytes ciphertext for file[0xC8:0x18C]


def _pic_payload_size(pic: PictureDesc) -> int:
    """Bytes contributed to the picture region by one picture: index
    record (24 B) + pixel-data block (``pic.data_size``)."""
    return PICXINXI_RECORD_SIZE + pic.data_size


def _total_picture_data(pics: list[PictureDesc]) -> int:
    """Total pixel-data bytes for all pictures (NOT including the
    24-byte index records)."""
    return sum(p.data_size for p in pics)


def _total_font_payload(fonts: list[FontDesc]) -> int:
    """Total bytes contributed by the font region: N × 44-byte headers
    plus each font's name+glyph block."""
    return (ZIMOXINXI_HEADER_SIZE * len(fonts)
            + sum(f.name_and_glyph_bytes for f in fonts))


def derive_appinf1_fields(body: BodyDescription) -> dict:
    """Lay out the body sections in canonical order and produce every
    field that goes into ``appinf1``.

    Returns a dict whose keys match :data:`scripts.lib.tft_attrs.APPINF1_LAYOUT`
    plus the three reserved zero fields.
    """
    # ── Resources region ────────────────────────────────────────────────
    resourcesfileddr = RESOURCESFILEDDR
    strdataaddr = resourcesfileddr + body.resources_size

    picxinxiadd = resourcesfileddr + body.picxinxi_offset_in_resources

    # Pictures sit immediately after the 24-byte index table.
    pic_data_total = _total_picture_data(body.pictures)
    # The font region begins right after the picture data; if there are
    # no pictures, zimoxinxiadd starts at picxinxiadd directly.
    zimoxinxiadd = (picxinxiadd
                    + PICXINXI_RECORD_SIZE * len(body.pictures)
                    + pic_data_total)

    # gmov / video / wav directories are written as 0 when empty (every
    # fixture observed has gmovqyt == videoqyt == wavqyt == 0). The
    # layout for populated lists is unknown from this corpus; emit 0
    # for now, leaving a follow-up if a fixture ever exercises them.
    gmovxinxiadd = 0 if not body.gmovs else picxinxiadd
    videoxinxiadd = 0 if not body.videos else picxinxiadd
    wavxinxiadd = 0 if not body.wavs else picxinxiadd

    # ── strdata region ──────────────────────────────────────────────────
    static_str_beg = body.init_bytecode_size       # strdata-relative
    app_allvas_addr = static_str_beg                # they coincide
    attdataaddr = (app_allvas_addr
                   + APPALLVAS_ENTRY_SIZE * body.app_allvas_qty)

    # ── Trailing directories (absolute file offsets) ────────────────────
    end_of_strdata_rel = attdataaddr + body.attribute_records_bytes
    pageadd = strdataaddr + end_of_strdata_rel
    objxinxiadd = pageadd + PAGEXINXI_ENTRY_SIZE * body.page_qty
    # Body ends at objxinxiadd + 232·objqyt; the trailing CRC follows.

    fields = {
        "staticstrBeg": static_str_beg,
        "AppAllvasAddr": app_allvas_addr,
        "AppAllvasQty": body.app_allvas_qty,
        "attdataaddr": attdataaddr,
        "resourcesfileddr": resourcesfileddr,
        "strdataaddr": strdataaddr,
        "pageadd": pageadd,
        "objxinxiadd": objxinxiadd,
        "picxinxiadd": picxinxiadd,
        "gmovxinxiadd": gmovxinxiadd,
        "videoxinxiadd": videoxinxiadd,
        "wavxinxiadd": wavxinxiadd,
        "zimoxinxiadd": zimoxinxiadd,
        "MainCodeHex": body.main_code_hex,
        "pageqyt": body.page_qty,
        "objqyt": body.obj_qty,
        "picqyt": len(body.pictures),
        "gmovqyt": len(body.gmovs),
        "videoqyt": len(body.videos),
        "wavqyt": len(body.wavs),
        "zimoqyt": len(body.fonts),
        "encode": body.encode,
        # Reserved (always zero in observed fixtures).
        "_res1": 0,
        "_res2": 0,
        "_res3": 0,
    }
    return fields


# 14 u32s + 8 u16s + 2 u8s + 1 u16 = 76 bytes.
_APPINF1_FMT = "<" + "I" * 14 + "H" * 8 + "BBH"
assert struct.calcsize(_APPINF1_FMT) == APPINF1_PLAIN_SIZE


def _pack_appinf1_plain(f: dict) -> bytes:
    """Pack the 76-byte ``appinf1`` plaintext from a derived field dict."""
    return struct.pack(
        _APPINF1_FMT,
        f["staticstrBeg"],
        f["AppAllvasAddr"],
        f["AppAllvasQty"],
        f["attdataaddr"],
        f["resourcesfileddr"],
        f["strdataaddr"],
        f["pageadd"],
        f["objxinxiadd"],
        f["picxinxiadd"],
        f["gmovxinxiadd"],
        f["videoxinxiadd"],
        f["wavxinxiadd"],
        f["zimoxinxiadd"],
        f["MainCodeHex"],
        f["pageqyt"],
        f["objqyt"],
        f["picqyt"],
        f["gmovqyt"],
        f["videoqyt"],
        f["wavqyt"],
        f["zimoqyt"],
        f["_res1"],
        f["encode"],
        f["_res2"],
        f["_res3"],
    )


def compute_appinf1(body: BodyDescription) -> ComputedAppinf1:
    """Derive every ``appinf1`` field from ``body``, pack it, and encrypt
    the 196-byte H2 region with the model-CRC-keyed H2 cipher.

    Returns the structured field dict, the 196-byte plaintext, and the
    196-byte ciphertext ready to write at file offset 0xC8.
    """
    # Note: h2_cipher names are asm-verbatim. encrypt() = DecData (read
    # direction, used when *parsing* an existing TFT), decrypt() = Encode
    # (write direction, used when *building* a TFT).  See
    # ``findings/h2-cipher.md`` and ``sim/tft_loader.py``.
    from scripts.lib.h2_cipher import decrypt as _h2_encode

    fields = derive_appinf1_fields(body)
    plain = _pack_appinf1_plain(fields)
    # 120 trailing 0xff bytes — see findings/h2-trailing.md.
    plain += b"\xff" * (H2_PLAIN_SIZE - APPINF1_PLAIN_SIZE)
    assert len(plain) == H2_PLAIN_SIZE
    ciphertext = _h2_encode(plain, body.model_crc)
    return ComputedAppinf1(
        fields=fields,
        plaintext=plain,
        encrypted=ciphertext,
    )


# ─────────────────────────────────────────────────────────────────────────
# Reverse helper: derive a BodyDescription from a parsed TFT file.
# Used by the fixture round-trip test below.
# ─────────────────────────────────────────────────────────────────────────


def body_description_from_tft(raw: bytes) -> BodyDescription:
    """Walk a parsed TFT and reconstruct the high-level
    ``BodyDescription`` that, when fed back to :func:`compute_appinf1`,
    must reproduce the original ``appinf1`` byte-for-byte.

    Round-trip helper for tests, not for general use.
    """
    from scripts.lib import tft_format, tft_attrs

    hdr = tft_format.parse(raw)
    a = tft_attrs.parse_appinf1_corrected(hdr.appinf1)

    # Resources size = strdataaddr − resourcesfileddr.
    resources_size = a["strdataaddr"] - a["resourcesfileddr"]
    picxinxi_offset_in_resources = a["picxinxiadd"] - a["resourcesfileddr"]

    # Pictures: parse each 24-byte index record.
    pictures: list[PictureDesc] = []
    for n in range(a["picqyt"]):
        rec_off = a["picxinxiadd"] + n * PICXINXI_RECORD_SIZE
        rec = raw[rec_off:rec_off + PICXINXI_RECORD_SIZE]
        pictures.append(PictureDesc(
            pictureid=struct.unpack_from("<H", rec, 4)[0],
            width=struct.unpack_from("<H", rec, 12)[0],
            height=struct.unpack_from("<H", rec, 14)[0],
            data_size=struct.unpack_from("<I", rec, 16)[0],
            qumo=rec[0],
            quality=rec[1],
            alphaen=rec[2],
            picdatatype=rec[3],
            encodeen=rec[6],
            alphaaddr=struct.unpack_from("<i", rec, 20)[0],
        ))

    # Fonts: each header's "data_start" at +24 is the offset from
    # zimoxinxiadd to the start of that font's name+glyph block. The
    # block runs until the next font's name+glyph block (or strdataaddr
    # for the last font). We only need the size for layout purposes.
    fonts: list[FontDesc] = []
    if a["zimoqyt"]:
        data_starts: list[int] = []
        for n in range(a["zimoqyt"]):
            hdr_off = a["zimoxinxiadd"] + n * ZIMOXINXI_HEADER_SIZE
            data_starts.append(struct.unpack_from("<I", raw, hdr_off + 24)[0])
        # End of last font's payload = strdataaddr − zimoxinxiadd.
        data_starts.append(a["strdataaddr"] - a["zimoxinxiadd"])
        for n in range(a["zimoqyt"]):
            block = data_starts[n + 1] - data_starts[n]
            fonts.append(FontDesc(name_and_glyph_bytes=block))

    # The strdata region: init bytecode runs 0..staticstrBeg, then
    # AppAllvas table (12 × qty), then attribute records, then end.
    init_bytecode_size = a["staticstrBeg"]
    attribute_records_bytes = (a["pageadd"]
                               - a["strdataaddr"]
                               - a["attdataaddr"])

    return BodyDescription(
        model_crc=hdr.model_crc,
        encode=struct.unpack_from("<B", hdr.appinf1, 0x48)[0],
        main_code_hex=a["MainCodeHex"],
        resources_size=resources_size,
        picxinxi_offset_in_resources=picxinxi_offset_in_resources,
        pictures=pictures,
        fonts=fonts,
        gmovs=[],     # No fixtures exercise these yet.
        videos=[],
        wavs=[],
        init_bytecode_size=init_bytecode_size,
        app_allvas_qty=a["AppAllvasQty"],
        attribute_records_bytes=attribute_records_bytes,
        page_qty=a["pageqyt"],
        obj_qty=a["objqyt"],
    )


# Re-export for code that may have imported the older name.
__all__ = [
    "BodyDescription",
    "PictureDesc",
    "FontDesc",
    "ComputedAppinf1",
    "compute_appinf1",
    "derive_appinf1_fields",
    "body_description_from_tft",
    "APPINF1_LAYOUT",
]
