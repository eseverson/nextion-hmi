# Path D — Page raster extraction

**TL;DR:** `source/nextion.hmi.tft` contains zero pre-rendered page rasters
or picture assets. The dashboard is rendered entirely procedurally from
`fill` rectangles, text from `.zi` fonts, and a progress-bar component.
There is therefore nothing to extract to PNG. The exploration script
(`scripts/extract_pages.py`) handles the general case (would emit PNGs for
any embedded RGB565 image it finds) and prints a labelled inventory of
every resource entry in the TFT.

## Resource section layout

The TFT (`504,784` bytes total, model `NX4832F035`, 480×320 panel rotated
to 320×480) is laid out, per file header 1, as:

| File offset | Size  | Section                                  |
|-------------|-------|------------------------------------------|
| `0x00000`   | `0x10000` | File headers (header 1 + encrypted header 2) |
| `0x10000`   | `0x60000` | **Resources section** (bootloader + drivers + fonts) |
| `0x70000`   | rest      | Compiled user code |
| end         | 4         | File CRC |

Header 1 fields (verified):
- `lcd_resolution_x = 320`, `lcd_resolution_y = 480` (rotated for vertical mount)
- `ressources_files_address = 0x10000`
- `ressource_files_size     = 0x60000`
- `ressources_files_count   = 12`

## Resources directory

The first 144 bytes of the resource section (i.e. file `0x10000..0x10090`)
form a fixed 12 × 12-byte directory, with 8 non-empty slots. Each slot is
`(rel_offset:u32, size:u32, reserved:u32)` where `rel_offset` is relative
to the resource section start. After dumping every slot to
`work/res_NN_<kind>.bin`:

| #  | file offset | size      | content (sniffed)                    |
|----|-------------|-----------|--------------------------------------|
| 0  | `0x010090`  |    17,570 | rest of bootloader header (driver tables, GB2312 index) |
| 1  | `0x014532`  |   243,128 | STM32 firmware blob (Nextion bootloader) |
| 2  | `0x04faea`  |    24,380 | LCD driver code |
| 3  | `0x055a26`  |     1,850 | small lookup table — 16-bit entries, font glyph index? |
| 4  | `0x056160`  |     1,587 | bootloader strings (ASCII: "Please re download…") + small index |
| 5  | `0x056793`  |     3,298 | (offset, size) table → ARM Thumb code chunks |
| 6  | `0x057475`  |     5,432 | same shape as #5 — more driver code chunks |
| 7  | —           |         0 | empty slot |
| 8  | `0x0589ad`  |    50,969 | concatenation of `liber-font.zi` (13,899 B) + `liber-48.zi` (37,070 B) |

Slot 8's bytes match the project's two `.zi` font files concatenated;
beyond a small identifier word at offset `0x1c..0x20` the contents are
byte-identical to the source `.zi` files in the firmware repo.

**There is no slot of "picture" or "image" type.** No slot has a size that
matches any plausible RGB565 image (480·320·2 = 307,200; nothing else is
even within 2× of that).

## Why no rasters?

Cross-checked against the HMI source via `tools/Nextion2Text/Nextion2Text.py`:

- 4 pages: `main`, `gauge`, `settings`, `error`
- 39 components total, none of them a `Picture` or `Crop` component
- Page `main` uses: 8 `Variable` (color slots), 9 `XFloat`, 9 `Text`, 1
  `Progress Bar`, 1 `Hotspot`, 1 `Timer` — all vector/text rendering

In the user-code section (`0x70000..0x7B3CC`):

| opcode (Basic-series numerated_operators[4]) | encoding | occurrences |
|----------------------------------------------|----------|-------------|
| `pic`                                        | `09 01 04` | **0** |
| `xpic`                                       | `09 0a 04` | **0** |
| `picq`                                       | `09 0f 04` | **0** |
| `fill`                                       | `09 0d 04` | 33 |
| `page`                                       | `09 0b 04` | 6 |

Confirms procedural rendering — every page fills its background with a
rect and then draws components on top.

## Pixel format note (theoretical)

If this TFT had pictures, the format would be 16-bit RGB565
little-endian (the Nextion native pixel format used by `pic`/`picq`
opcodes). Per the Nextion docs all colour constants in the .HMI use
RGB565 — e.g. the `bco`/`pco` values in `main` page are stored as 5-bit/
6-bit/5-bit packed integers (`bco=10566 = 0x2946`, etc). A future TFT
that does include pictures should decode with `decode_rgb565()` in the
script as-is.

## Compression

N/A for this file (no images). For reference, third-party documentation
says Nextion stores T0/K0/F-series pictures uncompressed RGB565 in the
"pictures" sub-block of the resources section (between `pictures_address`
and `gmovs_address` per header 2). The Discovery/Intelligent series
optionally use a custom compressed format; we did not encounter any
compressed asset in this file.

## Header 2 caveat

TFTTool can't decode header 2 for the `NX4832F035_011` model — the XOR
key is unknown (set to `0` placeholder in `TFTTool.py`). Header 1 alone
provided everything needed for this analysis (resources address + size).
The resource directory is unencrypted and fully parseable.

## Outputs

- Script: [`scripts/extract_pages.py`](../scripts/extract_pages.py)
- Run: `python3 scripts/extract_pages.py`
- Dump every resource to `work/res_NN_<kind>.bin`:
  `python3 scripts/extract_pages.py --dump-bins`
- All output goes to `work/` (gitignored). PNGs will be written to
  `work/page_NN.png` whenever a future/different TFT does contain rasters.

## What I tried but didn't pursue

- Procedural page replay: walking the user-code blocks for each `page N`
  routine and executing `fill`/text/progress-bar draws into a virtual
  framebuffer. Doable but non-trivial — would need a partial Nextion VM.
  Out of scope for path D ("extract pre-rendered imagery"). Flagged here
  in case a future path picks it up.
- Header 2 XOR-key recovery for F-series: tried using known constraints
  (e.g. `ressources_files_address` should equal header 1's `0x00010000`)
  but the resulting key didn't yield sensible values for the rest of
  header 2. Leaving for path A or a future session — unrelated to D.
