# S — State of knowledge

A short snapshot of what is and isn't pinned down about the Nextion HMI
and TFT formats (and the ZI font format they both reference). For depth
on any individual item, follow the link to the detailed finding.

Last updated: 2026-05-10.

---

## HMI (editor project file)

### Solved

- **File layout** ([A](A-hmi-format.md)) — three reserved 512 KB sectors
  for the primary directory, a backup directory, and a sentinel region;
  all real content lives past `0x00700000` after the `ver21234` magic.
- **Directory entry structure** (28 bytes: `<16s II ? bbb>`), with a
  mirrored backup copy at `0x00080000`.
- **Page header schema** — every fixed field decoded; the appended
  payload is a `*.pa` blob whose first 4 bytes are a custom CRC.
- **Page CRC algorithm** ([Q](Q-page-crc-cracked.md)) — five-segment
  chained CRC-32/MPEG-2 with **4 mixing rounds per byte** (poly
  `0x04C11DB7`, init `0xFFFFFFFF`). The body sweep is followed by
  micro-CRCs over `datasize`, `datainformationqyt`, `pagelock`, and
  `hmiffid`. Implementation: [`scripts/page_crc.py`](../scripts/page_crc.py),
  verified against all 4 baseline `*.pa` blobs and exp-04. Extracted by
  disassembling `achmi.dll` (itself embedded in the
  `achmiface.dll` resource — see [R](R-editor-unpacking.md)).
- **Save-time behaviour** ([H8 in H](H-experiment-batch-1.md), [G1 in I](I-experiment-batch-2.md))
  — pure no-change saves are byte-identical. State leaks only when the
  editor recompiles (content changed).
- **Component-record location** of `val`, `txt`, `bco`/`pco`
  ([G2/G3/G4 in I](I-experiment-batch-2.md)) — pinpointed inside `0.pa`,
  though length-prefix vs terminator for `txt` still needs a follow-up.
- **Orientation byte** ([G10 in I](I-experiment-batch-2.md)) — H1+0x14;
  90°/270° rebake coords, 180° is a runtime flag only.
- **Tombstones / compaction** ([G6, G12 in I](I-experiment-batch-2.md))
  — accumulate per save until an unspecified compaction event collapses
  them. Trigger condition unconfirmed.

### Unsolved (HMI)

| ID | What's missing | Why it matters |
|----|----------------|----------------|
| H2 | Directory-entry tail bytes for fonts and `main.HMI` | Adding/replacing a font without corrupting the table |
| H3 | Purpose of the `0xFFFFFFFF` sentinel at file offset `0x380000` | May be a wear-levelling marker |
| H4 | `main.HMI` bytes `0x0C..0x60` | Per-display config; blocked on a second device model |
| H5 | Whether the page-CRC family applies to `Program.s`, `*.zi`, `main.HMI` | Generalising the CRC layer |
| H7 | `PageHeader+0x15 / +0x17` non-zero pattern | Probably a flag set; small impact |
| H13 / H14 | New-page id allocation policy + manifest growth on add-page | Needed to programmatically extend a project |
| H16 | Editor-version field byte location | Cross-version compatibility |
| H19 | Coordinate encoding under 90°/270° (literals are rotated) | Needed to write rotated layouts |

## TFT (compiled runtime artifact)

### Solved

- **Field map** ([B](B-tft-roundtrip.md)) — every H1 field, body section,
  resource directory, ZI font table, and the trailing file CRC is
  decoded. TFTTool's lossless round-trip is broken on F-series
  (`H2+0x44..0xC4` overwritten with `0xFF`); we have a working in-place
  patcher that preserves it.
- **Tail file CRC** — known: `CRC32` XORed with `raw[0x03] ^ raw[0x2e] ^ raw[0x3c]`.
- **H1 CRC** — known textbook variant, auto-recomputed.
- **H1+0x3c = file_size** u32 LE ([F1](H-experiment-batch-1.md)).
- **H2 cipher structure** ([L](L-h2-cipher-cracked-half.md)) — 32-byte
  repeating XOR pad. **Key bytes 0..15 recovered**; bytes 16..31 still
  unknown, but solvable by adding a Picture component (which would shift
  a known plaintext into that window).
- **Bytecode opcodes** ([C](C-bytecode-opcodes.md)) — every opcode the
  miata firmware actually uses is documented; `nxt-1.67.1 ≡ nxt-1.65.1`
  for that subset.
