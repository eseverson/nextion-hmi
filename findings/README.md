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
