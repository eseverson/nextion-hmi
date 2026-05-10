# ZI font format

The Nextion `.zi` font file holds a single bitmap font: a fixed header,
a character map, and per-glyph image data. ZI fonts are referenced by
the TFT/HMI by their integer filename prefix (e.g. `0.zi` → id 0).

Three versions are observed in the wild:

| Version | Width | Pixel depth | Per-glyph layout                              |
|---------|-------|-------------|-----------------------------------------------|
| v3      | fixed | 1bpp        | `width × height` bits packed flat MSB-first   |
| v5      | var   | 1bpp        | RLE'd stream, opcode `0x01`                   |
| v6      | var   | 1bpp or AA  | RLE'd stream, opcode `0x01` (B&W) or `0x03` (3-bit alpha) |

Reference parser: [`sim/font.py`](../sim/font.py). All three versions
are implemented and integrated into the renderer.

## File-level layout

```
+0x00   u32   magic / version          # version byte at +0x03
+0x04   u8    encoding                 # see codepage table below
+0x05   u8    mb_mode                  # 0=single-byte, 1=multi-byte
+0x06   u8    width                    # cell width
+0x07   u8    height                   # cell height
+0x08   u32   glyph_count
+0x0C   u8    klft                     # kerning left  (parsed, not yet applied)
+0x0D   u8    krht                     # kerning right (parsed, not yet applied)
+0x0E   u8    cp_first_low
+0x0F   u8    cp_last_low
+0x10   u8    cp_first_high            # 0 for single-byte codepages
+0x11   u8    cp_last_high             # 0 for single-byte codepages
+0x12   bytes name                     # null-padded ASCII display name
+0x21   u8    align8                   # v6: bit 0 = "glyph offsets are pre-divided by 8"
+0x2C   character_map[glyph_count]
+...    glyph data stream
```

### Codepage byte

| value | codepage     |
|-------|--------------|
| `0x00`| iso-8859-1   |
| `0x01`| GB2312       |
| `0x02`| GB18030      |
| `0x03`| iso-8859-1 (variant; observed)|
| `0x04`| Shift-JIS    |
| `0x05`| Big5         |
| `0x06`| KS C 5601    |

The `mb_mode` byte is **not** a reliable indicator of single vs.
multi-byte iteration. Decide based on the byte range (`cp_first_*`,
`cp_last_*`) instead.

## Character map entry (10 bytes per glyph)

```
+0x00   u8    cp_low
+0x01   u8    cp_high
+0x02   u8    width                    # glyph advance width
+0x03   u8    klft                     # per-glyph kerning
+0x04   u8    krht
+0x05   u8    flags                    # bit 7 = "use raw bitmap" (legacy)
+0x06   u24   data_offset              # offset into glyph data stream
+0x09   u16   data_length              # bytes consumed for this glyph
```

If `align8` is set in the file header, multiply `data_offset` by 8 to
get the real byte offset.

`data_length` is the byte cost of this glyph in the stream; advance the
stream pointer by exactly this many bytes.

## Glyph data stream

Each glyph starts with a 1-byte mode:

- `0x01` — B&W (1bpp).
- `0x03` — 3-bit alpha (0..7 levels). v6 only.

The rest of the glyph is RLE'd with **`YZdddddd` opcodes** packed MSB
first into the stream. Each byte represents `(Y, Z, ddddd)`:

- `Y` is the high bit, `Z` the second-high bit. The pair selects the
  opcode family.
- `dddddd` is a 6-bit count or alpha value, depending on the family.

### B&W mode opcodes (mode byte `0x01`)

| `YZ` | Meaning                                              |
|------|------------------------------------------------------|
| `00` | `dddddd + 1` zero pixels (background)                |
| `01` | `dddddd + 1` ink pixels (opaque)                     |
| `10` | `eee` zero pixels followed by `bbb` ink pixels       |
| `11` | reserved / unobserved                                |

The `10` form's `eeebbb` packs into the low 6 bits: top 3 bits = white
run length, bottom 3 bits = ink run length, each `+1`. *(Some
documentation lists this as "white pixels followed by opaque pixels";
we treat both runs as ink in monochrome glyph rendering.)*

### AA mode opcodes (mode byte `0x03`)

| `YZ` | Meaning                                              |
|------|------------------------------------------------------|
| `00` | `dddddd + 1` zero-alpha pixels                       |
| `01` | `dddddd + 1` full-alpha pixels                       |
| `10` | one pixel at alpha `dddddd & 0x07` (3-bit)           |
| `11` | run of length `((dddddd >> 3) + 1)` at alpha `dddddd & 0x07` |

Each emitted pixel is `(alpha << 5)` in the 8-bit alpha output (so 7 →
0xE0, 1 → 0x20). For renderer compositing this gets multiplied by the
component's `pco` colour.

## Renderer integration

A loader extracts every `.zi` blob from the HMI directory and stores
the parsed `ZiFont` keyed by integer id. Each glyph's L-mode mask is
colourised with the component's `pco` and pasted at integer pixel
coordinates onto the page canvas. Variable-width fonts position
correctly by walking the per-glyph advance widths.

## Open questions

- **Z1 (v6 align8 path)**: parser handles both `align8=0` and non-zero
  forms; no observed font exercises the multiply-by-8 branch.
- **Z2 (kerning)**: per-glyph `klft` and `krht` are parsed but not
  applied. Hardware photo comparison needed to verify whether the real
  firmware applies them.
- **Z3 (v6 B&W `10` opcode)**: the white-then-ink pair currently
  renders both runs as ink. Without a synthetic font that toggles this
  the visual difference can't be verified.