- **Per-component init-script bytecode disassembler**
  ([scripts/tft_bytecode.py](../scripts/tft_bytecode.py)) — structurally
  decoded; LOAD operands reference attribute IDs (the value-table
  decoding is what's left).

### Unsolved (TFT)

| ID | What's missing | Why it matters |
|----|----------------|----------------|
| T1 | Last 16 bytes of the H2 XOR pad | Full read-write parity with the editor |
| T2 | The `0x44..0xC4` H2 region (gated on T1) | TFTTool wipes it; semantics unknown |
| T4 | Resource directory's third "reserved" u32 | Likely metadata |
| T5 | Bytecode for unused opcodes (`pic`, `xpic`, `picq`, `xstr`, `crcputh`, `qrcode`, `tswS`, `lcd_dev`, …) | Each needs a fixture exercising the opcode |
| T6 | Control-flow opcodes (`while`/`for`/`goto`) | Loop encoding |
| T7 | "Global memory directory" at usercode offset 0 | Variable layout — partially mapped in [M](M-loop-bytecode-and-pic.md) |
| T8 | Whether `nxt-1.67.1` adds opcodes vs `nxt-1.65.1` outside our subset | Cross-version compat |
| — | **Per-component attribute-record value table** | The bytecode disassembler resolves attribute IDs but not their values. Renderer uses type-specific defaults to paper over the gap. |

## ZI fonts

### Solved

- v3 / v5 / v6 parser ([F](F-zi-fonts.md)) — integrated; the sim renders
  ZI text glyph-by-glyph and matches the editor's appearance for the
  fonts shipped in the miata project.

### Unsolved (ZI)

- Z1: v6's 8-byte-aligned glyph-offset path (parsed but never exercised
  by a real fixture).
- Z2: per-glyph kerning fields (`klft`/`krht`) — parsed, unused; needs a
  hardware photo to validate.
- Z3: v6 B&W mode opcode `11 www bbb` — both runs currently treated as
  ink; needs a synthetic font to verify.

## Cross-cutting tooling status

- **HMI → sim**: full pixel parity on the miata pages.
- **TFT-only → sim**: pixel parity on simple pages; exotic component
  types (Waveform, CropPicture, etc.) use editor-default fallbacks
  because their attribute records aren't in the value table yet.
- **TFT write path**: lossless patcher works (CRCs + file_size kept
  consistent). The H2 cipher's missing 16 key bytes block any rewrite
  that needs to touch addresses in `H2+0x10..0x1f`.

## Feasibility today: editing vs. authoring

**Editing an existing file (in place) — feasible, with caveats.**
We have a working patcher for both HMI and TFT that recomputes every
known CRC (page CRC, H1 CRC, tail file CRC) and keeps `file_size`
consistent. Reliable mutations today:

- Variable `val` (TFT + HMI byte locations known)
- Text `txt` (location known; the length-prefix vs terminator detail is
  the only blocker for arbitrary new strings of a different length —
  same-length swaps already work)
- `bco` / `pco` on located components
- Orientation (H1+0x14)
- Editor-version fields, baud, dim, sleep timeout — all narrow byte
  swaps validated by the experiment batches
- ZI font swaps at the directory level
- Adding/removing a tombstoned component (compaction trigger still
  fuzzy, but tombstones themselves are well-understood)

The limit is anything that requires writing into `H2+0x10..0x1f` or the
unmodelled `H2+0x44..0xC4` region: those need the missing 16 H2 key
bytes (T1) before we can encrypt new content correctly.

**Authoring a new HMI/TFT from scratch — not yet feasible.** The
blockers, in roughly decreasing severity:

1. **Per-component attribute-record value table** — the bytecode
   disassembler resolves attribute IDs, but we don't yet know how the
   editor *writes* the value table that those IDs index into. Without
   this we can't emit a new component with non-default attributes.
2. **H2 cipher (T1)** — 16 key bytes missing. Encrypting a freshly
   built H2 isn't possible until this is closed.
3. **Bytecode generation** — we can disassemble per-component init
   scripts and the main user-code body, but we have no encoder for
   event handlers or for control-flow opcodes (T5/T6).
4. **Page CRC** — solved, so this is no longer a blocker for new
   pages, but the page *body* itself (`*.pa` payload) still needs the
   attribute-record schema (item 1) to be authorable.
5. **HMI directory tail bytes** (H2 in HMI) and per-display config
   block (H4) — needed to author a valid `main.HMI`.
6. **Global memory directory** (T7) — partially mapped; a from-scratch
   project would need to emit a valid TLV layout for declared globals.

Order-of-operations to make authoring feasible: T1 + the attribute-record
schema would together unlock ~80% of useful authoring. Both look
tractable by continuing the `achmi.dll` disassembly that already
delivered the page-CRC algorithm.

## What would move the needle most next

1. **Picture-component experiment** → cracks the rest of the H2 key
   (T1) → unblocks T2.
2. **Disassemble more of `achmi.dll`** (199 other dispatch-table
   subcommands besides the page-CRC one) → likely contains the H2
   cipher and the attribute-record decoder.
3. **Q/QQ/QQQ text fixture** → resolves the `txt` length-prefix
   question (H12).
4. **Two-editor-version save** of the same project → resolves H16 and,
   incidentally, H4 if the second editor targets a different model.
