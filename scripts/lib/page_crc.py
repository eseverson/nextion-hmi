#!/usr/bin/env python3
"""page_crc — compute the leading-4-byte CRC of an HMI page blob.

Reverse-engineered from the Nextion Editor's bundled native achmi.dll
(the unmanaged dispatch shim behind achmiface.HmiSafe.HmiSafeWritePageFileSafe,
subcommand 0x27). The .NET writer (hmitype.Myapp_inf.OutPutPageFile) writes the
56-byte page header with crc=0, then hands the file pointer to this routine,
which patches a few header bytes and finalises the CRC.

The table is the standard CRC-32/MPEG-2 lookup (polynomial 0x04C11DB7,
MSB-first, no reflection), bundled in both hmitype.AppServer.tab256 and
achmi.dll's .rdata. Each byte is mixed in by 4 rounds of
``crc = (crc << 8) ^ tab[crc >> 24]`` after the byte is XORed into the low
byte (i.e. mixing is per-byte, not per-bit), which is the byte-wise routine
``hmitype.AppServer.CRC32`` (same diffusion as standard byte-table CRC-32 but
with a different, deliberately-redundant pass count).

The page CRC chains 5 segments:

    crc = CRC32(0xFFFFFFFF, page[4:filesize])      # body
    crc = CRC32(crc,        page[4:8])              # datasize, re-CRCed
    crc = CRC32(crc,        page[0xc:0x10])         # datainformationqyt
    crc = CRC32(crc,        page[0x14:0x15])        # pagelock byte
    crc = CRC32(crc,        page[0x15:0x16])        # hmiffid byte (the 0x4f marker)

The four trailing micro-steps run over header bytes that are *also* covered by
the body sweep — the value lands at a different state because the running
register is non-zero by then. Stored at page[0:4] little-endian.

Verified against all four live `*.pa` blobs in the baseline editor save:
3.pa, 2.pa, 1.pa, 0.pa. The 0x214f magic at page[0x15:0x17] and the upver
patches at page[0x1a]/page[0x1c]/page[0x1d] are written by the same native
function before the CRC pass — they're driven by global config bytes the
editor maintains separately, so any roundtrip writer needs to mirror them
before computing the CRC.
"""
from __future__ import annotations
import struct
from pathlib import Path


# CRC-32/MPEG-2 lookup table (poly 0x04C11DB7, MSB-first, no reflection).
def _make_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7 if crc & 0x80000000 else (crc << 1)) & 0xFFFFFFFF
        table.append(crc)
    return table


TAB256 = _make_table()


def crc32_bytewise(seed: int, data: bytes) -> int:
    """Byte-wise Nextion CRC. Equivalent to hmitype.AppServer.CRC32 in the
    decompiled editor and the routine at achmi.dll!10007930.

    Each byte is XORed into the low 8 bits of the running register, then
    four rounds of ``r = (r << 8) ^ tab[r >> 24]`` mix the bits.
    """
    r = seed & 0xFFFFFFFF
    for b in data:
        r ^= b
        for _ in range(4):
            r = ((r << 8) & 0xFFFFFFFF) ^ TAB256[(r >> 24) & 0xFF]
    return r & 0xFFFFFFFF


OBJNAME_FIELD_SIZE = 14


def hash_objname(name: str | bytes) -> int:
    """Compute the TFT component-name lookup hash for an objname.

    The TFT stores names as `crc32_bytewise(0xffffffff, name.ljust(14, b'\\x00'))`.
    The 14-byte width matches the HMI typebyte 0x1e objname field. The padding
    is significant: hashing the unpadded ASCII gives a different value (e.g.
    `hash_objname("xixr") = 0xff6ddc1f` vs `crc32_bytewise(0xffffffff, b"xixr")
    = 0x00067955`). Names ≥ 14 bytes are truncated.

    Verified against `/tmp/collision.tft` and `/tmp/xixr.tft`:
    `page0 -> 0xac967926, xixr -> 0xff6ddc1f, w621q -> 0xd1e1feb9,
    x1 -> 0x08c28c7b`.
    """
    if isinstance(name, str):
        name = name.encode("ascii")
    if len(name) > OBJNAME_FIELD_SIZE:
        name = name[:OBJNAME_FIELD_SIZE]
    else:
        name = name + b"\x00" * (OBJNAME_FIELD_SIZE - len(name))
    return crc32_bytewise(0xFFFFFFFF, name)


