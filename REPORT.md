# Nextion HMI/TFT exploration — synthesis report

Four parallel exploration paths, each isolated to its own git worktree. All four
returned with concrete artefacts (per-path writeups in `findings/`, scripts in
`scripts/`). This report consolidates what's genuinely new.

Subject files: `source/nextion.hmi.HMI` (7.5 MB) and `source/nextion.hmi.tft`
(493 KB). The TFT model is **NX4832F035_011** (Nextion 3.5" Discovery, T1/F
series, 480×320), compiled by editor `nxt-1.67.1`.

## Headline findings

### 1. The HMI is an append-only journal — earlier saves are recoverable (Path A)

The `.HMI` file is not just a directory + payloads. It has four distinct
regions, with a **byte-identical backup of the directory at offset 0x80000**,
a sentinel `0xFFFFFFFF` at 0x380000, the magic `ver21234` at 0x6FFFF8, and the
data area starting at 0x700000. This 512KB-aligned layout is consistent with an
internal flash filesystem image.

More usefully: the 22-entry directory in our file has 8 live + **14 tombstoned**
entries. The Nextion editor never overwrites a payload in place — it appends a
new copy and flips the old entry's `deleted` byte. Concrete proof: the deleted
copy of `0.pa` differs from the live copy in **only 7 of 22,123 bytes**.
Earlier versions of pages and `Program.s` are therefore fully recoverable from
a single `.HMI` file. Nextion2Text silently filters tombstones and never
exposes this.

The 28-byte directory entry layout is now decoded:
`name[16] + start:u32 + size:u32 + deleted:u8 + tail0:u8 + tail1:u8 + tail2:u8`,
with `tail0..tail2` being a redundant `(size>>8, size>>16, size>>24)` for live
page/script entries (different and undecoded for fonts and `main.HMI`).

`main.HMI` is also decoded — it's a **192-byte resource manifest** containing a
blob CRC, model-id CRC (`0x1ce47603` = NX4832F035_011 here), per-display config,
and a 6-entry table of `(extension, "N.ext")` pairs naming the project's fonts
and pages.

None of this layout was previously documented in nxt-doc.

### 2. TFTTool silently corrupts F-series TFTs — even on no-op round-trips (Path B)

This is a severe bug, not a gap. Even loading `nextion.hmi.tft` and calling
`update_raw()` with no other changes overwrites bytes `0x10c..0x18c` (128 B
inside header 2) with `0xFF`. The original 128 bytes are structured,
high-entropy data. **TFTTool's `_fileHeader2` schema models 68 B of content;
the rest is treated as `0xFF` padding and zeroed on save.** This is correct
behaviour for T0/K0 (the documented target series) but destructive on the
F-series.

A second related issue: `_modelXORs[NX4832F035_011] = 0` — the F-series XOR
key isn't in TFTTool's table, so the header-2 "decryption" is a no-op and the
parsed addresses (`static_usercode_address = 0xfa122830`, …) are all garbage.
Consequence: even the *modeled* H2 fields are not reliably mutable on this
file.

What still works on F-series:
- Header 1 fields (resolution, orientation, file_version, lcd model, …) are
  losslessly mutable.
- Body byte-pokes (text labels, color literals, baud) survive — the body is
  never edited by TFTTool. The tail file CRC is auto-recomputed on
  `update_raw()`. **The catch is that the same `update_raw()` corrupts H2.** A
  standalone CRC helper that doesn't touch H2 is required.

The CRC algorithm itself is now fully mapped: H1 CRC = CRC32 over first 0xC4 B;
H2 CRC = same over encrypted H2; tail CRC = byte-wise CRC32 over `raw[:-4]`
(word-wise for X-series), with the **LSB XORed with `raw[0x03] ^ raw[0x2e] ^
raw[0x3c]`** (vendor letter, model_crc[0], file_version) before storage. That
last detail isn't called out in either nxt-doc or the TFTTool README.

Practical text labels — `RPM`, `Coolant`, `Battery`, `Pulsewidth m…` — sit in
fixed-stride 32-byte slots starting at file offset `0x712a0`. Same-length
replacements look feasible today using a custom CRC fixup that bypasses
`update_raw()`.

### 3. No new opcodes — the documented instruction set fully decodes this firmware (Path C)

