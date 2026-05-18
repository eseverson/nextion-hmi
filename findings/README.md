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
  [`scripts/lib/h2_cipher.py`](../scripts/lib/h2_cipher.py).
- [`achmi-internals.md`](achmi-internals.md) — How the editor's
  `achmi.dll` is unpacked, plus the 200-entry dispatch table that
  contains the file-format primitives.
- [`directory-checksum.md`](directory-checksum.md) — HMI top-level
  directory checksum (`CRC32_T` over entries + `"ADEC"` sentinel),
  validated by `CFSOpenSystem` on every editor open. Reference impl:
  [`scripts/lib/page_crc.directory_checksum`](../scripts/lib/page_crc.py).
- [`authoring.md`](authoring.md) — Working programmatic add-component
  tools for both HMI (editor round-trip) and TFT (direct flash) paths.
  Covers `add_hotspot.py`, `add_hotspot_tft.py`, `add_xfloat_tft.py`,
  their mechanics, and current limitations.

## Authoring research

Reverse-engineering deliverables for the encoder gaps from
[`next-steps.md`](next-steps.md):

- [`attribute-records.md`](attribute-records.md) — per-page 24-byte
  attribute-record table (`binattinf`). Documents the writer routine in
  `hmitype.dll`, the per-type attribute lists, and the bit-packed
  type/length/flags field. Decoder:
  [`scripts/lib/tft_attrs.py`](../scripts/lib/tft_attrs.py).
- [`init-bytecode-encoder.md`](init-bytecode-encoder.md) — per-component
  init bytecode emitter, derived from the editor's per-type Nextion
  script templates. Encoder:
  [`scripts/lib/tft_init_encoder.py`](../scripts/lib/tft_init_encoder.py),
  round-trips against XFloat / QRCode / Picture / Page fixtures.
- [`script-compiler.md`](script-compiler.md) — event-handler script
  compiler, located in `hmitype.appbianyi`. Minimal compiler:
  [`scripts/lib/script_compiler.py`](../scripts/lib/script_compiler.py),
  round-trips 8 source→bytecode pairs from the project corpus.
- [`script-control-flow.md`](script-control-flow.md) — `if`/`else`/
  `while`/`for` lowering rules + multi-operand expression emission.
  Helper module:
  [`scripts/lib/script_compiler_extras.py`](../scripts/lib/script_compiler_extras.py),
  verified against the `16_loop` fixture (simple expressions, 4-level
  if-elseif chain, `||` chain, while loop with back-jump).
- [`memory-allocation.md`](memory-allocation.md) — `appbianyi.StructHtoL`
  allocator and the frame-offset rule that turns `h0.val` into the
  bytecode operand `01 54 04 00 00`. Decodes the global memory
  directory at usercode offset 0.
- [`text-setbrush-variant.md`](text-setbrush-variant.md) — Per-type
  inline-vs-LOAD dispatch in `mobj.canshutihuan`: `attposup == -1`
  forces inline ASCII regardless of the attribute's value. Closes the
  Text/Button-family init-encoder gap.
- [`main-hmi-config.md`](main-hmi-config.md) — Full schema of the
  `main.HMI` 96-byte `hmifilehead` (no opaque per-display block);
  only `Modelcrc` varies between F-series displays.
- [`h2-trailing.md`](h2-trailing.md) — The 120 bytes after `appinf1`
  in H2 are literal `0xff` padding, not a fingerprint. Verifier:
  [`scripts/lib/h2_trailing.py`](../scripts/lib/h2_trailing.py) — 27/27
  fixtures match.

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
