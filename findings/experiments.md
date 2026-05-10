# Experiment queue and status

This document tracks all editor experiments — completed, in progress,
and queued. Each experiment is a self-contained probe: a specific
single change in the Nextion editor, paired with a saved HMI+TFT
output, that pins down some aspect of the format.

Experiment fixtures live under `tests/editor outputs/`. Each numbered
subdirectory contains the saved files and an `instructions.md` with
exact reproduction steps.

## Conventions

- **Cumulative state**: experiments 04..11 build on each other. Each
  `NN.HMI/.tft` includes every earlier change in the chain
  (`0xDEADBEEF` from 04 persists in 05, 06, 07, 08, etc.). Interpret
  byte-count deltas as deltas from the immediately previous experiment,
  not from baseline.
- **Baseline**: `00_baseline/base.HMI` is the user's modified-from-
  original starting state. Diffs run with `scripts/diff_hmi.py` and
  `scripts/diff_tft.py`.
- **Pure no-change saves** produce **byte-identical TFTs**. The
  compile pipeline is deterministic when content hasn't changed.

## Status legend

- `[x]` resolved — findings folded into the format docs
- `[~]` partially resolved — open follow-up listed
- `[ ]` queued — fixture not yet captured or not yet analysed
- `[!]` blocked — needs external resource (different device, different
  editor version, etc.)

## Completed experiments

| # | Name                  | Status | What it pinned down                                                                                                          |
|---|-----------------------|--------|------------------------------------------------------------------------------------------------------------------------------|
| 00| baseline              | `[~]`  | Substrate for every later experiment. Missing the matching `.tft` (need to capture one).                                     |
| 01| orientation_flip      | `[x]`  | H1+0x14 is the orientation byte. 180° is runtime-only (bytecode unchanged); 90°/270° rebakes coordinates.                    |
| 04| red_val_deadbeef      | `[x]`  | Variable `val` byte location pinpointed in both HMI (`0.pa+0x524b`) and TFT (`0x713f3`). Clean substrate that cracked H1.    |
| 05| text_qqqqqqqq         | `[~]`  | Text `txt` byte location pinpointed in HMI (`0x71db4d`) and TFT (`0x712c0`). The length-prefix encoding around it isn't pinned down — see queued experiment "text bracketed lengths".  |
| 06| bco_magenta           | `[~]`  | `bco` location pinpointed in HMI (`0x720f6a`, 1730 bytes into the page payload). TFT-side location not isolated (`1f f8` is too common in the body).                |
| 07| add_hotspot           | `[x]`  | Adding a Hotspot grows the page payload by **+516 bytes**. Every component carries a fixed-size attribute record + per-event slot allocation. |
| 08| delete_component      | `[x]`  | Component deletion does **not** shrink files. Old payload is tombstoned and a new payload is appended; the deleted component's slot is reformatted rather than reclaimed. Confirms append-only journal. |
| 09| program_s_page1       | `[ ]`  | Fixture captured. Diff not yet analysed — should reveal whether the `Program.s` blob has its own CRC family (H5).            |
| 10| timer_extra_line      | `[x]`  | One more bytecode-encoding probe. Superseded by 16 for control-flow opcodes; this one stays as a regression sample.          |
| 11| add_page              | `[~]`  | TFT grows by **+1180 bytes** per empty page; HMI grows by a full sector chunk (+66168 bytes). New-page id allocation pattern (`len(pages)` vs lowest-free) needs the directory diff. |
| 13| save_six_times        | `[x]`  | 6 consecutive no-change saves → byte-identical TFTs. HMI grows monotonically for 5 saves; one tombstone reclaimed on save 6 (compaction trigger still fuzzy). |
| 15| picture               | `[x]`  | Added one Picture component. TFT grew by 132 KB (picture pixel data). Confirmed H2 schema deviation: F-series `pictures_count` is at H2+0x3a, not H2+0x34 as in T0/K0. |
| 16| loop                  | `[x]`  | Added `for(int qq=0; qq<5; qq=qq+1)` to one handler. T6 (control-flow opcodes) decoded: `09 00 04 = cjmp`, `54 20 = jmp`. Loop literals use ASCII (`5`=`0x35`), not int-literal form. Global memory directory grows by 4 bytes per declared local. |
| 17| more_components       | `[x]`  | Sim regression fixture covering Waveform, CropPicture, DualStateButton, ScrollingText, Checkbox, Radio, QRCode, Gauge. Used to drive type-specific renderer defaults and the per-component init-bytecode disassembler. |

## Queued experiments

These are the next experiments that would close known gaps. Each
entry includes: what to do in the editor, what the diff should look
like, and which unknown it answers.

### Text length encoding (closes H12)

**Folder**: `tests/editor outputs/18_text_brackets/`

**Goal**: Pin down whether text strings are length-prefixed,
null-terminated, or padded to a fixed slot.

