# Next steps — toward authoring HMI/TFT from scratch

The end goal is a Linux-native toolchain that can **create** HMI and
TFT files from scratch (not just patch existing ones). This doc lists
the remaining work, in priority order, with the specific approach
for each item.

## Where we are now

**Solved primitives** (re-implementable in any language):

- All three CRC layers (page CRC, H1 CRC, file CRC) — see
  [`format-hmi.md`](format-hmi.md), [`format-tft.md`](format-tft.md).
- F-series H2 cipher (encrypt + decrypt) — see
  [`h2-cipher.md`](h2-cipher.md).
- HMI directory + entry layout, append-only journaling, manifest format.
- TFT body section layout: resources, fonts, usercode, picture data.
- ZI font format (v3/v5/v6) — see [`format-zi.md`](format-zi.md).
- Bytecode opcode set for everything observed in practice — see
  [`format-bytecode.md`](format-bytecode.md).
- Component on-disk stride (52 + 180 = 232 bytes on F-series).
- Coordinate encoding under 90°/180°/270° rotation.

**Editing today** is fully working for the well-mapped fields:
Variable val, Text txt (same-length), bco/pco, ZI font swaps,
orientation, baud, dim, editor version, file_size adjustments, and
H2 fields. The in-place patcher
[`scripts/patch_tft.py`](../scripts/patch_tft.py) handles every CRC
and the cipher.

## Gaps that block authoring

The remaining unknowns, in roughly decreasing order of authoring
impact:

### 1. Component attribute-record value table

**Status**: Per-component init bytecode is disassembled
([`scripts/tft_bytecode.py`](../scripts/tft_bytecode.py)), but its
`LOAD` operands reference attribute IDs rather than literal values.
The flat data region that maps attribute IDs to values is only
partially decoded — XFloat, Slider, ProgressBar, Variable, Text, and
Button records are pinpointed; exotic types (Waveform, CropPicture,
Checkbox, Radio, QRCode, Gauge, DualStateButton, type 113) are not.

**Why it blocks authoring**: emitting a new component requires
emitting both the init-script bytecode AND the value-table entries it
references. Without the value-table format, we can only produce
components whose attributes happen to fit the per-type fixed records.

**Approach**:

