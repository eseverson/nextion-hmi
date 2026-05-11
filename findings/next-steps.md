# Next steps — toward authoring HMI/TFT from scratch

Forward-looking work items only. Completed items live in the format
docs and cross-referenced research docs.

## 1. Integrate the script compiler additions

**Where it stands**: Three independent pieces exist and pass their own
self-tests:

- [`scripts/script_compiler.py`](../scripts/script_compiler.py) — base
  compiler (assignments, `print`/`printh`, `page N`, system-variable
  writes, global `int` declarations).
- [`scripts/script_compiler_extras.py`](../scripts/script_compiler_extras.py)
  — control-flow lowering (`if`/`else`/`while`/`for`) and multi-operand
  expression emission. Verified against `16_loop`. See
  [`script-control-flow.md`](script-control-flow.md).
- The frame-offset rule for component-attribute access (`h0.val` →
  `01 54 04 00 00`) from [`memory-allocation.md`](memory-allocation.md).

**What's left**: Stitch the three pieces into one compiler. Specifically:

- Wire `script_compiler_extras` into `script_compiler.py` as the
  expression+control-flow front-end.
- Implement the `(component, attr) → frame_offset` resolver using the
  rule `obj.memorypos + Upatt0.attposup`. Needs the per-type
  `_PARAM_Head` byte sizes (the one open gap from
  [`memory-allocation.md`](memory-allocation.md)) — pull these from
  `hmitype.dll!*_PARAM_Head` struct definitions.
- Cover more event-handler patterns from `nextion/source/nextion.hmi.tft`
  to widen the round-trip corpus beyond the current 8 pairs.

## 2. Finish the per-component init-bytecode encoder

**Where it stands**: [`scripts/tft_init_encoder.py`](../scripts/tft_init_encoder.py)
round-trips XFloat / QRCode / Picture / Page. The Text `setbrush`
sub-variant dispatch is now fully understood
([`text-setbrush-variant.md`](text-setbrush-variant.md)): per-type
`attposup == -1` → emit attribute value as ASCII literal; otherwise →
LOAD operand.

**What's left**:

- Apply the per-type `attposup == -1` rule to Text / Button / Button_T
  emission inside `tft_init_encoder.py`. The full per-type attribute
  list with `attposup` values is in
  [`attribute-records.md`](attribute-records.md).
- Button-family templates use `if (val==1) { … } else { … }` — wire
  in the integrated script compiler (item 1) to compile those.
- Variable-length attribute storage (strings, picture data, curve
  buffers) — the allocator is now mapped
  ([`memory-allocation.md`](memory-allocation.md)); implement the
  `Sstr`/`molloc`/`binary` allocation paths.

## 3. Build the attribute-record encoder

**Where it stands**: [`scripts/tft_attrs.py`](../scripts/tft_attrs.py)
**decodes** the per-page 24-byte `binattinf` table; the writer chain
in `hmitype.dll` is fully mapped in
[`attribute-records.md`](attribute-records.md).

**What's left**: Translate the writer chain
(`UpAttsMake.addatt` → `Attmake.attinfUpToBin` →
`mpage.Allattbytes_set` → `mobj.attpianyiset`) into Python. Given a
list of components with authored attribute values, emit:

- Each page's `allattbytes` (24 bytes per attribute, concatenated).
- Each component's `Attstrpianyi` (180 bytes on F-series): 4-byte
  init-bytecode offset followed by u16 record indexes.

Mechanical work — no further disassembly needed.

## 4. `appinf1` (H2) population helper

**Where it stands**: The 76-byte `appinf1` schema is fully decoded
([`format-tft.md`](format-tft.md#header-2-appinf1-196-bytes-encrypted)).
The 120-byte trailing region is literal `0xff` padding
([`h2-trailing.md`](h2-trailing.md)).

**What's left**: Implement
`compute_appinf1(body_layout) -> bytes` — a mechanical pass that fills
every address and count field from the body's section sizes (pages,
components, pictures, fonts, etc.), then appends `b'\xff' * 120`
before handing off to `h2_cipher.encrypt`.

## 5. `main.HMI` writer

**Where it stands**: Full 96-byte `hmifilehead` schema decoded
([`main-hmi-config.md`](main-hmi-config.md) + the updated layout in
[`format-hmi.md`](format-hmi.md#mainhmi-blob-project-manifest)). Only
the `Modelcrc` field varies between F-series models; everything else
is project-content-driven.

**What's left**: Implement `write_main_hmi(project) -> bytes`:

- Fill every field from the project metadata.
- Compute the blob CRC over `[+0x04..end]` using the same five-segment
  chained CRC-32/MPEG-2 (try the page-CRC family first; if it doesn't
  match, this is the one place [`format-hmi.md`'s H5](format-hmi.md)
  open question lands).
- Emit the trailing `(ext, name)` ref array in declaration order.

## 6. End-to-end author CLI

**Where it stands**: All the individual encoders and helpers either
exist or are one-step-away from existing (items 1-5).

**What's left**:

- `scripts/author_tft.py` and `scripts/author_hmi.py` that take a
  project description (pages, components, scripts, fonts, pictures)
  and emit a valid `.tft`+`.HMI` pair.
- A round-trip validator: re-author a known editor output and diff
  against the original byte-for-byte. Pure no-change saves are
  byte-deterministic (see
  [`format-hmi.md`](format-hmi.md#append-only-journal)), so the diff
  should be empty.
- A higher-level project format (YAML/JSON) so the IR is hand-editable.

## Recommended order of attack

1. **Item 1** — integrate the script compiler pieces. Unblocks the
   Text/Button-family templates in item 2 and gives every later
   encoder access to expressions/control flow.
2. **Item 2** — finish init-bytecode encoder coverage. Round-trip the
   full project TFT's per-component blocks. (Pull the `_PARAM_Head`
   table during this work since it's needed for item 1's resolver
   anyway.)
3. **Item 3** — attribute-record encoder. Mechanical.
4. **Item 4** — `appinf1` writer. Mechanical.
5. **Item 5** — `main.HMI` writer. Mechanical apart from the CRC
   variant question.
6. **First authored TFT**: empty project, one blank page. Round-trip
   validate against an editor-saved empty project.
7. **Item 6** — full author CLI. Round-trip the full project.

## Open disassembly leads

Small things flagged by the recent disassembly batch but not blocking:

- **`_PARAM_Head` byte-size table** per component type — needed for
  the frame-offset resolver in item 1. Likely an `int[]` constant in
  `hmitype.dll` or a per-`GuiObj_<kind>` static field. ~10-line read.
- **`&&` logical-AND in `chonggouifwhile`** — corpus has `||` only;
  the IL for `&&` looks symmetric but is not yet corpus-verified.
- **`L <addr>` opcode preceding every loop** — encoder emits a dummy
  value; the actual semantics (label table? source-map for debugger?)
  are not yet pinned.
- **`encode`-related scrambling** observed on `15_picture` /
  `17_more_components` fixtures (noted in
  [`memory-allocation.md`](memory-allocation.md)). Affects byte
  ordering within string-attribute init data.

## Out of scope

- **Tombstone/compaction policy** — a from-scratch writer never emits
  tombstones; only matters for a writer that round-trips edits while
  preserving editor undo history.
- **Multi-model `main.HMI` config** — only `Modelcrc` varies between
  F-series models, so a single u32 input from the user covers it. No
  separate table needed.