**Steps**:
1. Start from `05_text_qqqqqqqq`'s state (where `t0.txt = "QQQQQQQQ"` is
   already in place).
2. Save three iterations:
   - `iter_q1.{HMI,tft}`: change `t0.txt` to `"Q"`.
   - `iter_q3.{HMI,tft}`: change `t0.txt` to `"QQQ"`.
   - `iter_q12.{HMI,tft}`: change `t0.txt` to `"QQQQQQQQQQQQ"`.

**Expected diff** (versus the corresponding `05.tft`):
- A small fixed-position metadata change (length field) immediately
  before the `Q…` run, OR a null terminator at varying offsets after.
- If length-prefixed: byte just before the `Q`s changes between
  iterations.
- If null-terminated: the `Q` run ends in `0x00` and the offset of that
  null changes with text length.
- If fixed-slot padded: the slot size stays constant, with trailing
  `0x00` bytes growing/shrinking.

**Answers**: H12 (Text `txt` storage encoding).

### Single-attribute deltas for exotic component types

**Folder**: `tests/editor outputs/19_attr_walk/`

**Goal**: Map attribute-record byte positions for exotic types
(Waveform / CropPicture / Checkbox / Radio / QRCode / Gauge /
DualStateButton). These types don't have a fixed 24-byte record like
XFloat; their attribute values are scattered in the flat region pointed
to by the per-component init bytecode.

**Steps**:

Start from `17_more_components` (which has one of each exotic type
already). For each component type, save one TFT per attribute change:

1. Gauge `bco` change from default to `0xF81F` (magenta).
2. Gauge `pco` change from default to `0xF81F`.
3. Gauge `val` change to `0xDEADBEEF` (or the editor's signed-int max).
4. QRCode `txt` change to `"QQQQQQQQ"`.
5. Checkbox `val` change between 0 and 1.
6. Radio `val` change between 0 and 1.
7. ScrollingText `dir` change between 0 and 1.
8. ScrollingText `dis` (scroll speed) change.
9. DualStateButton `val` change between 0 and 1.

**Expected diff**: A small handful of bytes change per save, isolated
to the per-component init bytecode region for that component. The
position of the change identifies where in the `LOAD` operand sequence
the attribute is encoded.

**Answers**: Per-component attribute-record schema for exotic types.
Unblocks the value-table portion of the
[`format-bytecode.md`](format-bytecode.md) decoder.

### Event-handler bytecode probes

**Folder**: `tests/editor outputs/20_event_scripts/`

**Goal**: Pin down how event-handler script source compiles to
bytecode for opcodes the current corpus doesn't exercise.

**Steps**: For each of the missing opcodes, write a Press event on
any Hotspot that uses just that opcode with simple operands, then
save:

1. `iter_xstr.tft` — Press handler: `xstr 100,100,200,30,1,WHITE,BLACK,1,1,1,"Hello"`
2. `iter_pic.tft`  — Press handler: `pic 0,0,0` (after adding a picture)
3. `iter_xpic.tft` — Press handler: `xpic 0,0,50,50,0,0,0`
4. `iter_picq.tft` — Press handler: `picq 0,0,50,50,WHITE` (treats clip as bg fill)
5. `iter_qrcode.tft` — Press handler: `qrcode 0,0,200,200,16,BLACK,WHITE,"test"`
6. `iter_crc.tft` — Press handler: `crcputh 0 "deadbeef"`
7. `iter_tswS.tft` — Press handler: `tswS m0,0` (toggle touch on a Hotspot)
8. `iter_cur.tft` — Press handler: `cur t0,1`

**Expected diff**: A 12-30 byte bytecode block appears inside the
Hotspot's per-event slot. Each block has the form
`<size u32> 09 NN SS <operands>` and reveals the `(size_class, index)`
pair for the new opcode.

**Answers**: T5 (unused-opcode encoding). Once captured, the
instruction-set table in
[`tools/TFTTool/NextionInstructionSets.py`](../tools/TFTTool/NextionInstructionSets.py)
can be confirmed/expanded.

### Page-add allocation pattern (closes H13, H14)

**Folder**: `tests/editor outputs/21_add_page_chain/`

**Goal**: Determine whether new page IDs are `len(pages)` or
lowest-free. Determine whether `main.HMI` (the resource manifest)
appends or rewrites on add-page.

**Steps**:
1. Start from a clean baseline.
2. Add page → save as `iter_add1.{HMI,tft}`.
3. Add another page → save as `iter_add2.{HMI,tft}`.
4. **Delete** the page added in step 2 → save as `iter_del.{HMI,tft}`.
5. Add another page → save as `iter_add3.{HMI,tft}`. The new page's ID
   reveals the allocation policy.

**Expected diff** of `main.HMI` blob between iterations:
- Append-only manifest growth (entry appended) vs. rewritten manifest
  (size unchanged, entries reordered).
- New page's ID matches the next free integer (`lowest-free`) vs. the
  current page count (`len(pages)`).

**Answers**: H13, H14.

### Page CRC family for `Program.s` and `*.zi` (closes H5)

**Folder**: `tests/editor outputs/22_program_s_byte/`

**Goal**: Verify whether `Program.s` and `*.zi` blobs use the same
five-segment chained CRC as `*.pa` (just with a different segment
chain). The leading u32 of each blob has the shape of a CRC.

**Steps**:
1. Start from baseline.
2. Change one ASCII character inside `Program.s` (e.g. `page 0` →
   `page 1`). Save as `iter_p1.{HMI,tft}`.
3. Restore. Change one byte inside `0.zi`'s glyph data via the font
   editor (or via an external hex edit + re-import if the editor
   allows). Save as `iter_z1.{HMI,tft}`.

**Expected diff**:
- For each iteration, the leading u32 of the affected blob changes;
  everything else is byte-for-byte unchanged except for one inner
  byte.
- Then enumerate plausible chain variations of the page CRC (no
  micro-CRCs / different segment counts / etc.) against the data and
  find which produces the new leading u32.

**Answers**: H5. Likely small modification of
[`scripts/page_crc.py`](../scripts/page_crc.py).

### Stable region decode (advances H2 trailing region)

**Folder**: `tests/editor outputs/23_minimal_project/`

**Goal**: Build the smallest possible project (one empty page, no
components, no events) and capture its HMI+TFT. The `H2[0x40..0xC4]`
region (decrypted) should be much smaller / simpler than for a
populated project, making its 4×32-byte row structure easier to
identify.

**Steps**:
1. In a fresh editor session, **File → New Project**.
2. Select the same model as the working project (`NX4832F035_011`).
3. Don't add any components.
4. Save as `iter_min.{HMI,tft}`.

**Expected diff** (decrypted H2 vs. populated project):
- `pageqyt = 1`, all other counts = 0.
- The 4×32-byte rows at `0x40..0xC4` should be either nearly empty or
  reveal a clear "per-page row" pattern. Rows that disappear here are
  the per-component rows.

**Answers**: Structure of the `H2[0x40..0xC4]` trailing region.

### Multi-model project compile (closes H4)

**Folder**: `tests/editor outputs/24_alt_model/`

**Goal**: Distinguish per-display config (model-specific) from
project-content data in `main.HMI` bytes `0x0C..0x60`.

**Steps**:
1. Take the same project.
2. Compile twice with **File → Change Display Model** between saves —
   once as `NX4832F035_011`, once as `NX3224F028_011`.
3. Capture both `.HMI` and `.tft`.

**Expected diff** in `main.HMI`:
- Model-id CRC at `+0x08` changes.
- Per-display config at `+0x0C..+0x60` changes wherever a display
  parameter (resolution, pixel pitch, GPIO map, etc.) differs.

**Answers**: H4. Also gives a second data point for the model_crc
function (which is otherwise just the one model we have).

**Note**: this experiment may be unrealistic if the editor refuses to
compile for a model whose firmware blob isn't on disk; in that case
the unknown stays blocked until physical hardware is available.

### Different editor version save (closes H16)

**Folder**: `tests/editor outputs/25_editor_ver/`

**Goal**: Find the editor-version field byte location by saving the
same project from two different editor versions.

**Steps**:
1. Install Nextion Editor `1.65.x` or `1.63.x` alongside the current
   `1.67.x` (typically allowed via separate install directories).
2. Open the same project in each and save.
3. Diff.

**Expected diff**: Editor-version bytes in H1 change (H1+0x01,
H1+0x02, H1+0x1b). Optional further changes inside HMI directory
entries or `main.HMI` would identify additional version-stamped fields.

**Answers**: H16.

## Where each experiment plugs into the format docs

- HMI page CRC algorithm → fixed by **04** providing a clean 4-byte
  diff substrate. See [`format-hmi.md`](format-hmi.md#page-crc-algorithm).
- HMI orientation → **01**. See
  [`format-tft.md`](format-tft.md#orientation).
- Variable val location → **04**. See
  [`format-tft.md`](format-tft.md#component-records).
- Text txt location → **05**. See same.
- Component overhead → **07**. See
  [`format-hmi.md`](format-hmi.md#component-records-inside-pa).
- HMI growth in sectors → **07/08/11**. See
  [`format-hmi.md`](format-hmi.md#resource-section-growth).
- Save determinism → **13**. See
  [`format-hmi.md`](format-hmi.md#append-only-journal).
- Picture support → **15**. See
  [`format-tft.md`](format-tft.md#resources-section-0x100000x70000).
- Control-flow opcodes → **16**. See
  [`format-bytecode.md`](format-bytecode.md#control-flow).
- Exotic component types → **17**. See
  [`format-bytecode.md`](format-bytecode.md#per-component-init-bytecode).
