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


def extract_xfloat_records(data: bytes) -> list[dict]:
    """Decode the leading run of XFloat records from the per-component
    records region.

    The region starts at the first `de ff 01 01` after `strdataaddr`
    (pco/sta/font signature) and runs until the text-slot region. Each
    XFloat record is 24 bytes:

    +0   bco (u16)
    +2   pco (u16)
    +4   sta (u8)
    +5   font (u8)
    +6   val (u32)
    +10  ... 14 bytes of additional fields (movex/movey/maxval/etc.)

    Other component types (ProgressBar=32B, etc.) live in the same
    region with different record sizes; this function walks the leading
    run of consecutive 24-byte records that start with the expected
    `<bco> <pco> 01 01` shape, stopping at the first record that
    doesn't match. So for `source/nextion.hmi.tft` it returns the six
    XFloats (x0..x5) before j0 cleanly. Per-type dispatch for the rest
    is a follow-up.
    """
    from scripts.h2_cipher import encrypt as h2_decrypt
    if len(data) < H2_END:
        return []
    model_crc = struct.unpack_from("<I", data, APPINF0_MODELCRC_OFF)[0]
    plain = h2_decrypt(data[H2_START:H2_END], model_crc)
    strdataaddr = struct.unpack_from("<I", plain, 0x14)[0]

    sig = b"\xde\xff\x01\x01"   # pco=0xffde, sta=1, font=1 — XFloat signature
    first = data.find(sig, strdataaddr)
    if first < 0:
        return []
    records = []

    # Find the records region's outer bounds. End at first text-slot
    # marker (`01 01 00 <ASCII>`), which sits right after the records.
    n = len(data) - 4
    region_end = n
    for i in range(strdataaddr, n - 4):
        if (data[i:i + 3] == b"\x01\x01\x00"
                and 32 <= data[i + 3] < 127 and data[i + 4] != 0):
            region_end = i
            break

    # Walk forward by 24 bytes. When the signature doesn't match, skip
    # forward by 8 bytes and try again (handles the 32-byte ProgressBar
    # record interrupting an otherwise-uniform run of XFloat records).
    off = first - 2
    while off + 24 <= region_end:
        if data[off + 2:off + 6] != sig:
            off += 8
            continue
        bco = struct.unpack_from("<H", data, off)[0]
        pco = struct.unpack_from("<H", data, off + 2)[0]
        sta = data[off + 4]
        font = data[off + 5]
        val = struct.unpack_from("<I", data, off + 6)[0]
        vvs0 = data[off + 10]
        vvs1 = data[off + 11]
        records.append({
            "bco": bco, "pco": pco, "sta": sta, "font": font, "val": val,
            "vvs0": vvs0, "vvs1": vvs1,
            "_off": off,
        })
        off += 24
    return records


def extract_slider_records(data: bytes) -> list[dict]:
    """Find Slider (type=1) records.

    The Slider record sits in a per-page records region (the
    `attdataaddr` region for that page). Layout (16 bytes):

        +0  bco (u16)     +2  pco (u16)
        +4  val (u16)     +6  maxval (u16)
        +8  minval (u16)  +10 ch (u16)
        +12 ... 4 more bytes

    Detection: search file body for a 12-byte sequence where: `bco`
    matches the page bco extracted via `extract_page_bco`, `pco !=
    0xffde` (XFloat default), `minval < maxval`, and
    `minval <= val <= maxval`.

    To avoid matching inside the resources/page-bytecode regions,
    restrict the scan to the strdataaddr region (after init bytecode
    and per-page records start).

    Returns one dict per match in file order.
    """
    from scripts.h2_cipher import encrypt as h2_decrypt
    if len(data) < H2_END:
        return []
    model_crc = struct.unpack_from("<I", data, APPINF0_MODELCRC_OFF)[0]
    plain = h2_decrypt(data[H2_START:H2_END], model_crc)
    strdataaddr = struct.unpack_from("<I", plain, 0x14)[0]

    page_bco = extract_page_bco(data) or 0x2946
    out = []
    n = len(data) - 4
    i = strdataaddr
    while i < n - 12:
        bco = struct.unpack_from("<H", data, i)[0]
        if bco != page_bco:
            i += 1
            continue
        pco = struct.unpack_from("<H", data, i + 2)[0]
        val = struct.unpack_from("<H", data, i + 4)[0]
        maxval = struct.unpack_from("<H", data, i + 6)[0]
        minval = struct.unpack_from("<H", data, i + 8)[0]
        ch = struct.unpack_from("<H", data, i + 10)[0]
        if (0 < pco < 0xfffe and pco != 0xffde
                and pco != page_bco
                and 0 < maxval < 0x8000
                and minval < maxval
                and minval <= val <= maxval
                and 0 < ch < 256):
            out.append({
                "bco": bco, "pco": pco, "val": val,
                "maxval": maxval, "minval": minval, "ch": ch,
                "_off": i,
            })
            i += 12
            continue
        i += 1
    return out


