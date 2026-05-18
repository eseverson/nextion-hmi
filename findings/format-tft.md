# TFT file format

The `.TFT` file is the compiled runtime artifact the editor produces
from a `.HMI` project. The Nextion device runs this file directly:
panel firmware, fonts, picture/video resources, compiled page layouts,
and event-script bytecode are all packed into the same image.

This document focuses on the **F-series** (T1 / Discovery /
Intelligent) family. The T0/K0 layout is similar but differs in
several specific fields; TFTTool's schema documents T0/K0 well and is
incomplete for F-series.

## File-level layout

```
0x00000   appinf0 (Header 1, "H1")           plaintext, 196 B
0x000C4   H1 CRC (CRC-32/MPEG-2 of H1)        4 B
0x000C8   appinf1 (Header 2, "H2") + trailing 196 B total, encrypted
0x0018C   H2 CRC (CRC-32/MPEG-2 of H2 region) 4 B
0x00190   body (resources / pages / fonts / usercode / etc.)
EOF-4     file CRC (CRC-32/MPEG-2 of file[:-4]) XOR'd
```

The body itself is partitioned per `appinf0`/`appinf1` address fields
into:

```
0x10000   resources section   (bootloader + drivers + fonts + pictures)
          length = ressources_files_size, default 0x60000
0x70000   usercode             (compiled bytecode + global memory directory)
          end at H1+0x3c (file_size)
```

## Header 1 (`appinf0`, 196 bytes plaintext)

```
+0x00   u8    old_lcd_orientation
+0x01   u8    editor_version_main      (e.g. 1)
+0x02   u8    editor_version_sub       (e.g. 67)
+0x03   u8    editor_vendor            (ASCII 'N' or 'T')
+0x10   u16   lcd_resolution_x
+0x12   u16   lcd_resolution_y
+0x14   u8    ui_orientation           (see Orientation, below)
+0x16   u8    model_series             (must be 0/1/2/3/100 to validate)
+0x1B   u8    editor_version_bugfix
+0x2E   u32   model_crc                (CRC of model name string)
+0x32   u8    file_version
+0x3C   u32   file_size                (LE; equals total bytes on disk)
+0x37..0x47   resources_files_*        (address/size/count tuple)
+0xC0..0xC4   misc unknowns            (driver/binary addresses, file_id, metadata_size)
+0xC4   u32   H1 CRC (this header's own CRC, at end of H1)
```

H1 is mutable byte-for-byte and any well-formed value passes runtime
validation. CRCs at H1+0xC4 (over `[0..0xC4]`) and the trailing
whole-file CRC must be recomputed after any change.

Notable fields:

- **`file_size` (H1+0x3c)** — `u32 LE`. Increments by exactly the byte
  delta on every save. Must equal `len(file)`.
- **`ui_orientation` (H1+0x14)** — see "Orientation" below.
- **`model_series` (H1+0x16)** — `0=T0`, `1=K0`, `2=X3`, `3=X5`,
  `100=T1/F-series`. Values outside this set are rejected.
- **`model_crc` (H1+0x2e)** — CRC of the canonical model name (e.g.
  `NX4832F035_011`). Determines which device firmware the file is
  paired with.

## Header 2 (`appinf1`, 196 bytes encrypted)

The encrypted region is 196 bytes long; the `appinf1` struct itself is
76 bytes (`= 0x4c`). After decryption, layout per `hmitype.dll!appinf1`:

```
+0x00   u32   staticstrBeg              (start of static-string region)
+0x04   u32   AppAllvasAddr             (global variables address;
                                          strdata-relative — see below)
+0x08   u32   AppAllvasQty              (global variables count)
+0x0c   u32   attdataaddr               (page-attribute records region)
+0x10   u32   resourcesfileddr          (=0x10000)
+0x14   u32   strdataaddr               (=0x80000)
+0x18   u32   pageadd                   (per-page xinxi records)
+0x1c   u32   objxinxiadd               (per-component xinxi records)
+0x20   u32   picxinxiadd               (picture records)
+0x24   u32   gmovxinxiadd
+0x28   u32   videoxinxiadd
+0x2c   u32   wavxinxiadd
+0x30   u32   zimoxinxiadd              (ZI font records)
+0x34   u32   MainCodeHex               (main-code hash)
+0x38   u16   pageqyt
+0x3a   u16   objqyt
+0x3c   u16   picqyt
+0x3e   u16   gmovqyt
+0x40   u16   videoqyt
+0x42   u16   wavqyt
+0x44   u16   zimoqyt
+0x46   u16   res1
+0x48   u8    encode
+0x49   u8    res2
+0x4a   u16   res3
```

The trailing 120 bytes (`H2[0x114..0x18c]`) past the struct are
project-specific fingerprint data covered by the same CRC. They
decrypt to plausibly-structured content; partial decoding is in
[`h2-cipher.md`](h2-cipher.md).