a. **Experiment-driven**: run the "single-attribute deltas" experiment
   batch in [`experiments.md`](experiments.md#single-attribute-deltas-for-exotic-component-types).
   One save per attribute change isolates each attribute's byte
   position.

b. **Disassembly-driven**: locate the editor routine that writes the
   value table. Candidates: an unmapped subcommand in
   `achmi.dll`'s 200-entry dispatch table (see
   [`achmi-internals.md`](achmi-internals.md#what-s-still-to-be-mapped)),
   or a managed method in `hmitype.dll` (likely in `Myapp_inf` or
   `appbianyi`, given those are the compile-path entry points).

Both directions in parallel is fine; whichever produces a complete map
first wins.

### 2. Per-component init bytecode encoder

**Status**: Disassembler exists. No encoder.

**Why it blocks authoring**: every component on a TFT has an init
bytecode block that's run once when its page loads. Without an encoder
we can't produce that block from declared attributes.

**Approach**: Static disassembly of `hmitype.dll` or the relevant
`achmi.dll` subcommand. Each component type's init block follows a
predictable opcode-header pattern (see
[`format-bytecode.md`](format-bytecode.md#per-component-init-bytecode));
the encoder is a small state machine that emits `LOAD` operands for
each non-default attribute. Should be 100-200 lines of Python once the
attribute-record schema (gap #1) is settled.

### 3. Event-handler bytecode encoder

**Status**: Disassembly via TFTTool's tables works (with the
nxt-1.67.1 instruction set added). No encoder exists.

**Why it blocks authoring**: any `codesup` / `codesdown` / `codestimer`
/ `codesload` handler needs to be compiled to bytecode. Authoring a
project without scripts is barely useful.

**Approach**: This is essentially a small compiler for the Nextion
script language. The grammar is well-documented in
[`tools/nxt-doc`](../tools/nxt-doc); the bytecode encoding is fully
mapped in [`format-bytecode.md`](format-bytecode.md). Build the
parser + emitter. Cross-validate against round-tripped TFTs from the
editor.

Useful experimental cross-checks live in
[`experiments.md`](experiments.md#event-handler-bytecode-probes) for
opcodes the current corpus doesn't exercise.

### 4. Global memory directory writer

**Status**: Partially mapped per
[`format-tft.md`](format-tft.md#usercode-section-0x70000file_size--4)
— first u32 is the directory size in bytes, subsequent u32s look like
`(offset, size)` or `(offset, count)` tuples. The "add a local int"
experiment showed that declaring a new variable adds one slot and
shifts two internal pointer fields by +4.

**Why it blocks authoring**: a TFT without this directory at usercode
offset 0 will not load. Even an empty project has one (size 0x48).

**Approach**: Run a short series of experiments adding one local int
at a time (1, 2, 3, 4 vars) and observe the directory's growth
pattern in detail. With 4-5 data points the layout should fall out
trivially. Alternatively, find the writer in `hmitype.dll`'s
`appbianyi` path — likely a 50-line managed method.

### 5. `appinf1` (H2) population

**Status**: All 12 address fields decode correctly. F-series count
field positions are pinned for `pageqyt`/`pictureqyt`/etc. The 120-byte
trailing region at `H2[0x114..0x18c]` decrypts to plausibly-structured
data, partly mapped as 4×32-byte rows.

**Why it matters for authoring**: producing a valid H2 means computing
every address and count from the body content and writing the trailing
fingerprint rows.

**Approach**: The address/count computations are mechanical given the
body layout. The trailing rows need either the minimal-project
experiment in [`experiments.md`](experiments.md#stable-region-decode-advances-h2-trailing-region)
or the `appbianyi` writer disassembly.

### 6. `main.HMI` manifest writer

**Status**: Layout is mapped per
[`format-hmi.md`](format-hmi.md#mainhmi-blob-project-manifest). Three
unknowns remain in bytes `0x0C..0x60` (per-display config).

**Why it matters for authoring**: every HMI needs a `main.HMI` blob,
or the editor (and likely the device) will reject the file.

**Approach**: The per-display config is model-specific. For a
single-model toolchain, copying the bytes verbatim from a known-good
file of the same model is sufficient. A model-agnostic toolchain would
need either physical hardware variety (blocked) or the disassembled
config table from `hmitype.dll`'s model registry.

### 7. Tombstone/compaction policy

**Status**: Tombstone retention is confirmed (append-only journal).
Compaction trigger is partially understood: it happens occasionally
(once in 6 saves with no content) but the exact rule is unknown.

**Why it matters for authoring**: a from-scratch writer can simply
never emit tombstones (start with the live entries only). So this is
*not* a blocker for the first version of an authoring tool — only for
a writer that round-trips edits while preserving history. Leave for
later.

## Recommended order of attack

Highest-leverage path:

1. **Run the "single-attribute deltas" experiment batch** (gap #1).
   Cheap (editor saves only) and unblocks both the value-table
   encoder and the disassembler's value resolution.
2. **In parallel, disassemble `achmi.dll` subcommands** that haven't
   been mapped yet, specifically looking for the value-table writer.
   Cross-check against experimental results from step 1.
3. **Write the per-component init bytecode encoder** (gap #2) using
   the value table from step 1+2.
4. **Write the global memory directory experiment series and decode**
   (gap #4). Small effort, unblocks the simplest possible authored
   project (one page, no components, no scripts).
5. **At this point a "trivial authored TFT" is achievable**: empty
   project with one blank page, valid CRCs, valid H2, valid memory
   directory. Validates the toolchain end-to-end before tackling
   scripts.
6. **Write the event-handler bytecode encoder** (gap #3). Largest
   single piece of code but well-specified by the existing opcode
   tables.
7. **Wire it all into a writer** alongside the existing reader.

After step 5 we have a minimal working authoring pipeline; steps 6-7
fill in the expressive features.

## Disassembly vs. experiment — which fits where

For each remaining gap, the more efficient attack is:

| Gap                                | Best attack                          |
|------------------------------------|--------------------------------------|
| Attribute-record value table       | Experiments first, disasm to verify  |
| Init-bytecode encoder              | Disassembly                          |
| Event-handler encoder              | Disassembly + table replication      |
| Global memory directory writer     | Experiments (cheap data points)      |
| `appinf1` trailing fingerprint     | Experiments (minimal project)        |
| `main.HMI` per-display config      | Copy bytes; disasm for multi-model   |
| Tombstone policy                   | Not on authoring path                |

The pattern: **experiments are efficient for small, well-localised
unknowns** where one save isolates the unknown to a few bytes.
**Disassembly is efficient for encoders** — routines that read inputs
from many places and emit a single output blob, which experiments
can't reverse-engineer in finite time.

## What gets shipped along the way

Each step produces a reusable artifact:

- Step 1: completed [`experiments.md`](experiments.md) with attribute
  byte positions tabulated in [`format-tft.md`](format-tft.md).
- Step 2: completed [`achmi-internals.md`](achmi-internals.md) with
  more dispatch entries mapped.
- Step 3: new `scripts/tft_bytecode_encoder.py`.
- Step 4: new `scripts/global_memory_directory.py`.
- Step 5: new `scripts/author_tft.py` for the empty-project case.
- Step 6: new `scripts/script_compiler.py`.
- Step 7: end-to-end `nxt-author` CLI.

Each step's output validates against round-tripping a known editor
output. The simulator already exists and can be used as the rendering
oracle for any authored file before flashing to hardware.
