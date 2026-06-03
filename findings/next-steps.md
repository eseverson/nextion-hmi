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

- **`ATTPOSUP_TABLE` coverage**: [`scripts/lib/memory_allocator.py`](../scripts/lib/memory_allocator.py)
  now exposes two parallel tables — `ATTPOSUP_TABLE` (T0 path, used by
  the 16_loop self-test) and `ATTPOSUP_TABLE_T1` (F-series path, used
  by NX*F* displays like the miata-dash). The F-series table covers 19
  component types (LEIs 0, 1, 5, 51, 52, 53, 55, 56, 57, 58, 59, 98,
  106, 109, 112, 113, 116, 121, 122) — Curve, Slider, Touchcap, Timer,
  Vari, Button_T, GText, CheckBox, Radio, Qrcode, XFloat, Button,
  Prog, Hotspot, Pic, Picc, Text, Page, Zhizhen. All entries were
  extracted from the second branch (``function_objdataraminmemory != 1``,
  which is the F-series path per
  [`text-setbrush-variant.md`](text-setbrush-variant.md)) of each
  `GuiObj<Kind>.GetAtts_WithNoHead` method in the decompiled
  `hmitype.dll` IL.
  - **Byte-verified**: T0 XFloat (val=+5) and Vari (val=+0) only.
  - **IL-only (unverified against a byte-decoded F-series fixture)**:
    all entries in `ATTPOSUP_TABLE_T1`. The Slider entry (val=+10)
    cross-matches the `0x44a + 10 = 0x454` reference in
    [`memory-allocation.md`](memory-allocation.md) § "Worked example",
    so it has indirect support but isn't end-to-end byte-checked
    through the allocator yet. A round-trip fixture (e.g.
    `17_more_components`) should be used to byte-verify the F-series
    block layout before relying on the table for emit-side authoring.
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

**Where it stands** (updated 2026-05-18):
[`scripts/lib/tft_init_encoder.py`](../scripts/lib/tft_init_encoder.py)
round-trips XFloat / QRCode / Picture / Page / **Text / Button /
Button_T / GText** byte-identically against
`17_more_components/17.tft` (see
[`init-bytecode-encoder.md`](init-bytecode-encoder.md#byte-identical-round-trip-coverage-2026-05-18)
for the full table of verified blocks).

The Text setbrush `attposup == -1` rule
([`text-setbrush-variant.md`](text-setbrush-variant.md)) is wired
in. The Button/Button_T if-else control flow uses the un-negated
`==` comparator (per `script-control-flow.md` VM semantics) and
emits draw3d bevel blocks for `sta=1, style=4`.

Variable-length attribute storage is implemented in
[`scripts/lib/tft_attrs_encoder.py`](../scripts/lib/tft_attrs_encoder.py)
as `LongAttrAllocator`: long Sstr (>4 bytes), `molloc` (curve
buffers), and `binary` blobs now flow through a two-pass allocator
that mirrors `appbianyi.StructHtoL`. The Sstr / Strlenth record pair
is patched automatically when `build_component_records` is given an
`allocator=`.

**What's left**:

- **Other types with if-else templates** — Checkbox (lei 56), Radio
  (57), Prog (106 dir=0 already, other dirs not yet) use sysvar
  scratch (`sya0 = '&w&' / 4`) plus an inner `if(val==1){…}` body.
  The script-bytecode compiler can already emit those source lines;
  hooking them into the init encoder is mechanical once a byte-verified
  fixture exists.
- **`style != 4` button paths** — the encoder currently emits the
  setbrush trailing as literal `'1'` only for `sta=1, style=4`. For
  `style ∈ {1,2,3}` the trailing should be `LOAD(borderw)`. No fixture
  in `17_more_components` exercises this; need a `style=1/2/3` button
  save to verify.
- **GText secondary blocks (`09 24 08` scroll-init, `4c 20 03`
  motion-jmp)** — the Ref event's setbrush + zstr blocks are
  byte-identical, but the two trailing blocks (which arm the scrolling
  motion handler) are not yet emitted. These are not part of the Ref
  event proper — they belong to the slide event chain.
- **Allocator integration with the page-level writer** — the
  `LongAttrAllocator` materialises the byte image of the long-string
  region but the caller still needs to wire its output into the
  per-page private memory section (see
  [`memory-allocation.md`](memory-allocation.md) "Per-page private
  allocator"). A future fixture exercising a long-`txt` Text on a
  real save will confirm the offset coordinate system byte-for-byte.

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

## 4. `appinf1` body-driven derivation — done

`compute_appinf1(body: BodyDescription) -> ComputedAppinf1` in
[`scripts/lib/compute_appinf1.py`](../scripts/lib/compute_appinf1.py)
derives every offset/count field from a high-level `BodyDescription`
(picture descriptors, font block sizes, page/obj counts, init-bytecode
size, AppAllvas count, total attribute-record bytes) and returns both
the structured field dict and the 196-byte encrypted H2 ready to write
at file offset 0xC8. Verified byte-identical for all 5 fixtures
(`16_loop`, `17_more_components`, `15_picture`, `11_add_page`,
`01_orientation_flip`) via the round-trip in
`scripts/research/compute_appinf1.py`.

**Caveats** (would benefit from a fixture):

- `gmovxinxiadd` / `videoxinxiadd` / `wavxinxiadd` are emitted as `0`
  when their respective `qty == 0` (matches every observed fixture).
  When populated their byte layout inside the resources region is
  unverified; we don't have a fixture exercising any of them yet.
- `picxinxi_offset_in_resources` is treated as caller-supplied — in
  every fixture it's the constant `0x48b5d`, but that value is a
  property of the bootloader/driver/font composition of the resources
  blob, not derivable from project content alone.

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

**Mechanism resolved** — names are stored as `crc32_bytewise(0xffffffff,
name)` u32 hashes in a sorted `(u32 hash, u16 ordinal)` table in
strdata. Cracked using `/tmp/14_char_name.tft`: the 14-char custom
hotspot name hashes to `0x689a44ff`, present exactly once in the TFT
at strdata-relative `0x11a0`. Full details in
[`component-name-hashes.md`](component-name-hashes.md).

**Still open for the speed-gauge case specifically**:
miata-dash's TFT has *no* component-name hash table — only the 4-entry
"well-known" table at strdata+0x34 (same in every TFT). Two
possibilities, in order of likelihood:

- (a) **Position-derived names**: miata-dash's components were never
  renamed in the editor, so no hash table was emitted; firmware
  resolves `x9` from `(type_prefix, ordinal-among-same-type)`.
- (b) The hash table exists but has a different shape when no custom
  names are present.

### Test path

Flash [`add_xfloat_tft.py`](../scripts/tools/add_xfloat_tft.py)'s
output (`/tmp/miata_with_xfloat.tft`, the 10th XFloat on page 0). If
`x9.val=…` writes land, hypothesis (a) is confirmed and no hash
authoring is needed. If not, we know we must emit a hash entry —
mechanics are now documented and would be added to the add-component
tools next.

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
