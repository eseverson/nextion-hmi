# Next steps — toward authoring HMI/TFT from scratch

Forward-looking work items only. Completed items are not listed here;
their results live in the format docs and the cross-referenced
research docs.

## 1. Finish the script compiler

**Where it stands**: [`scripts/script_compiler.py`](../scripts/script_compiler.py)
handles assignments, `print`/`printh`, `page N`, system-variable
writes, and global `int` declarations. Round-trips 8 source→bytecode
pairs from the project corpus.

**What's left**:

a. **Component-attribute access** (e.g. `dim=h0.val`, `x0.bco=red.val`).
   These compile to a local-var ref like `01 54 04 00 00` where
   `0x454` is the frame offset of `h0.val` in the page's local
   memory. Computing that offset needs a per-page
   `(component_name, attr_name) → frame_offset` resolver. Trace
   `appbianyi.mollocmemory_add` (~50 lines of IL in `hmitype.dll`)
   to recover the allocation rule. See
   [`script-compiler.md`](script-compiler.md).

b. **Control-flow lowering** for `if`/`while`/`for`/`else`. The editor
   does this in `appbianyi.chonggouifwhile` — rewrites the structured
   construct to a flat sequence of `i` (cjmp) + `54 20 …` (jmp) +
   labels. Encoder needs to compute branch offsets after emitting the
   target.

c. **Multi-operand expressions** (e.g. `x0.val=h0.val+5`). Today only
   single-RHS-token assignments work. Needs a small expression parser
   that emits in the canonical Nextion order (recovered from existing
   compiled examples in the project TFT).

**Why it's first**: gap #2's button-family init templates use `if/else`
and several Text templates emit multi-step expressions, so the
init-bytecode encoder can't reach 100% coverage until this lands.
The script compiler is also the gating dependency for any project that
has event handlers (which is almost all of them).

## 2. Finish the per-component init-bytecode encoder

**Where it stands**: [`scripts/tft_init_encoder.py`](../scripts/tft_init_encoder.py)
round-trips XFloat / QRCode / Picture / Page byte-for-byte. Templates
extracted for every visible component type.

**What's left**:

a. **Button-family templates** (Button, DualStateButton, type-3 Hotspot
   refresh) — these use `if (val==1) { …pressed colours… } else
   { …released colours… }`. Blocked on the control-flow piece of the
   script compiler (item 1b).

b. **Text `setbrush` sub-variant** — when `spax`/`spay` are zero, the
   editor inlines them as ASCII literals rather than emitting a LOAD
   operand. Detection logic needed in the encoder.

c. **Variable-length attribute references** — strings (`txt`, `path`)
   and arrays are stored in a separate memory area pointed to by
   `attmemorypos`. Allocation rule lives in
   `appbianyi.mollocmemory_add`. Same target as item 1a.

## 3. Build the attribute-record encoder

**Where it stands**: [`scripts/tft_attrs.py`](../scripts/tft_attrs.py)
**decodes** the per-page 24-byte `binattinf` table; per-type attribute
layouts are documented in
[`attribute-records.md`](attribute-records.md).

**What's left**: the **encoder** side. Take a list of components with
their authored attribute values and emit:

- Each page's `allattbytes` (24 bytes per attribute, concatenated).
- Each component's `Attstrpianyi` (180-byte block on F-series; first
  4 bytes = byte offset of this component's init bytecode within
  `strdata`, remainder = u16 indexes back into the page table).

Writer chain in `hmitype.dll` is mapped already
([`attribute-records.md`](attribute-records.md)) — this is a
mechanical translation of `mpage.Allattbytes_set` + `mobj.attpianyiset`
to Python.

## 4. Global memory directory writer

**Where it stands**: First u32 of the usercode region is the directory
size in bytes; subsequent u32s are `(offset, size)` or `(offset, count)`
tuples. Adding a single `int` local grows the directory by 4 bytes and
shifts two internal pointer fields by +4 (one observation).

**What's left**: Pin down the layout with either:

- **Disassembly path**: trace `appbianyi.mollocmemory_add` in
  `hmitype.dll`. Same routine flagged by items 1a and 2c, so this work
  benefits all three. Expected ~50 lines of IL.
- **Experiment path**: save 5 small projects with progressively more
  local-int declarations (0, 1, 2, 3, 5 locals) and inspect the
  directory growth pattern byte-by-byte.

The disassembly path is preferred — three other items already need
the same routine read.

## 5. `appinf1` (H2) population