Earlier writeups labelled the address fields with TFTTool's T0/K0 names
(`pictures_address` etc.) and put counts at `0x30..0x3F` as u32 fields;
that's wrong on F-series — counts are u16 starting at `+0x38` and the
address slots above are distinct. Source of truth is
`hmitype.dll!appinf1` as decompiled in
[`achmi-internals.md`](achmi-internals.md).

### AppAllvas — global scalar name table

`AppAllvasAddr` is a strdata-relative pointer. The table is
`AppAllvasQty` entries × 12 bytes:

```
+0   u32   name_hash      # crc32_bytewise(name)  — the page-CRC kernel
                          # over the global's ASCII name, e.g. "sys0"
+4   u32   offset         # byte offset of the global within the
                          # global-variable memory area (sys0=0, sys1=4,
                          # sys2=8, plus 4 bytes per user-declared int)
+8   u32   type           # type code; 9 (= attlei nibble for SS32) for
                          # `int` globals — only type observed so far
```

For both miata-dash and `17_more_components` the table is byte-identical:

```
[0]: fb ba 73 d0  08 00 00 00  09 00 00 00   sys2  @ +8  int
[1]: 95 81 f1 d9  00 00 00 00  09 00 00 00   sys0  @ +0  int
[2]: 22 9c 30 dd  04 00 00 00  09 00 00 00   sys1  @ +4  int
```

The hashes match `crc32_bytewise(0xFFFFFFFF, name.encode("ascii"))`
for `sys2`/`sys0`/`sys1`. Insertion order is not alphabetical — likely
a hash-bucket order or declaration order; the runtime almost
certainly looks them up by linear scan since `AppAllvasQty` is small
in real projects.

Both projects include the 3 implicit `sys0/sys1/sys2` declarations
from `Program.s`. User-declared `int foo=0` globals would extend the
table; we don't yet have a fixture with explicit user globals to
verify.

`AppAllvas` is NOT the component-name resolver — component names like
`x0` or `m0` don't appear here, by hash or otherwise. The mechanism
the runtime uses to resolve serial commands like `x9.val=42` is still
opaque; see [`next-steps.md`](next-steps.md) "Last blocker".

H2 encryption details and the cipher implementation live in
[`h2-cipher.md`](h2-cipher.md). H2 is fully decryptable and
re-encryptable today.

The H2 CRC at `0x18C..0x190` is CRC-32/MPEG-2 over the encrypted
region `[0xC8..0x18C]` (196 bytes). Recompute on every write.

## Orientation

| H1+0x14 | Orientation | Effect on compilation                                  |
|---------|-------------|--------------------------------------------------------|
| `0x01`  | 0° (default) | none                                                  |
| `0x00`  | 90°         | coordinate literals rebaked to swapped aspect          |
| `0x03`  | 180°        | runtime-only flip; bytecode unchanged                  |
| `0x02`  | 270°        | predicted to rebake like 90°; untested                 |

90°/270° rotation flips the screen aspect ratio, so the editor rewrites
component coordinates. 180° preserves the aspect ratio, so it's applied
as a render-time hint only.

## File CRC (trailing 4 bytes)

```
crc = CRC32_MPEG2(file[:-4])
crc ^= file[0x03]                # editor_vendor
crc ^= file[0x2e]                # low byte of model_crc
crc ^= file[0x3c]                # low byte of file_size
file[-4:] = crc.to_bytes(4, 'little')
```

Variant by model series: `0/1/100` use byte-wise CRC over `file[:-4]`;
series `2/3` use a word-wise variant padded to 4-byte multiples.

## Body sections

### Resources section (0x10000..0x70000)

The first 144 bytes form a 12-slot resource directory; each slot is
`(rel_offset:u32, size:u32, reserved:u32)`. Empty slots have all-zero
fields. Slot kinds observed:

| Slot | Typical content                                              |
|------|--------------------------------------------------------------|
| 0    | Rest of bootloader header (driver tables, GB2312 index)      |
| 1    | STM32 firmware blob (Nextion bootloader, packed)             |
| 2    | LCD driver code                                              |
| 3..6 | Smaller code/data tables (driver helpers, font glyph index)  |
| 7    | (unused in observed projects)                                |
| 8    | Concatenated `.zi` fonts (project's font set, byte-for-byte) |

The pictures sub-block sits between `pictures_address` and
`gmovs_address`. RGB565 LE images are stored uncompressed on
F-series; Picture component records reference them by index. The
slot-kind decoder in [`scripts/lib/tft_format.py`](../scripts/lib/tft_format.py)
walks every section.

### Usercode section (0x70000..file_size − 4)

The usercode region holds, in order:

1. **Global memory directory** at offset 0 (TLV-like layout; first u32
   is its total size in bytes, subsequent u32s look like
   `(offset, size)` or `(offset, count)` tuples that grow when locals
   are added).
2. **Per-page init bytecode**, addressable via `appinf1.usercode_address`
   + page-specific offsets.
3. **Per-component init bytecode**, each block addressed via the
   component's `PianyiData[+0x34]` u32. See
   [`format-bytecode.md`](format-bytecode.md) for the opcode and block
   layout.
4. **Flat attribute-value region** that holds attribute values for
   types whose data didn't fit in a fixed-stride record (colours as
   raw u16, integers as raw u32, strings as null-terminated latin-1).

