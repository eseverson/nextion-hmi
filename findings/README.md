# Findings index

Reverse-engineering notes on the Nextion HMI and TFT file formats and
the F-series device runtime.

## Format specs

- [`format-hmi.md`](format-hmi.md) — `.HMI` project file: directory,
  append-only journal, `main.HMI` manifest, page payload structure,
  page CRC algorithm.
- [`format-tft.md`](format-tft.md) — `.TFT` runtime file: H1 + H2
  header, body sections (resources, fonts, usercode, pictures),
  file CRC, on-disk component records.
- [`format-zi.md`](format-zi.md) — `.zi` bitmap font format
  (v3 / v5 / v6).
- [`format-bytecode.md`](format-bytecode.md) — TFT bytecode: opcode
  reference, per-component init blocks, per-event scripts, control flow.

## Cracked layers

- [`h2-cipher.md`](h2-cipher.md) — F-series H2 cipher (stateful 3-stage
  subtract/XOR mix, 16-byte key block). Reference impl:
  [`scripts/h2_cipher.py`](../scripts/h2_cipher.py).
- [`achmi-internals.md`](achmi-internals.md) — How the editor's
  `achmi.dll` is unpacked, plus the 200-entry dispatch table that
  contains the file-format primitives.

## Authoring research

Reverse-engineering deliverables for the three encoder gaps from
[`next-steps.md`](next-steps.md):

- [`attribute-records.md`](attribute-records.md) — per-page 24-byte
  attribute-record table (`binattinf`). Documents the writer routine in
  `hmitype.dll`, the per-type attribute lists, and the bit-packed
  type/length/flags field. Decoder:
  [`scripts/tft_attrs.py`](../scripts/tft_attrs.py).
- [`init-bytecode-encoder.md`](init-bytecode-encoder.md) — per-component
  init bytecode emitter, derived from the editor's per-type Nextion
  script templates. Encoder:
  [`scripts/tft_init_encoder.py`](../scripts/tft_init_encoder.py),
  round-trips against XFloat / QRCode / Picture / Page fixtures.
- [`script-compiler.md`](script-compiler.md) — event-handler script
  compiler, located in `hmitype.appbianyi`. Minimal compiler:
  [`scripts/script_compiler.py`](../scripts/script_compiler.py),
  round-trips 8 source→bytecode pairs from the project corpus.

## Roadmap

- [`experiments.md`](experiments.md) — Every editor experiment,
  completed and queued, with exact reproduction steps and expected
  diffs.
- [`next-steps.md`](next-steps.md) — Path from "edit existing TFTs" to
  "author TFTs from scratch": prioritised gap list with
  experiment-vs-disassembly recommendations per gap.

## Sample outputs

- `E-preview-main.png`, `E-preview-main-warning.png`,
  `E-preview-settings.png` — early procedural-renderer screenshots,
  retained as visual baselines.

## History

Earlier versions of these notes (single-purpose findings A–S) are
preserved in git history. The consolidated docs above supersede them.
