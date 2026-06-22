"""main_hmi — build and CRC-verify main.HMI blobs.

``main.HMI`` is the project-manifest blob stored in every ``.HMI`` file.
Its 96-byte ``hmifilehead`` header carries model identity, display config,
and the resource-reference count; the header is followed by a variable-
length array of 16-byte ``(ext, name)`` resource entries (pages + fonts).

CRC — H5 (was open question in ``findings/format-hmi.md``):
    The blob CRC uses the same :func:`scripts.lib.page_crc.crc32_bytewise`
    family as page blobs, chained over five segments:

        crc = CRC32(0xFFFFFFFF, blob[4:])          # full body
        crc = CRC32(crc, blob[0x10:0x14])           # Modelcrc, re-fed
        crc = CRC32(crc, blob[0x04:0x08])           # Datasize, re-fed
        crc = CRC32(crc, blob[0x0a:0x0b])           # filever, re-fed
        crc = CRC32(crc, blob[0x0e:0x0f])           # hmiffid (0x4f), re-fed

    The total re-mix covers 10 bytes — the same count as the page-blob
    chain (4+4+1+1). Verified against four distinct stored CRCs covering
    guidire=0/1, ResourcesFileQyt=1/6/7, and two upver1 values.

Segment fields (``hmifilehead`` struct, IL line 31752-31823):

    +0x00  u32  crc                    blob CRC
    +0x04  u32  Datasize               always 0x60 (header size)
    +0x08  u8   upver0                 editor major (1)
    +0x09  u8   upver1                 editor minor (0x43-0x44)
    +0x0a  u8   filever                file-format magic (0x21)
    +0x0b  u8   xiliemark              series mark (0x64 = F-series)
    +0x0c  u8   guidire                orientation (0..3)
    +0x0d  u8   encode                 text encoding (3 = UTF-8/GB2312)
    +0x0e  u8   hmiffid                HMI format magic (0x4f)
    +0x0f  u8   otp                    OTP flag (0)
    +0x10  u32  Modelcrc               CRC of model name string
    +0x14  u32  password               project password (0 = none)
    +0x18  u32  ResourcesFileTypeAddr  offset to resource array (= 0x60)
    +0x1c  u32  ResourcesFileQyt       resource entry count
    +0x20  i32  MemoryFileSystemLenth  embedded-FS bytes (0)
    +0x24  u8   upver2                 patch-version stamp (1)
    +0x25  u8   RAM1OPEN               (0)
    +0x26  u8   resourcescancel_font   (0)
    +0x27  u8   resourcescancel_pic    (0)
    +0x28  6×u32 APPMEDATAHEX0..5      (all 0)
    +0x40  u8   TT_asp100_tc           (0)
    +0x41  u8   picencodever           picture-encoding version (2 in 1.67.x)
    +0x42  u16  res1                   (0)
    +0x44  7×u32 res15..21             (all 0)
    =0x60

After the header: ``ResourcesFileQyt`` × 16-byte entries, each:
    [0:8]   ext   (ASCII, right-padded with NUL), e.g. b'zi\\x00…'
    [8:16]  name  (ASCII, right-padded with NUL), e.g. b'0.zi\\x00…'
"""
from __future__ import annotations
import struct

from scripts.lib.page_crc import crc32_bytewise

HEADER_SIZE = 0x60
_FILEVER  = 0x21   # file-format magic
_XILIEMARK = 0x64  # F-series mark
_HMIFFID  = 0x4f   # HMI format magic
_UPVER2   = 0x01   # patch-version stamp


def main_hmi_crc(blob: bytes) -> int:
    """Compute the stored CRC for a main.HMI blob (H5 algorithm).

    Input is the full blob including the leading 4-byte CRC field (which
    is ignored and re-derived).  Stored at ``blob[0:4]`` little-endian.
    """
    crc = crc32_bytewise(0xFFFFFFFF, blob[4:])
    crc = crc32_bytewise(crc, blob[0x10:0x14])  # Modelcrc
    crc = crc32_bytewise(crc, blob[0x04:0x08])  # Datasize
    crc = crc32_bytewise(crc, blob[0x0a:0x0b])  # filever
    crc = crc32_bytewise(crc, blob[0x0e:0x0f])  # hmiffid
    return crc


def verify(blob: bytes) -> bool:
    """Return True if ``blob[0:4]`` matches the computed main.HMI CRC."""
    stored = struct.unpack_from("<I", blob, 0)[0]
    return main_hmi_crc(blob) == stored


def write_main_hmi(
    *,
    model_crc: int,
    resources: list[tuple[str, str]],
    guidire: int = 0x01,
    encode: int = 0x03,
    upver0: int = 0x01,
    upver1: int = 0x44,
    picencodever: int = 0x02,
) -> bytes:
    """Build a complete main.HMI blob with the CRC computed.

    Args:
        model_crc:     CRC of the model-name string, e.g.
                       ``0x1ce47603`` for ``NX4832F035_011``.
        resources:     Ordered list of ``(ext, name)`` pairs, e.g.
                       ``[('zi', '0.zi'), ('pa', '0.pa')]``.  Fonts
                       (``zi``) conventionally come before pages (``pa``),
                       and within each group entries follow their HMI
                       directory order.
        guidire:       GUI orientation byte (0..3; 1 = portrait 0°).
        encode:        Text-encoding id (3 = UTF-8 / GB2312).
        upver0:        Editor major-version stamp (1).
        upver1:        Editor minor-version stamp (e.g. 0x44 = v1.67.2).
        picencodever:  Picture-encoding version (2 in editor ≥ 1.67.x).

    Returns:
        The full blob bytes (header + resource array) with CRC at [0:4].
    """
    qty = len(resources)

    hdr = bytearray(HEADER_SIZE)
    # [0:4] CRC — filled in after body is finalised
    struct.pack_into("<I", hdr, 0x04, HEADER_SIZE)      # Datasize
    hdr[0x08] = upver0
    hdr[0x09] = upver1
    hdr[0x0a] = _FILEVER
    hdr[0x0b] = _XILIEMARK
    hdr[0x0c] = guidire
    hdr[0x0d] = encode
    hdr[0x0e] = _HMIFFID
    # hdr[0x0f] = otp = 0
    struct.pack_into("<I", hdr, 0x10, model_crc & 0xFFFFFFFF)
    # password = 0 at [0x14:0x18]
    struct.pack_into("<I", hdr, 0x18, HEADER_SIZE)       # ResourcesFileTypeAddr
    struct.pack_into("<I", hdr, 0x1c, qty)
    # MemoryFileSystemLenth = 0 at [0x20:0x24]
    hdr[0x24] = _UPVER2
    # RAM1OPEN, resourcescancel_font/pic = 0 at [0x25:0x28]
    # APPMEDATAHEX0..5 = 0 at [0x28:0x40]
    # TT_asp100_tc = 0 at [0x40]
    hdr[0x41] = picencodever
    # res1..res21 = 0 at [0x42:0x60]

    res_arr = bytearray()
    for ext, name in resources:
        ext_b = ext.encode("ascii")[:8].ljust(8, b"\x00")
        name_b = name.encode("ascii")[:8].ljust(8, b"\x00")
        res_arr += ext_b + name_b

    blob = bytes(hdr) + bytes(res_arr)
    crc = main_hmi_crc(blob)
    return struct.pack("<I", crc) + blob[4:]


__all__ = ["main_hmi_crc", "verify", "write_main_hmi"]
