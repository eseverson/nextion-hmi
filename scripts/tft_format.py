"""tft_format — F-series TFT layout primitives.

Constants, struct definitions, and helpers shared by readers, writers, and
the simulator's TFT loader. The format was reverse-engineered from
achmi.dll (subcommand 0x21 = H2 cipher, subcommand 0x23 = trailing CRC,
subcommand 0x27 = page CRC) and hmitype.dll's `Myapp_inf.OutPutPageFile`
writer; see `findings/R-editor-unpacking.md` for the deep dive.

A valid F-series TFT (xiliemark=100) has three integrity layers — the H1
CRC, the H2 CRC, and a file-wide trailing CRC. The H1 region (bytes
0..0xc4) is plaintext; the H2 region (0xc8..0x18c) is encrypted with the
stateful cipher in `scripts.h2_cipher`. Both CRCs and the H2 cipher are
seeded by `ModelCRC`, a 32-bit value at `appinf0.ModelcrcL/H` (file
offset 0x2e).

The trailing CRC at `len(file) - 4` is the file-wide bytewise CRC32-MPEG2
XORed with three metadata bytes pulled from globals at write time
(achmi.dll subcmd 0x23). For *patching* (mutating an existing TFT) we can
preserve the original XOR mask, so the resulting trailing CRC stays
correct without needing to know what the metadata bytes were.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass


# Region offsets (constant across all F-series TFTs).
H1_START   = 0x000
H1_END     = 0x0c4    # H1 region: 196 bytes plaintext
H1_CRC_OFF = 0x0c4    # 4-byte CRC of H1
H2_START   = 0x0c8
H2_END     = 0x18c    # H2 encrypted region: 196 bytes (76 are appinf1; rest is 0xff padding)
H2_CRC_OFF = 0x18c    # 4-byte CRC of H2 ciphertext
RESOURCES_START = 0x190

H1_SIZE = H1_END - H1_START   # 0xc4 = 196
H2_SIZE = H2_END - H2_START   # 0xc4 = 196 (encrypted bytes)

# In appinf0 (H1), the 32-bit ModelCRC straddles two ushorts.
APPINF0_MODELCRC_OFF = 0x2e

# appinf1 occupies the first 76 (0x4c) bytes of decrypted H2; the
# remaining 120 bytes are 0xff padding.
APPINF1_SIZE = 0x4c


def read_model_crc(data: bytes) -> int:
    """Read the 32-bit ModelCRC from the plaintext H1 region."""
    return struct.unpack_from("<I", data, APPINF0_MODELCRC_OFF)[0]


@dataclass
class TftHeader:
    """Plaintext view of the TFT header layers."""
    h1: bytes              # 196-byte plaintext appinf0 (NOT including the trailing CRC)
    h2: bytes              # 196-byte decrypted region (76-byte appinf1 + 120 bytes of 0xff)
    h1_crc: int
    h2_crc: int

    @property
    def model_crc(self) -> int:
        return struct.unpack_from("<I", self.h1, APPINF0_MODELCRC_OFF)[0]

    @property
    def appinf1(self) -> bytes:
        return self.h2[:APPINF1_SIZE]


@dataclass
class TrailingCrcInfo:
    """The trailing 4-byte CRC stores `bytewise_crc(file[:-4]) XOR mask`,
    where `mask` is the XOR of three metadata bytes determined at write
    time. For patchers we don't know which bytes — but for an existing
    file we can recover the mask by computing the body CRC and XORing
    with the stored tail."""
    stored: int
    mask: int    # `stored XOR computed_body_crc` — preserve this through edits


def parse(data: bytes) -> TftHeader:
    """Decrypt + slice the header layers from a complete TFT file."""
    from scripts.h2_cipher import encrypt as _h2_decrypt  # asm-verbatim path = decrypt
    if len(data) < RESOURCES_START + 4:
        raise ValueError(f"file too small to be a TFT: {len(data)} bytes")
    model_crc = read_model_crc(data)
    h1 = data[H1_START:H1_END]
    h1_crc = struct.unpack_from("<I", data, H1_CRC_OFF)[0]
    h2_cipher = data[H2_START:H2_END]
    h2_plain = _h2_decrypt(h2_cipher, model_crc)
    h2_crc = struct.unpack_from("<I", data, H2_CRC_OFF)[0]
    return TftHeader(h1=h1, h2=h2_plain, h1_crc=h1_crc, h2_crc=h2_crc)


def trailing_crc_mask(data: bytes) -> TrailingCrcInfo:
    """Recover the trailing-CRC XOR mask from an existing valid TFT.
    The body CRC is over `data[:-4]`; the stored value is body XOR mask."""
    from scripts.page_crc import crc32_bytewise
    stored = struct.unpack_from("<I", data, len(data) - 4)[0]
    body = crc32_bytewise(0xFFFFFFFF, data[:-4])
    return TrailingCrcInfo(stored=stored, mask=stored ^ body)


def extract_page_bco(data: bytes) -> int | None:
    """Heuristically pick the page background colour (RGB565 bco) from
    the TFT's flat data region.

    The "color records" — between the per-object init bytecode and the
    text-slot region — are 24-byte slots with bco at offset 0 and pco
    at offset 2 of each slot. In practice every project we've tested
    has all components on a page using the same bco (the page default),
    so the most-frequent 16-bit color value in that region is the page
    bco. Returns None if the region can't be located.
    """
    from scripts.h2_cipher import encrypt as h2_decrypt
    if len(data) < H2_END:
        return None
    model_crc = struct.unpack_from("<I", data, APPINF0_MODELCRC_OFF)[0]
    plain = h2_decrypt(data[H2_START:H2_END], model_crc)
    strdataaddr = struct.unpack_from("<I", plain, 0x14)[0]

    # Locate the text-slot region (`01 01 00 <ASCII>` markers); the
    # color region ends just before it.
    n = len(data) - 4
    text_region_start = None
    for i in range(strdataaddr, n - 4):
        if (data[i:i + 3] == b"\x01\x01\x00"
                and 32 <= data[i + 3] < 127 and data[i + 4] != 0):
            text_region_start = i
            break
    if text_region_start is None:
        return None

    # Walk back to find the start of the color region: a contiguous run
    # of 0xff bytes (≥16) marks the boundary between init bytecode and
    # color records.
    colors_start = strdataaddr
    i = text_region_start - 16
    while i > strdataaddr:
        if data[i:i + 16] == b"\xff" * 16:
            # Skip to the end of the 0xff run.
            j = i + 16
            while j < n and data[j] == 0xff:
                j += 1
            colors_start = j
            break
        i -= 1
    region = data[colors_start:text_region_start]
    if len(region) < 6:
        return None

    # Tally u16 LE values that appear at "looks like a bco position":
    # bytes preceded by `01 ?? 00 00` or followed by another u16 + `01 01`.
    # Simpler: just count every u16 in the region and pick the mode.
    from collections import Counter
    counts = Counter()
    for i in range(0, len(region) - 1):
        v = region[i] | (region[i + 1] << 8)
        # Ignore obvious non-color values (00 0001, ffff, control bytes).
        if v in (0x0000, 0xffff, 0x0001, 0x0100, 0x0101):
            continue
        counts[v] += 1
    if not counts:
        return None
    bco, _ = counts.most_common(1)[0]
    return bco


def extract_variable_vals(data: bytes, n_variables: int) -> list[int]:
    """Pull Variable component `val` values out of the TFT's flat data
    region.

    Variable vals are stored as a contiguous u32-LE array preceded by a
    fixed 4-byte marker `90 01 01 00`. The array length matches the
    project's Variable component count (type=52). Returns an empty list
    if the marker can't be located.
    """
    marker = bytes.fromhex("90010100")
    if len(data) < H2_END:
        return []
    # Search after H2 (no point looking inside the encrypted header).
    idx = data.find(marker, RESOURCES_START)
    if idx < 0 or idx + 4 + 4 * n_variables > len(data):
        return []
    out = []
    for i in range(n_variables):
        v = struct.unpack_from("<I", data, idx + 4 + i * 4)[0]
        out.append(v)
    return out


def extract_text_slots(data: bytes) -> list[tuple[int, str]]:
    """Heuristically pull `txt` attribute strings out of the TFT body.

    The editor packs every component's `txt` value into a flat region
    that begins after `appinf1.strdataaddr`'s init-bytecode section.
    Each slot is preceded by a 3-byte signature `01 01 00` followed
    immediately by the latin-1 string and a null terminator.

    Returns `(file_offset, text)` tuples in file order. False positives
    are possible (the signature is short and `01 01 00` can appear in
    unrelated runs), so callers should treat the list as best-effort.

    Tested against `source/nextion.hmi.tft`: recovers 12 of 12 visible
    text-component values plus one false positive (color bytes that
    happen to spell `'F)'`).
    """
    from scripts.h2_cipher import encrypt as h2_decrypt
    if len(data) < H2_END:
        return []
    model_crc = struct.unpack_from("<I", data, APPINF0_MODELCRC_OFF)[0]
    plain = h2_decrypt(data[H2_START:H2_END], model_crc)
    strdataaddr = struct.unpack_from("<I", plain, 0x14)[0]
    if strdataaddr >= len(data):
        return []
    out = []
    i = strdataaddr
    n = len(data) - 4   # skip the trailing CRC
    while i < n:
        if data[i:i + 3] == b"\x01\x01\x00":
            start = i + 3
            end = start
            # Latin-1 printable run (32..255), null-terminated, max 80 chars.
            while end < min(start + 80, n) and 32 <= data[end] < 256:
                end += 1
            # Require length ≥ 3 — shorter "matches" are almost always
            # color bytes (e.g. 0x46 0x29 reads as "F)"). Real 2-char
            # txt values are vanishingly rare in practice; we'd rather
            # miss a hypothetical "OK" than spam component lists with
            # color-byte noise.
            if end >= start + 3 and end < n and data[end] == 0:
                out.append((start, data[start:end].decode("latin-1")))
                i = end + 1
                continue
        i += 1
    return out
