# Programmatic authoring — current capabilities

Status as of 2026-05-17. Two parallel paths exist for mutating Nextion
project files without going through the Nextion Editor: HMI-side
(takes a `.HMI`, produces a new `.HMI` the editor opens and recompiles)
and TFT-side (takes a `.tft`, produces a `.tft` the device runs
directly). They share the underlying primitives — page CRC, H2 cipher,
attribute-record layout — but differ in which container they patch.

## Tools

| Tool | Input | Output | Notes |
|---|---|---|---|
| [`scripts/tools/add_hotspot.py`](../scripts/tools/add_hotspot.py)         | `.HMI` | `.HMI` | Editor opens cleanly. Verified on `00_baseline/base.HMI` and miata-dash. |
| [`scripts/tools/add_hotspot_tft.py`](../scripts/tools/add_hotspot_tft.py) | `.tft` | `.tft` | All three CRC layers reseal; new entry matches existing Hotspots. |
| [`scripts/tools/add_xfloat_tft.py`](../scripts/tools/add_xfloat_tft.py)   | `.tft` | `.tft` | Records + objxinxi + init bytecode all generated. Untested on hardware. See limitations below. |

## HMI-side authoring (`add_hotspot.py`)

### What the tool does

1. Locates the live `<page_id>.pa` blob in the directory.
2. Builds a new `.pa` blob: existing page header + PCH array (every
   existing PCH's `startOffset` shifted by +12 to make room for the new
   entry) + existing component data + 499-byte Hotspot template +
   56-byte trailing sub-record table.
3. Patches the Hotspot template at known offsets:
   `id` (+0x6b), `objname` (+0x80, up to 14 ASCII bytes), `x` (+0x122),
   `y` (+0x138), `w` (+0x14e), `h` (+0x164), `endx` (+0x17a),
   `endy` (+0x190). `type` is left as the template's baked-in 109.
4. Updates page header: `numberobj += 1`, `datasize = len(new blob)`.
5. Recomputes the page CRC via the chained five-segment CRC-32/MPEG-2
   routine (`scripts/lib/page_crc.page_crc`).
6. In-place updates the `0.pa` directory entry's `start` (= EOF of the
   original file, where the new blob is appended) and `size`.
   Preserves the 3 unknown trailing bytes of the entry.
7. Mirrors the modified entry to the backup directory at
   `0x80000 + entry_offset`.
8. Recomputes the 4-byte directory checksum
   (`CRC32_T(0xFFFFFFFF, dir_bytes + b"ADEC")`) and writes it at both
   `dir_end` and `0x80000 + dir_end`. See
   [`directory-checksum.md`](directory-checksum.md).

### Verification

- Output byte-identical to `07_add_hotspot/07.HMI`'s 499-byte Hotspot
  template when given default params (zero diffs).
- Editor opens the file with no warnings or errors; new Hotspot
  renders at the requested position with the patched name and id.
- Page CRC and directory checksum both re-validate.

### Limitations
- **Other component types not yet supported.** The mechanics generalise
  but each type needs its own per-component byte template (the 499
  bytes of attribute records + the trailing sub-record table) extracted
  from a corresponding editor fixture.

## TFT-side authoring (`add_hotspot_tft.py`, `add_xfloat_tft.py`)

Bypasses the editor entirely — produces a `.tft` the device can run.

### Common mechanics

For each new component, write three byte blocks:

1. **objxinxi entry** (232 bytes). Layout decoded in
   [`attribute-records.md`](attribute-records.md#objxinxi-entry-layout-per-component-232-bytes):
   `lei` (1B) + `id` (1B) + constant `0x3700` (2B) + `init_off` (4B,
   the strdata-relative offset of the component's init bytecode, or
   `0xFFFFFFFF` for types without one) + 20 bytes `0xff` padding +
   `objdatarampos` (4B) + 12 bytes mostly zero (byte +34 = `0x7f`) +
   `w / h / endx / endy` (4 × u16) + 180-byte Attstrpianyi:
   `bytecode_offset` u32 + 88 × u16 slots indexed by AppAttNames, each
   slot holding a record_index or `0xffff`.
2. **Allattbytes records** (`stride × 24` bytes). Built via
   `build_component_block(lei, page_record_base, records_by_name, *,
   bytecode_offset)` in `scripts/lib/tft_attrs_encoder.py`. Head
   fields (type/id/vscope/x/y/w/h/endx/endy) plus the type's declared
   attrs, placed at offsets from `PER_LEI_LAYOUT[lei]`. Empty offsets
   in the stride get 24 zero bytes.
3. **Init bytecode** (variable length, only if the type has one).
   Built via `tft_init_encoder.encode_init_block(Component(...),
   attr_addr)`. The `attr_addr` callback maps attribute names to their
   record indexes in the page-wide allattbytes table.

Then splice them into the file:

- Bytecode goes at the end of the strdata bytecode region
  (= byte before page 0's `attdataaddr_rel`). Existing `init_off`
  values are unaffected because they all point earlier.
- Records go at the end of the target page's allattbytes (= start of
  the next page's records, before shift).
- Entry goes at `objxinxiadd + (page.objstar + page.objqyt) × 232`.

Update every downstream pointer (other pages' `attdataaddr_rel` and
`objstar`, `appinf1.pageadd / objxinxiadd / attdataaddr / objqyt`,
target page's `objqyt`). Bump `appinf1.objqyt`. Re-encrypt H2 with
the new appinf1, reseal H1 / H2 / trailing CRCs.

### Verified Hotspot output (`add_hotspot_tft.py`)

Hotspots are the easy case — `init_off = 0xFFFFFFFF`, stride 27,
head-fields-only.

For miata-dash + a Hotspot at `(100, 100, 80×40)` on page 0:

- File: 504,784 → 505,664 bytes (+880 = 648 records + 232 entry).
- All three CRCs validate after reseal.
- New objxinxi entry decodes correctly, matches existing Hotspots'
  structure (obj21 = `m0`).
- Pages 1–3 shift cleanly. Existing Hotspot at obj21 is byte-identical
  between input and output.

### Verified XFloat output (`add_xfloat_tft.py`)

XFloat needs init bytecode and 22 populated attribute slots (vs 9 for
Hotspot). Stride 41.

For miata-dash + an XFloat at `(400, 240, 76×32)` on page 0:

- File: 504,784 → 506,098 bytes (+1,314 = 98 bytecode + 984 records
  + 232 entry).
- All three CRCs validate.
- Init bytecode parses as two length-prefixed blocks: 70-byte
  `setbrush` (opcode `09 1d 08`) and 20-byte `fstr` (`09 14 04`).
- New XFloat's records contain `type=59, id=30, x=400, y=240, w=76,
  h=32, endx=475, endy=271, sta=1, style=4, font=1, bco=0x2946,
  pco=0xFFFF, xcen=1, ycen=1, val=0`.

### Limitations

1. **`objname` not stored in TFT allattbytes.** XFloat records cover
   22 slots, none of which is `objname`. Searching miata-dash's TFT
   for ASCII / UTF-16 / length-prefixed encodings of `x0`, `m0`,
   `main`, etc. returns zero matches. The name-lookup mechanism the
   Nextion firmware uses for serial commands (`x9.val=…`) isn't yet
   mapped — this is the last blocker for using TFT-direct authoring
   to drive miata-dash's speed gauge. See "Open question" below.
3. **`objdatarampos` hard-coded.** Set to `24` for Hotspot and
   XFloat. Editor's allocator (mapped in
   [`memory-allocation.md`](memory-allocation.md)) picks the real
   value; for our PoC it's cosmetic.

## Open question — TFT component-name lookup

How does `x9.val=42` over UART resolve to a specific component on the
device when the string `x9` doesn't appear anywhere in the TFT bytes?

Three hypotheses:

1. **Name lookup is encoded/hashed somewhere we haven't mapped.**
2. **Derived from `(type_prefix, position-of-component-of-that-type-
   on-page)`.** For miata-dash the existing names `x0`..`x8` don't
   match `f"x{id}"` — IDs are 1..6,8..10 — but they MIGHT match
   "Nth XFloat on the page in obj-index order". If true, the existing
   `add_xfloat_tft.py` output already produces an `x9`-addressable
   component (the 10th XFloat on page 0).
3. **Names are in a separate file the editor strips on save** (like
   the `0.is`/`0.i` companion HMI files), and the device addresses
   purely by numeric ID.

Resolving this needs an empirical experiment: take a working TFT,
rename one XFloat in the editor (e.g. `x0` → `xspeed`), save and
re-flash. If `xspeed.val=…` now works and `x0.val=…` doesn't, names
ARE in the TFT — diff the two saves to find where.

## Generalising to more types

Two missing pieces for non-Hotspot types on the TFT side:

1. **Init bytecode emitter coverage.**
   [`tft_init_encoder.py`](../scripts/lib/tft_init_encoder.py) already
   round-trips XFloat, QRCode, Picture, Page. The setbrush-variant
   rule covers Text/Button (per
   [`text-setbrush-variant.md`](text-setbrush-variant.md)) but isn't
   wired into the emitter yet. Button-family templates use
   `if (val==1) {…} else {…}` and need the integrated script compiler
   (see [`next-steps.md`](next-steps.md) item 1).
2. **Declared-attr value population.** For most types this is a CLI
   shaping job — collect `--bco / --pco / --val / --font / …` flags
   and pipe them into `build_component_block(...)`. The encoder
   already accepts arbitrary `records_by_name` dicts.

On the HMI side, generalising means capturing per-type byte templates
the way we captured the Hotspot template from `07_add_hotspot`: drop
one of each type in an editor fixture, diff against baseline to
extract the per-component byte block + trailing sub-record table,
identify patchable offsets.