### Component records

Per-component on-disk stride on F-series is **52 + 180 = 232 bytes**:

- **`objdata_Ram` (52 bytes, fixed layout)**:
  - `objType u8, id u8, merry u8, objstate u8`
  - `6 × u32` event slots
  - `memorypos u32, move u32, sendkey u32, aph u32, regaddr u32`
  - `movex/movey/x/y/w/h/endx/endy` (8 × u16)

  This is enough to recover **type, id, x, y, w, h, events** for every
  component on every page directly from the TFT.

- **`PianyiData` (180 bytes, type-specific)** — contains a packed table
  of attribute IDs that index into the 82-entry `xilie.AppAttNames`
  table. Field byte locations of the values themselves vary by
  component type:

  | Type             | Storage                                                |
  |------------------|--------------------------------------------------------|
  | XFloat (59)      | 24-byte record starting at first `de ff 01 01` sig     |
  | Slider (1)       | 16-byte record in per-page attdataaddr region          |
  | Progress bar (106)| non-uniform record between XFloats                    |
  | Variable (52)    | u32 array following `90 01 01 00` marker               |
  | Text / Button    | embedded in text-slot region (see below)               |
  | exotic types     | init bytecode references attribute IDs only            |

  Text slot prefixes:
  - **Text / ScrollingText**: `<pco u16> <bco u16> 01 01 00 <text>\0`
  - **Button**: `<bco u16> <bco2 u16> <pco u16> <pco2 u16> 01 01 00 <text>\0`

  Filter false-positive text matches by requiring ≥3 printable ASCII
  characters; the colour bytes occasionally spell short ASCII pairs.

For exotic component types (Waveform=0, CropPicture=5,
DualStateButton=53, ScrollingText=55, Checkbox=56, Radio=57, QRCode=58,
Gauge=122, type=113), the runtime attribute values live in the init
bytecode rather than a fixed-stride record. Recovering them requires
the disassembler in [`format-bytecode.md`](format-bytecode.md) and is
still partial.

## TFTTool limitations on F-series

TFTTool can read T0/K0 TFTs losslessly but breaks F-series:

- Its `_modelXORs[NX4832F035_011] = 0`, so its "decryption" is a no-op.
  Decoded header 2 contains garbage addresses.
- On save, it overwrites `H2+0x44..0xC4` with `0xFF` (treating the
  unmodelled region as padding). This destroys real fingerprint data
  on every round-trip.
- It treats `H2+0x44` as the H2 content-end; on F-series that's wrong.

For F-series, recompute CRCs manually and use
[`scripts/lib/h2_cipher.py`](../scripts/lib/h2_cipher.py) for H2.
[`scripts/tools/patch_tft.py`](../scripts/tools/patch_tft.py) implements a working
in-place patcher that keeps every invariant correct.

## What's lossless to edit today

In-place edits that recompute the three CRCs (H1, H2, file) and keep
`file_size` consistent:

- Any H1 field (editor version, orientation, resolution, file_version,
  model swap, file_size adjustments).
- Any H2 field (full read-write parity).
- Body fields where the byte location is known:
  - Variable `val` (u32 LE at the variable's record)
  - Text `txt` (same-length swaps trivial; different length requires
    knowing the length-prefix encoding — see open question H12)
  - `bco`/`pco` on located components
  - Slider `maxval`/`minval`/`val`/`ch`
  - XFloat `vvs0`/`vvs1`/`val`
- ZI font table entries (location/size in the resource directory).
- Adding/removing a tombstoned component.

## What is NOT yet feasible

- Authoring a TFT from scratch — the per-component attribute-record
  value table that the bytecode references is only partially mapped.
- Editing attributes on exotic component types (Waveform / CropPicture
  / Checkbox / Radio / QRCode / Gauge / DualStateButton / type 113):
  their values live in the per-component init bytecode and the value
  decoder isn't built yet.
- Generating new event-handler bytecode from scratch — opcodes are all
  decoded ([`format-bytecode.md`](format-bytecode.md)) but no encoder
  exists.

See [`next-steps.md`](next-steps.md) for the prioritised path to close
those gaps.