def crc32_T(seed: int, data: bytes) -> int:
    """4-byte-block variant of the Nextion CRC mixing kernel
    (``CRC32_T`` at ``achmi.dll!.text:0x10007990``).

    Same polynomial and table as :func:`crc32_bytewise`, but each
    iteration XORs a full little-endian u32 word into the running CRC,
    then runs the same four mixing rounds.

    The native routine requires the data pointer and length to be
    4-byte aligned; if not, it returns the seed unchanged.

    Used by the HMI top-level directory checksum (combined with the
    ``"ADEC"`` sentinel — see :func:`directory_checksum`).
    """
    if len(data) % 4 != 0:
        return seed & 0xFFFFFFFF
    r = seed & 0xFFFFFFFF
    for i in range(0, len(data), 4):
        dword = int.from_bytes(data[i:i + 4], "little")
        r ^= dword
        for _ in range(4):
            r = ((r << 8) & 0xFFFFFFFF) ^ TAB256[(r >> 24) & 0xFF]
    return r


# 4-byte constant XORed into the directory checksum after the entry
# table. ASCII ``"ADEC"``, baked into ``achmi.dll`` at
# ``.rdata:0x1001dbc4``.
DIRECTORY_CRC_SENTINEL = b"ADEC"


def directory_checksum(directory_bytes: bytes) -> int:
    """Compute the 4-byte checksum the editor stores immediately after
    the HMI directory entries (at file offset ``4 + count * 28``).

    ``directory_bytes`` must be the contiguous bytes of
    ``(u32 count) + (count × 28-byte entries)``. The result is
    :func:`crc32_T` over those bytes plus a 4-byte ``"ADEC"`` sentinel
    (the same constant the native code reads from ``.rdata:0x1001dbc4``).

    Verified against the baseline, 07_add_hotspot, and miata-dash
    HMI fixtures — every stored checksum reproduces.
    """
    return crc32_T(0xFFFFFFFF, directory_bytes + DIRECTORY_CRC_SENTINEL)


def page_crc(blob: bytes) -> int:
    """Return the 32-bit CRC stored at page[0:4] for a `*.pa` blob.

    Inputs the *full* page bytes (including the leading 4-byte CRC field —
    the function ignores those and re-derives the value).
    """
    if len(blob) < 0x38:
        raise ValueError(f"page blob too short: {len(blob)} bytes (need >= 56)")
    crc = crc32_bytewise(0xFFFFFFFF, blob[4:])
    crc = crc32_bytewise(crc, blob[4:8])
    crc = crc32_bytewise(crc, blob[0xc:0x10])
    crc = crc32_bytewise(crc, blob[0x14:0x15])
    crc = crc32_bytewise(crc, blob[0x15:0x16])
    return crc


def verify(blob: bytes) -> bool:
    stored = struct.unpack_from('<I', blob, 0)[0]
    return page_crc(blob) == stored


def patch_crc(blob: bytes) -> bytes:
    """Return a copy of *blob* with the CRC at offset 0 fixed."""
    new_crc = page_crc(blob)
    return struct.pack('<I', new_crc) + blob[4:]


# ---------- self-test ----------

def _self_test() -> int:
    """Sanity check against the baseline editor capture."""
    repo_root = Path(__file__).resolve().parents[2]
    base_hmi = repo_root / "tests" / "editor outputs" / "00_baseline" / "base.HMI"
    if not base_hmi.exists():
        print(f"[skip] baseline HMI not found at {base_hmi}")
        return 0

    data = base_hmi.read_bytes()
    count = struct.unpack_from('<I', data, 0)[0]
    fails = 0
    for i in range(count):
        off = 4 + i * 28
        name = data[off:off+16].rstrip(b'\x00').decode('ascii', errors='replace')
        start = struct.unpack_from('<I', data, off+16)[0]
        size = struct.unpack_from('<I', data, off+20)[0]
        deleted = data[off+24]
        if deleted or not name.endswith('.pa'):
            continue
        blob = data[start:start+size]
        stored = struct.unpack_from('<I', blob, 0)[0]
        computed = page_crc(blob)
        ok = stored == computed
        flag = 'OK' if ok else 'FAIL'
        print(f"  [{flag}] #{i:2d} {name:8s} size=0x{size:04x}  "
              f"stored=0x{stored:08x}  computed=0x{computed:08x}")
        if not ok:
            fails += 1
    return fails


if __name__ == "__main__":
    raise SystemExit(_self_test())