**Where it stands**: Schema is fully decoded
([`format-tft.md`](format-tft.md#header-2-appinf1-196-bytes-encrypted)
with corrections from [`attribute-records.md`](attribute-records.md));
encrypt/decrypt round-trips losslessly via
[`scripts/h2_cipher.py`](../scripts/h2_cipher.py).

**What's left**:

a. **Compute every address/count from body content** — a mechanical
   pass once all the sub-section sizes are known (pages, components,
   pictures, fonts, etc.). Implement as a single
   `compute_appinf1(body_layout) -> bytes` helper.

b. **Trailing 120 bytes** (`H2[0x114..0x18c]`) — four ~32-byte rows
   that decrypt to plausibly-structured data. Either:
   - Run the `23_minimal_project` experiment from
     [`experiments.md`](experiments.md#stable-region-decode-advances-h2-trailing-region)
     to isolate per-component vs. per-page rows.
   - Find the writer in `appbianyi` and decode directly.

## 6. `main.HMI` manifest writer

**Where it stands**: Top-level layout is mapped per
[`format-hmi.md`](format-hmi.md#mainhmi-blob-project-manifest). Three
unknowns remain in bytes `0x0C..0x60` (per-display config).

**What's left**: Per-display config is model-specific. For a
single-model toolchain, copy the bytes verbatim from a known-good file
of the same model — that's enough to produce valid output. A
model-agnostic toolchain needs:

- **Disassembly path**: find the config table in `hmitype.dll`'s model
  registry. Almost certainly a switch/dictionary keyed by `model_crc`.
- **Hardware path**: collect HMIs from a second model and diff against
  the existing one. Blocked on physical hardware variety.

## 7. End-to-end author CLI

**Where it stands**: All the individual encoders exist as research
scripts. Nothing stitches them together.

**What's left**:

- A `scripts/author_tft.py` (and matching `author_hmi.py`) that takes
  a project description (pages, components, scripts, fonts, pictures)
  and emits a valid `.tft`+`.HMI` pair.
- A round-trip validator: re-author a known editor output and diff
  against the original byte-for-byte. Tolerance only for fields the
  editor itself non-deterministically populates (none today; saves are
  deterministic — see
  [`format-hmi.md`](format-hmi.md#append-only-journal)).
- Eventually a higher-level project format (YAML/JSON) so users don't
  hand-author the intermediate representation.

## Recommended order of attack

1. **Disassemble `appbianyi.mollocmemory_add`** in `hmitype.dll`. One
   read unblocks items 1a, 2c, 4. Expected: a couple hours.
2. **Finish the script compiler** (items 1b, 1c) — needed by the
   button-family init templates and by all non-trivial event handlers.
3. **Finish the init-bytecode encoder** (items 2a, 2b) — completes
   per-component coverage.
4. **Build the attribute-record encoder** (item 3) — the last
   per-component piece; mechanical translation of the already-mapped
   writer chain.
5. **Global memory directory writer** (item 4) — small, but blocking
   for any authored TFT.
6. **`appinf1` population helper** (item 5a) — mechanical.
7. **First authored TFT**: empty project, one blank page. Round-trip
   validate. This is the milestone where the pipeline first produces
   a device-loadable file from scratch.
8. **Trailing `H2[0x114..0x18c]` rows** (item 5b) — needed for any
   project beyond the minimal case.
9. **`main.HMI` for the single-model case** (item 6) — copy-bytes path.
10. **`author_tft.py` / `author_hmi.py` glue** (item 7).

## Disassembly vs. experiment

For the remaining items:

| Item                                  | Best attack                          |
|---------------------------------------|--------------------------------------|
| 1a Component-attribute resolver       | Disassembly (`mollocmemory_add`)     |
| 1b Control-flow lowering              | Disassembly (`chonggouifwhile`)      |
| 1c Multi-operand expressions          | Disassembly + corpus cross-check     |
| 2a Button-family init templates       | Blocked on 1b                        |
| 2b Text `setbrush` sub-variant        | Disassembly + corpus cross-check     |
| 3 Attribute-record encoder            | Disassembly (writer chain mapped)    |
| 4 Global memory directory             | Disassembly (`mollocmemory_add`)     |
| 5a `appinf1` address/count compute    | Mechanical, given body layout        |
| 5b H2 trailing rows                   | Experiment first, disasm to verify   |
| 6 `main.HMI` per-display config       | Copy bytes; disasm for multi-model   |
| 7 End-to-end CLI                      | Implementation only                  |

**Item 1 (`mollocmemory_add`) is the highest-leverage single read** —
it unblocks the script compiler's remaining work, the init-encoder's
variable-length attributes, and the global memory directory writer in
one pass.

## Out of scope

- **Tombstone/compaction policy** — a from-scratch writer can simply
  never emit tombstones. Only matters for a writer that round-trips
  edits while preserving editor undo history.
- **Multi-model `main.HMI` config table** — needs physical access to
  additional display models, blocked.