Cross-referencing the plain-text scripts inside the HMI against the compiled
bytecode in the TFT yielded **zero unidentified opcodes and zero unidentified
sysvars**. The 112 opcode markers across 13 distinct `(size, num)` pairs and
15 sysvar markers across 7 distinct pairs all resolve cleanly under TFTTool's
existing model-100 / `nxt-1.65.1` instruction set.

A useful side finding: editor `nxt-1.67.1` (which compiled this file) is not
in TFTTool's version table, but the model-100 subset from `nxt-1.65.1` decodes
this firmware byte-for-byte correctly. So `nxt-1.67.1` and `nxt-1.65.1` share
the model-100 instruction set, at least for the operations exercised here.
That's a candidate upstream PR for `NextionInstructionSets.py`.

The most direct cross-reference proof is in `findings/C-bytecode-opcodes.md`:
HMI's `Program.s` (`baud=115200`, `recmod=0`, `printh …`, `page 0`) maps
byte-for-byte to four blocks at `0x07004c..0x07008a` in the TFT.

A red herring worth flagging: TFTTool mis-decodes block 0 at `0x070000` (a
72-byte global-memory directory) as instructions, producing meaningless
`system:0x200000` entries. These are heuristic artefacts, **not** new opcodes.

### 4. This TFT contains zero rendered images — the dashboard is procedural (Path D)

A surprise. The TFT has eight non-empty resource slots (bootloader, LCD
driver, font tables, etc.) and **no picture/image asset of any kind**. The
HMI uses zero `Picture` or `Crop` components, and the user code section
emits **0 `pic`/`xpic`/`picq` opcodes vs 33 `fill` opcodes**. The dashboard
is rendered entirely from `fill` rectangles, text from `.zi` fonts, and a
progress-bar component.

Implication for a Linux preview tool: a static raster extractor isn't useful
for this project. A *procedural replay* — walking the user-code blocks for
each `page N` routine and executing `fill`/text/progress-bar draws into a
virtual framebuffer — would work, but requires a partial Nextion VM. Out of
scope for this round but a cleanly defined follow-up.

The other contribution from this path: **the TFT resource directory layout
is now decoded.** The first 144 bytes of the resources section are
12 × 12-byte slots, each `(rel_offset:u32, size:u32, reserved:u32)`. Eight
populated, four empty. This isn't in nxt-doc.

## Cross-cutting themes

### The F-series header-2 XOR key is the central unsolved blocker

Three of four paths (B, C, D) independently hit the same wall: TFTTool's
header-2 XOR key for `NX4832F035_011` is `0`, but the actual H2 in our file
is clearly encrypted. Without that key:

- Some H2 fields (usercode address, static-usercode address, `*_count`)
  cannot be read meaningfully → forced workarounds (Path C had to locate
  usercode by direct file inspection rather than reading the H2 pointer).
- TFTTool's "decrypt + re-encrypt" pipeline is a no-op pass → the H2 region
  is preserved nominally but with the corrupted-128-bytes caveat from Path B.

Recovering this key is the single highest-leverage follow-up. Two attack
ideas surfaced:
- **Known-plaintext**: H2 should encode `ressources_files_address = 0x10000`
  and `ressources_files_size = 0x60000` (which match H1). XOR pattern from
  those known values may yield the key. Path D tried this with no success
  (the resulting key produced nonsense for other H2 fields), so the
  encryption may not be a simple XOR.
- **Cross-firmware diffing**: compile a second F-series TFT with one tweaked
  H2 field via the official editor and diff the encrypted H2 bytes. The
  key derivation function could potentially be inferred.

### TFTTool's design envelope is T0/K0 — F-series exposes its limits

This isn't a bug per se; UNUF documents the scope. But three concrete
T0/K0-specific assumptions break on F-series:
1. The 128-byte H2 region above the `_fileHeader2` schema is treated as FF
   padding (Path B).
2. The XOR key table has placeholder zeroes for non-T0/K0 models (Paths
   B/C/D).
3. The CLI hard-aborts on unknown editor versions because instruction-set
   decoding is in the constructor path. Programmatic use needs
   `decode_usercode=False` (Path B).

A "minimal F-series helper" that wraps just the working CRC algorithms and
preserves H2 byte-for-byte is a small, useful follow-up — and would unblock
real work today.

### What's now genuinely new vs nxt-doc / TFTTool / Nextion2Text

Items below are new contributions from this exploration; not present in the
upstream tooling/docs as of this run:

| Domain | New finding | Source path |
|--------|-------------|-------------|
| HMI | Four-region file layout (primary dir + backup dir + sentinel + ver-magic + data) | A |
| HMI | 28-byte directory entry struct, including `tail0..tail2 == size>>8..>>24` for pages/scripts | A |
| HMI | Backup directory at 0x80000 is byte-identical to primary | A |
| HMI | Append-only journal — tombstoned entries are recoverable previous saves | A |
| HMI | `main.HMI` 192-byte layout (CRC + model-id + 6-entry resource manifest) | A |
| HMI | Tombstone name fields are not zeroed; treat as garbage when `deleted=1` | A |
| TFT | TFTTool corrupts H2+0x44..0xc4 (128 B) on every save for F-series | B |
| TFT | Tail-CRC LSB is XORed with `raw[0x03] ^ raw[0x2e] ^ raw[0x3c]` before storage | B |
| TFT | Body text labels live in fixed-stride 32-byte ASCII slots starting at `0x712a0` | B |
| TFT | `nxt-1.67.1` shares the model-100 instruction set with `nxt-1.65.1` (this subset) | C |
| TFT | TFTTool block 0 in usercode (`0x070000`, 72 B) is a global-memory directory mis-decoded as instructions | C |
| TFT | Resource directory layout: 12 × 12-byte `(rel_offset, size, reserved)` slots at start of resources section | D |
| Project | This particular project ships zero pre-rendered images — fully procedural rendering | D |

## Practical implications for the original goals

Restating the original framing: the user wanted (1) feasibility of a Linux
HMI editor and (2) feasibility of a Linux TFT compiler.