def extract_progressbar_records(data: bytes) -> list[dict]:
    """Find ProgressBar (type=106) records in the per-component records
    region.

    Strategy: ProgressBar records sit in *gaps* between XFloat records
    (XFloats walk in stride 24, ProgressBars insert at points where
    that stride breaks). Use `extract_xfloat_records` to find the
    record offsets, then search only in the gaps for the pattern:

        <val u8 in 0..100>  <flag u8>  <bco u16>  <pco u16>

    with `pco != 0xffde` (XFloat default), `bco` non-trivial, and a
    short zero-byte run before the val (alignment padding).

    Empirically val is in 0..100 (percent-fill). bco/pco are the bar's
    background and fill colors. Returns one dict per ProgressBar found.
    """
    xf_recs = extract_xfloat_records(data)
    if not xf_recs:
        return []
    out = []
    # Look immediately AFTER each XFloat record for an inserted PB.
    for i, rec in enumerate(xf_recs):
        gap_start = rec["_off"] + 24
        gap_end = (xf_recs[i + 1]["_off"]
                   if i + 1 < len(xf_recs) else gap_start + 16)
        if gap_end - gap_start < 8:
            continue
        # PB record sits at gap_start - 4 (overlapping x5's tail) or in
        # the gap proper. Try the gap first.
        for j in range(max(gap_start - 4, 0), gap_end - 8):
            v = data[j]
            if not (0 <= v <= 100):
                continue
            bco = struct.unpack_from("<H", data, j + 2)[0]
            pco = struct.unpack_from("<H", data, j + 4)[0]
            if not (0 < bco < 0xfffe and 0 < pco < 0xfffe and pco != 0xffde):
                continue
            # Require ≥3 leading zero bytes for confidence.
            if j >= 4 and data[j - 4:j - 1] != b"\x00\x00\x00":
                continue
            out.append({
                "val": v, "bco": bco, "pco": pco,
                "sta": data[j + 6], "font": data[j + 7], "_off": j,
            })
            break
    return out


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


