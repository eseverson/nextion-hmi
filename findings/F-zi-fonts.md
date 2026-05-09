# F — Real ZI fonts in the simulator renderer

The Liberation Mono substitute is gone. The renderer now uses the actual
Nextion ZI font glyphs straight out of the HMI file.

## What's loaded from this project's HMI

The HMI directory has two `.zi` entries:

| Entry  | Description text   | Format | Cell h | Glyphs | Encoding   |
|--------|--------------------|--------|--------|--------|------------|
| `0.zi` | `liberiso-8859-1`  | v6     | 24 px  | 224    | ISO-8859-1 |
| `1.zi` | `liber-48iso-8859-1` | v6   | 48 px  | 224    | ISO-8859-1 |

Both are variable-width, anti-aliased (3-bit alpha → 0..7 levels).

## Format support

`sim/font.py` parses **v3, v5, and v6** ZI fonts:

* **v3** (fixed-width 1bpp): the original spec. Each glyph is `width × height`
  bits packed flat MSB-first, no row alignment. Total bits = `width * height`.
* **v5 / v6** (variable-width, 1bpp or AA): per-glyph entry in a 10-byte
  character map (codepoint, width, kerning, 3-byte data offset, 2-byte data
  length), then a per-glyph stream that begins with `0x01` (B&W) or `0x03`
  (anti-aliased) and continues with RLE'd `YZdddddd` opcodes.
* v6's optional 8-byte alignment flag (offset 0x21 bit 0) is honoured: when
  set, the 3-byte data offsets are pre-divided by 8 and we multiply back.
  The fonts in this HMI don't use it but the parser handles both paths.

## Differences from the published spec

A few things in the actual files don't match the
[hagronnestad/nextion-font-editor](https://github.com/hagronnestad/nextion-font-editor)
docs verbatim:

1. **`encoding` byte width.** The v3 doc says encoding is a `uint16` at
   offset 0x04. The v5/v6 doc says it's a single byte at 0x04 with a
   "multi-byte mode" flag at 0x05. Our files match the v6 layout: byte
   0x04 = 0x03 (ISO-8859-1), byte 0x05 = 0x01 (multi-byte mode = 1 even
   though the codepage is single-byte; the actual byte-range fields decide
   what's iterated).
2. **`mb_mode` is not "subset".** Our files have `mb_mode = 1` even though
   they're plain single-byte ISO-8859-1. Per spec, `1` is "double" but the
   `cp_first_*` byte range (0,0) tells the parser this is single-byte. We
   key off the byte ranges, not `mb_mode`, and it parses correctly.
3. **`align8` byte semantics.** The spec puts the v6 align-flag at offset
   0x21. Our files have `0x00` there; our parser treats any non-zero value
   as "multiply offsets by 8".

The B&W and AA RLE state machines are implemented exactly as the v6 doc
describes. One small note: in B&W mode, opcode `11 www bbb` is documented
as "www white pixels followed by bbb opaque pixels" — we treat both runs
as fully opaque ink since the stream itself is monochrome.

## Renderer wiring

* `sim/loader.py` extracts every `.zi` blob from `hmi.header.content` and
  stores the parsed `ZiFont` in `state.fonts[font_id]`, keyed by the
  filename's integer prefix.
* `sim/renderer.py` calls `draw_zi_text(...)` for Text / XFloat / Number /
  Button components when their `font` attribute resolves to a loaded
  `ZiFont`. Each glyph's L-mode mask is pasted with the component's `pco`
  as the colour. Misses (`font_id` not in `state.fonts`) fall back to the
  Liberation Mono path.
* `align_text()` is unchanged; ZI text uses its own alignment helper that
  walks per-glyph advance widths so variable-width fonts position
  correctly.

## Visual outcome

Compare the new `findings/E-preview-main.png` with the previous version in
git history — the small label font (kPa, RPM, Coolant) is now rendered at
its native 24 px with the correct light/regular weight, and the large
digit font (123456, 0.0, etc.) uses the real Liberation 48 strokes
instead of TTF Liberation Mono Bold. Layout is unchanged — boxes, colours
and component positions are unaffected.

## Tests

* `tests/sim/test_font.py` (8 tests): header parse, ASCII 'A' nonempty,
  blank space, variable-width 'i' vs 'M', loader integration, missing
  glyph → blank, synthetic v3 decoder unit test.
* `tests/sim/test_renderer.py` adds two assertions:
  * Real ZI glyphs paint exact-pco-colour pixels in the RPM label box.
  * Renderer doesn't crash when `state.fonts` is empty (TTF fallback path).
* `tests/sim/fixtures/firmware_replay.png` regenerated against the new
  glyphs.

## Future work

* The `ZiFont.encode_text()` path goes through Python's `str.encode(codec,
  errors="replace")` — for non-ISO-8859 codepages the mapping may differ
  from Nextion firmware; verify if/when a project uses GB2312, BIG5,
  Shift-JIS, etc.
* Kerning fields (`klft`, `krht`) are parsed but not applied. The
  in-firmware text layout may use them for tighter horizontal spacing;
  current output looks correct for monospace digit columns and the few
  proportional labels we have.