**Linux editor: easier than estimated.** Path A's directory-format spec means
you can read existing `.HMI` files reliably today — better than what
Nextion2Text exposes (because it skips tombstones, doesn't validate the
backup, and doesn't decode `main.HMI`). Writing back to `.HMI` is also
within reach: the format is mostly a directory + length-prefixed blobs +
plain-text scripts, with a single CRC algorithm to identify.

**Linux TFT compiler: still hard, but the gap narrowed.** The remaining
unknowns are now specific:
1. F-series header-2 XOR key (and potentially key-derivation algorithm)
2. The semantics of TFTTool's "unmodeled" 128 B in H2
3. Page-blob CRC algorithm (zlib.crc32 doesn't match)
4. Whatever signing/verification the bootloader applies before accepting an
   uploaded TFT (not investigated here — would require Path E "flash and
   observe device reaction", which was explicitly out of scope)

Items 1–3 are tractable static-analysis problems. Item 4 is the one that
genuinely blocks DIY compilation on current firmware and we have no new
information on it.

**Cheap intermediate win available today**: a 100-line Python helper that
does **same-length text-label replacement in an F-series TFT**, fixing only
the tail CRC and leaving everything else untouched, would let you change
strings like `RPM`, `Coolant`, `Battery` from Linux without ever opening the
official editor. Verified safe per Path B's experiments. Would need flashing
to confirm device acceptance — that step was deliberately deferred.

## Repository state

```
nextion/
├── REPORT.md                            # this file
├── README.md                            # project intro
├── source/                              # reference HMI + TFT
├── scripts/
│   ├── setup.sh                         # bootstraps tools/
│   ├── hmi_dir.py                       # Path A — HMI directory parser
│   ├── dump_tft.py                      # Path B — header dump
│   ├── list_models.py                   # Path B — list known models
│   ├── mutate.py                        # Path B — mutation harness
│   ├── mutate_with_crc.py               # Path B — body-edit + repair harness
│   ├── opcode_match.py                  # Path C — opcode catalog walker
│   └── extract_pages.py                 # Path D — resource inventory + RGB565 PNG extractor
├── findings/
│   ├── A-hmi-format.md
│   ├── B-tft-roundtrip.md
│   ├── C-bytecode-opcodes.md
│   └── D-page-rasters.md
└── tools/                               # gitignored — populated by setup.sh
```

Worktree branches with the per-path commit history (untouched, in case full
context is wanted later):

| Path | Branch | Repo |
|------|--------|------|
| A | `worktree-agent-a7d158b697ce62dc9` | parent miata-dash repo |
| B | `worktree-agent-a2bf85fda692b923b` | parent miata-dash repo |
| C | `agent-opcode-research` | this nextion repo |
| D | `agent-page-rasters` | this nextion repo |

A and B's worktrees ended up branched from the parent firmware repo because
the harness gives subagents a worktree of the calling cwd's git repo. The
agents handled this gracefully — A worked under `nextion-research/`, B
bootstrapped a fresh `nextion/` inside the worktree. Their findings and code
are now copied into this repo's `findings/` and `scripts/`. Their original
branches are untouched in the parent repo and can be deleted whenever
convenient.

## Open research roadmap

A live catalogue of remaining unknowns + queued experiments lives at
[`findings/G-research-roadmap.md`](findings/G-research-roadmap.md).
Update it whenever an experiment lands or a new question opens.

### Current high-leverage discoveries (post-batch 2)

- **F-series H2 cipher cracked structurally** — 32-byte repeating XOR
  pad. **16 of 32 key bytes recovered** from baseline known-plaintext
  addresses (resources, usercode, videos, audios). See
  [findings/L](findings/L-h2-cipher-cracked-half.md).
- **F-series H2 schema deviates from T0/K0** — `pictures_count` is at
  H2+0x3a (not 0x34); other fields likely shifted too. See
  [findings/M](findings/M-loop-bytecode-and-pic.md) and
  [findings/N](findings/N-loop-bytecode-and-schema.md).
- **T6 loop bytecode partly decoded** — `cjmp` opcode is `09 00 04`,
  `jmp` (backward) is `54 20 ...`. ASCII bytes used for inline
  literals. [findings/N](findings/N-loop-bytecode-and-schema.md).
- **TFT body locations pinpointed**: Variable val at HMI offset
  0x71ae18 (21067 bytes into 0.pa); Text txt at 0x71db4d; component
  bco at 0x720f6a. See [findings/I](findings/I-experiment-batch-2.md).
- **HMI sectors are 64 KB-aligned** — structural changes grow the
  HMI by 0x10238 bytes (64K + overhead). Append-only journal confirmed
  with cumulative tombstones across saves.
- **Page CRC algorithm cracked** ([finding Q](findings/Q-page-crc-cracked.md)) —
  five-segment chained byte-wise CRC-32/MPEG-2 living in the editor's
  bundled native `achmi.dll` (subcommand 0x27). Implementation in
  `scripts/lib/page_crc.py`; verified across all 4 live pages. The page
  CRC was the **last barrier to writing valid HMI files**. The same
  achmi.dll has 199 other subcommands — the F-series H2 cipher (T1)
  likely lives in another one.

### What the page CRC unblocks

- **`scripts/tools/patch_hmi.py`** — modify any byte range inside a `*.pa`
  payload, recompute the CRC, write a structurally-valid HMI. Verified
  end-to-end: patch a Variable val, load patched file in the sim, see
  the new value rendered. First Linux-side HMI write tool.
- **Sim loader CRC sanity check** — every `*.pa` entry's CRC is verified
  on load; mismatches warn (without failing) so user-edited files can
  still load but get flagged.

## Suggested follow-ups, ranked

1. **F-series H2 XOR key recovery** — single highest-leverage. Unblocks
   reliable round-trip and reveals what the 128-byte unmodeled region
   actually encodes.
2. **Same-length text-label patcher** — small, useful, immediately
   demoable. A standalone Python helper that bypasses `update_raw()` and
   only fixes the tail CRC. Pairs naturally with a "flash to device" smoke
   test if/when ready to leave static-analysis-only mode.
3. **Tombstone history extractor** — surface the recoverable previous
   saves in a `.HMI` as a real feature. Useful for anyone who lost work.
4. ~~**Procedural page replay**~~ — **delivered as Path E.** Implemented as
   static-attribute rendering rather than VM execution; turned out to be
   sufficient for this project because the HMI describes everything as
   components. Renders 4/4 pages. Sample output:
   `findings/E-preview-main.png`. See `findings/E-procedural-preview.md`.
   A real VM would still be needed for projects that draw entirely from
   user-code `fill`/`xstr` calls (none here).
5. **Upstream contributions to nxt-doc / TFTTool** — file an issue/PR with
   (a) the HMI directory spec from Path A, (b) the resource directory
   spec from Path D, (c) the `nxt-1.67.1` instruction-set entry from
   Path C, (d) a heads-up about the F-series H2 corruption from Path B.