def extract_zi_fonts(data: bytes) -> dict[int, "ZiFont"]:
    """Extract embedded ZI fonts from the TFT.

    The TFT stores fonts at `appinf1.zimoxinxiadd` with the layout:

        zimoxinxiadd                — start of font headers
        + 0..44, +44..88, ...        — N × 44-byte font headers
        + 88..                       — first font's name + glyph data
        + ... etc                    — subsequent fonts' name + glyph data

    Each header's `data_start` field at offset 24 is the **offset from
    `zimoxinxiadd`** (not from the header's own start) to where that
    font's name + glyph data begin. The font's binary layout is
    identical to a standalone HMI `*.zi` file once you splice the
    44-byte header onto the name+glyph data with `data_start` rewritten
    to 44 (the header size, where the name immediately follows).

    Returns a dict keyed by font id (0, 1, ...) with parsed `ZiFont`
    instances, or an empty dict on failure.
    """
    from scripts.h2_cipher import encrypt as h2_decrypt
    from sim.font import parse_zi
    if len(data) < H2_END:
        return {}
    model_crc = struct.unpack_from("<I", data, APPINF0_MODELCRC_OFF)[0]
    plain = h2_decrypt(data[H2_START:H2_END], model_crc)
    zimoxinxiadd = struct.unpack_from("<I", plain, 0x30)[0]
    zimoqyt = struct.unpack_from("<H", plain, 0x44)[0]
    strdataaddr = struct.unpack_from("<I", plain, 0x14)[0]
    if zimoxinxiadd == 0 or zimoqyt == 0:
        return {}

    # Get each font's data_start (offset from zimoxinxiadd to its
    # name+glyph block). Add a sentinel for the end of the last font's
    # data — we cap it at `strdataaddr`, which is the next major region.
    data_starts = []
    for n in range(zimoqyt):
        hdr_off = zimoxinxiadd + n * 44
        if hdr_off + 44 > len(data):
            return {}
        ds = struct.unpack_from("<I", data, hdr_off + 24)[0]
        data_starts.append(ds)
    data_starts.append(strdataaddr - zimoxinxiadd)

    fonts = {}
    for n in range(zimoqyt):
        hdr_off = zimoxinxiadd + n * 44
        name_off = zimoxinxiadd + data_starts[n]
        next_off = zimoxinxiadd + data_starts[n + 1]
        if name_off >= next_off or next_off > len(data):
            continue

        # Detect the printable-ASCII name length empirically; the
        # `desc_len` byte at +17 is sometimes off-by-one from the actual.
        name_end = name_off
        while (name_end < next_off
               and name_end - name_off < 64
               and 32 <= data[name_end] < 127):
            name_end += 1
        name_bytes = bytes(data[name_off:name_end]).rstrip(b" ")
        if not name_bytes:
            continue
        glyph_start = name_off + len(name_bytes)
        glyph_data = data[glyph_start:next_off]

        # Reconstruct the standalone ZI: 44-byte header (with data_start
        # rewritten to 44 so the parser puts the name immediately after
        # the header) + name + glyph data.
        header = bytearray(data[hdr_off:hdr_off + 44])
        struct.pack_into("<I", header, 24, 44)
        header[17] = len(name_bytes)
        reconstructed = bytes(header) + name_bytes + glyph_data
        try:
            font = parse_zi(reconstructed)
        except Exception:
            continue
        fonts[n] = font
    return fonts


def extract_text_colors(data: bytes, slot_offset: int, comp_type: int) -> dict:
    """For a given text-slot file offset (the position of the first text
    byte, as returned by `extract_text_slots`), pull the bco/pco
    metadata that precedes the slot.

    Layout depends on the component type:

    * **Text (116) / ScrollingText (55)** — 4-byte prefix immediately
      before the `01 01 00` marker:

          ... <pco u16> <bco u16> 01 01 00 <text>\\0

    * **Button (98)** — wider prefix carrying both normal and pressed
      colors:

          ... <bco u16> <bco2 u16> <pco u16> <pco2 u16> 01 01 00 <text>\\0

    Returns a dict of attrs to merge into the component (`bco`, `pco`,
    plus `bco2`/`pco2` for Buttons). Returns `{}` if the offset is too
    close to the start of the file to read the prefix.
    """
    if comp_type == 98:    # Button
        if slot_offset < 11:
            return {}
        return {
            "bco":  struct.unpack_from("<H", data, slot_offset - 11)[0],
            "bco2": struct.unpack_from("<H", data, slot_offset - 9)[0],
            "pco":  struct.unpack_from("<H", data, slot_offset - 7)[0],
            "pco2": struct.unpack_from("<H", data, slot_offset - 5)[0],
        }
    # Text / ScrollingText: 4-byte pco/bco prefix.
    if slot_offset < 7:
        return {}
    return {
        "pco": struct.unpack_from("<H", data, slot_offset - 7)[0],
        "bco": struct.unpack_from("<H", data, slot_offset - 5)[0],
    }


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
