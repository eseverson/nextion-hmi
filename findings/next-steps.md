# Next steps — open work

Forward-looking items only. Tools/findings already delivered are
documented elsewhere; the most relevant cross-references:

- [`authoring.md`](authoring.md) — current add-component tools
  (`add_hotspot.py`, `add_hotspot_tft.py`, `add_xfloat_tft.py`) and
  their mechanics.
- [`directory-checksum.md`](directory-checksum.md) — HMI dir CRC
  algorithm.
- [`attribute-records.md`](attribute-records.md) — `binattinf` records,
  `objxinxi` entry layout, per-class layout table.
- [`format-hmi.md`](format-hmi.md#attribute-record-format-inside-a-component-hmi-side)
  — per-component attribute record typebyte encoding.

## 1. Script compiler — open work

The integration is now in place:
[`scripts/lib/script_compiler.py`](../scripts/lib/script_compiler.py)'s
`compile_event_handler()` ties the base statement compiler to
`script_compiler_extras`' control-flow and multi-operand expression
emission, and `CompileContext` accepts an optional
`memory_allocator.MemoryAllocator` so `<comp>.<attr>` refs resolve
through the project's allocated component layout instead of a hand-
populated dict. Self-tests cover basic statements, if-else, while,
allocator-driven resolution, and a 4-way elif cascade.

**What's left**:

- **`ATTPOSUP_TABLE` coverage**:
  [`scripts/lib/memory_allocator.py`](../scripts/lib/memory_allocator.py)'s
  table only includes XFloat (lei 59) and Vari (lei 52). Extending it
  to Text / Button / Slider / Page / Hotspot / etc. requires either
  more byte-verified fixtures or pulling the per-type `attposup`
  values from `hmitype.dll` IL (the same table that powers
  [`attribute-records.md`](attribute-records.md)'s per-class layout
  for binattinf records — but those are TFT-side offsets, not the
  public-memory offsets the script compiler needs; the two tables
  are related but distinct).
- **Widen the round-trip corpus.** Current byte-verified pairs come
  from `16_loop` (if-else and while). The miata-dash Timer event
  source (in `nextion/work/hmi_text/main.txt`) and Hotspot Touch-Press
  handlers are good next targets — the source is known, the compiled
  bytecode lives in `source/nextion.hmi.tft`, but locating it requires
  following the per-component event-handler sub-record chain (see
  [`format-bytecode.md`](format-bytecode.md)).
- **`for` loop with `int` declaration in the init.** Editor source
  like `for(int qq=0; qq<5; qq=qq+1)` runs the init through
  `compile_expression`, which doesn't currently recognise `int` decls.
  Detect and route `int`-prefixed inits to the global-declaration
  path instead.

## 2. Finish the per-component init-bytecode encoder

**Where it stands**:
[`scripts/lib/tft_init_encoder.py`](../scripts/lib/tft_init_encoder.py)
round-trips XFloat / QRCode / Picture / Page. Text setbrush
sub-variant dispatch is decoded
([`text-setbrush-variant.md`](text-setbrush-variant.md)).

**What's left**:

- Apply the per-type `attposup == -1` rule to Text / Button / Button_T
  emission inside `tft_init_encoder.py`. Full per-type attribute list
  with `attposup` values lives in
  [`attribute-records.md`](attribute-records.md).
- Button-family templates use `if (val==1) {…} else {…}` — needs the
  integrated script compiler from item 1.
- Variable-length attribute storage (strings, picture data, curve
  buffers) — allocator mapped in
  [`memory-allocation.md`](memory-allocation.md); implement the
  `Sstr` / `molloc` / `binary` allocation paths.

## 3. Attribute-record encoder — minor follow-ups

The core encoder is done (round-trips 1679/1679 records in
`17_more_components`). Remaining tasks:

- **30 component classes not in fixture 17** (Audio, Video, Gmov,
  all VP variants) — capture an editor fixture exercising one of each
  and extend `PER_LEI_LAYOUT` in
  [`scripts/lib/tft_attrs_layout.py`](../scripts/lib/tft_attrs_layout.py).
- **`hdr[+2..+3] = 0x3700`** and **`hdr[+32..+43]`** constants in the
  `objxinxi` entry header — semantics unknown. The encoder emits the
  observed constants verbatim; this is documentation only, not
  blocking.
- **Sstr > 4 bytes** — long-string allocation into the per-page
  memory region (see
  [`attribute-records.md`](attribute-records.md#whats-not-yet-figured-out),
  item 2).

## 4. `appinf1` body-driven derivation

`pack_appinf1` in
[`scripts/lib/tft_attrs.py`](../scripts/lib/tft_attrs.py) re-packs the
structured `appinf1` from a `fields` dict. What's still missing is a
helper `compute_appinf1(body_layout) -> dict` that *derives* every
address/count field from observed body section sizes (pages,
components, pictures, fonts, etc.). Mechanical; not blocking the
add-component tools (they update fields incrementally).

## 5. `main.HMI` writer

**Where it stands**: full 96-byte `hmifilehead` schema decoded
([`main-hmi-config.md`](main-hmi-config.md) + the layout in
[`format-hmi.md`](format-hmi.md#mainhmi-blob-project-manifest)). Only
`Modelcrc` varies between F-series models; everything else is
project-content-driven.

**What's left**: implement `write_main_hmi(project) -> bytes`:

- Fill every field from project metadata.
- Compute the blob CRC over `[+0x04..end]`. Try the page-CRC's
  five-segment chained CRC family first; this is the open H5
  question in [`format-hmi.md`](format-hmi.md).
- Emit the trailing `(ext, name)` ref array in declaration order.

## 6. End-to-end author CLI

**Where it stands**: incremental add-component tools work (see
[`authoring.md`](authoring.md)). No from-scratch author yet.

**What's left**:

- `scripts/author_tft.py` and `scripts/author_hmi.py` taking a
  project description (pages, components, scripts, fonts, pictures)
  and emitting a valid `.tft`+`.HMI` pair.
- Round-trip validator: re-author a known editor output and
  byte-compare. Pure no-change saves are byte-deterministic per
  [`format-hmi.md`](format-hmi.md#append-only-journal), so the
  diff should be empty.
- A higher-level project IR (YAML/JSON) so the input is hand-editable.

## Last blocker for the miata-dash speed-gauge use case

The firmware writes `x9.val=…` over UART. The string `x9` doesn't
appear anywhere in the **TFT** bytes — searched ASCII, UTF-16LE/BE,
length-prefixed, and `crc32_bytewise(name)` hash; no hits. But the
**HMI side does** store the objname as an inline string of up to 14
bytes (typebyte `0x1e` in the per-component attribute record — see
[`format-hmi.md`](format-hmi.md#attribute-record-format-inside-a-component-hmi-side)).
So when a project compiles HMI → TFT the editor either:

- (a) strips/transforms names into a form we haven't decoded yet;
- (b) embeds them at a position we haven't looked at; or
- (c) derives names at runtime from `(type_prefix, position-of-
      component-of-that-type-on-page)` and the HMI-side string is
      editor-only metadata.

Hypothesis (c) is still most plausible. We *did* find one parallel
mechanism — `AppAllvas` at `strdataaddr + AppAllvasAddr` — that hashes
global scalar names (`sys0`/`sys1`/`sys2`) via `crc32_bytewise` and
maps them to byte offsets. Component-name resolution would be the
analogous structure for `<name>.<attr>` access, but we haven't seen
it. See [`format-tft.md`](format-tft.md#appallvas--global-scalar-name-table).

### Available test paths

- [`add_hotspot.py`](../scripts/tools/add_hotspot.py) produces the
  canonical 511-byte Hotspot template (`objname` typebyte `0x1e`,
  14-byte inline name) byte-identical to editor saves.
  `add_hotspot.py source.HMI -o new.HMI --name x9` followed by
  editor-compile and flash tests whether the firmware's `x9.val=…`
  writes land. If they do, names ARE addressable directly by their
  HMI string — and the question shifts to "where are they encoded in
  the TFT".
- [`add_xfloat_tft.py`](../scripts/tools/add_xfloat_tft.py) bypasses
  the editor and inserts an XFloat that is the 10th XFloat on page 0
  of miata-dash. Flash and check whether `x9.val=…` writes land — if
  yes, hypothesis (c) is confirmed; if not, names ARE in the TFT and
  we need to find where.

If neither works, capture an editor-rename fixture (e.g. `x0` →
`xspeed`) save, then diff TFT bytes against the original — the
changed bytes are the name encoding.

## Recommended order of attack

1. **Speed-gauge unblock**: flash one of the test files above. Resolves
   the name-lookup question and unblocks the actual hardware use case.
2. **Item 1**: integrate the script compiler pieces. Unblocks the
   Button-family templates in item 2.
3. **Item 2**: finish Text/Button setbrush emission once item 1 is in.
4. **Item 3 follow-ups**: pick up classes not in fixture 17 as needed
   for specific authoring use cases (no need to do all 30 up front).
5. **Items 4 / 5 / 6**: needed for from-scratch authoring; not needed
   for add-component workflows that build on existing projects.

## Out of scope

- **Tombstone/compaction policy** — a from-scratch writer never emits
  tombstones; only matters for a writer that preserves editor undo
  history.
- **Multi-model `main.HMI` config** — only `Modelcrc` varies between
  F-series models; one u32 input covers it.
