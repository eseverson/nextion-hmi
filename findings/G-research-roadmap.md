# Research roadmap & status

A living catalogue of everything we don't yet understand about the
Nextion HMI / TFT formats and the runtime command/event surface, plus
the concrete experiments queued to crack each unknown. Update this file
as findings land.

Keep entries short. Status legend:

- `[ ]` — open / not started
- `[~]` — in progress (data dropped in `tests/editor outputs/`, awaiting analysis)
- `[x]` — resolved (link to the finding that answers it)
- `[!]` — blocked (note the blocker)

## How to update

1. Run an experiment from `tests/editor outputs/<NN>_*/` (or drop a new
   pair into a freshly-created folder).
2. Run `python3 scripts/analyze_editor_outputs.py --only <NN>_name`.
3. Update the matching row(s) below — change the status marker, add a
   one-line note with the date and outcome.
4. If the finding warrants a deeper writeup, add a new `findings/H-…md`
   file and link from here.

---

## 1. HMI format unknowns

| # | Unknown | How to crack | Status |
|---|---------|-------------|--------|
| H1 | Page-blob CRC algorithm (zlib.crc32 doesn't match) | Brute-force every CRC32 polynomial × seed × reflect against baseline 0.pa and 04 0.pa (known 4-byte payload diff) | `[x]` Resolved by [Q](Q-page-crc-cracked.md): five-segment chained byte-wise CRC-32/MPEG-2 (poly 0x04C11DB7, init 0xFFFFFFFF, 4 mixing rounds per byte). The body sweep is followed by micro-CRCs over `datasize`, `datainformationqyt`, `pagelock`, and `hmiffid` — the redundant chain is what threw textbook brute force. Algorithm lives in the editor's bundled native `achmi.dll` (extracted from `achmiface.dll`'s embedded resource). Implementation: [scripts/page_crc.py](../scripts/page_crc.py), verified against all 4 baseline `*.pa` blobs and exp-04. |
| H2 | Directory-entry trailing 3 bytes (`tail0..tail2`) for fonts and `main.HMI` (the size>>8 pattern doesn't apply) | Diff `00_baseline` vs `11_add_page` (font directory entry semantics may shift) | `[ ]` |
| H3 | Purpose of the `0xFFFFFFFF` sentinel at file offset 0x380000 | `13_save_six_times` — does it ever move, change, or get repeated? | `[ ]` |
| H4 | `main.HMI` blob bytes 0x0C..0x60 (per-display config) | Compare HMI files from two different physical models — out of scope until we have a second device | `[!]` (need 2nd model) |
| H5 | Page CRC for non-page entries (`Program.s`, `*.zi`, `main.HMI`) — they have a leading u32 too | `09_program_s_page1` produces a 1-byte payload diff; try same CRC algorithm hunt | `[ ]` |
| H6 | Whether the editor compacts the data area on save | `13_save_six_times` + `08_delete_component` — does data area ever shrink? | `[~]` Yes, occasionally per [G6](I-experiment-batch-2.md): one tombstone got compacted on iter6 of 13. Trigger condition unclear. |
| H7 | The "PageHeader+0x15 / +0x17" `?` bytes (consistently non-zero, varying per page) | Compare `*.pa` payloads across our 4 existing pages — partially analysable from baseline alone | `[ ]` |
| H8 | Whether saves leak ephemeral state (timestamps, save counters) | `12_save_no_change` — diff two no-op saves | `[x]` Resolved by [F2](H-experiment-batch-1.md) + revised by [G1](I-experiment-batch-2.md): leak only on saves that recompile (content changed). Pure no-change saves produce byte-identical TFTs. |
| H9 | Component attribute record layout — minimum overhead per component | `07_add_hotspot` — diff baseline vs +1 Hotspot | `[x]` Resolved by [G7](I-experiment-batch-2.md): +516 bytes per Hotspot. |
| H10 | Per-component `bco`/`pco` byte position inside attribute record | `06_bco_magenta` — distinctive 0xF81F is grep-able | `[~]` Located in HMI at `0x720f6a` per [G4](I-experiment-batch-2.md) (1730 bytes into 0.pa payload). TFT side has noisy false positives; needs narrower probe. |
| H11 | Per-Variable `val` byte position inside attribute record | `04_red_val_deadbeef` — distinctive 0xDEADBEEF is grep-able | `[x]` Resolved by [G2](I-experiment-batch-2.md): TFT `0x713f3`, HMI `0x71ae18` (21067 bytes into 0.pa payload). |
| H12 | Where Text `txt` strings live (length-prefixed? null-terminated?) | `05_text_qqqqqqqq` — known plaintext | `[~]` Located in TFT `0x712c0`, HMI `0x71db4d` per [G3](I-experiment-batch-2.md). Length-prefix or terminator format unconfirmed; needs Q/QQ/QQQ bracketed experiment. |
| H13 | New page id allocation (lowest-free vs len(pages) vs other) | `11_add_page` | `[ ]` (data exists; HMI directory analysis pending) |
| H14 | Sentinel meaning when adding a page (does `main.HMI` resource manifest grow?) | `11_add_page` | `[ ]` (data exists; HMI directory analysis pending) |
| H15 | Tombstone retention policy (always all? capped? FIFO?) | `13_save_six_times` | `[~]` Tombstones accumulate per save until a compaction event ([G12](I-experiment-batch-2.md), [G6](I-experiment-batch-2.md)). Trigger for compaction not yet pinned. |
| H16 | Editor version field bytes (where in H1?) | Open the project in a different editor version and re-save — out of scope unless multiple editors available | `[!]` |
| H17 | What encodes at usercode+0x715f4 (4 bytes that change every save)? | Diff per-save outputs vs known-changing inputs (timestamp? RNG?). `13_save_six_times` will show whether the value monotonically changes or is random. | `[x]` Resolved by [G1](I-experiment-batch-2.md): doesn't change on no-content saves; only refreshed on recompile. Likely a compile-time hash. |
| H18 | What encodes at usercode+0x71634 (1-byte save counter)? | `13_save_six_times` should confirm linear increment per save. | `[x]` Resolved by [G1](I-experiment-batch-2.md): doesn't increment on no-content saves; refreshed only on recompile. |
| H19 | Page coordinate encoding (90°/270° flips rotate *literals*; 180° doesn't) | `06_bco_magenta` + `07_add_hotspot` — distinctive bbox values | `[ ]` (new from F4) |
| H20 | Where is orientation stored in H1? | Direct observation across baseline/01/vertical | `[x]` Resolved by [G10 revised](I-experiment-batch-2.md): **H1+0x14**. 0x01=0°, 0x00=90°, 0x03=180°. 180° is runtime-only (user code unchanged); 90°/270° rebake coords. |

## 2. TFT format unknowns

| # | Unknown | How to crack | Status |
|---|---------|-------------|--------|
| T1 | F-series H2 XOR key (TFTTool placeholder = 0; H2 is clearly encrypted) | Self-correlation found 32-byte cycle; recover key bytes via known-plaintext addresses | `[~]` **Cipher cracked structurally per [L](L-h2-cipher-cracked-half.md): 32-byte repeating XOR pad. 16 of 32 key bytes recovered (positions 0..15). 16 unknown (positions 16..31) need plaintext at H2+0x10..0x1f. Adding one Picture component would likely crack it — pictures_address would shift between baseline and post-Picture experiment.** |
| T2 | The 128-byte H2 region at H2+0x44..H2+0xC4 that TFTTool wipes to 0xFF on save | After T1 lands (key known), decrypt this region and inspect | `[ ]` blocked on T1 |
| T9 | Field at H1+0x3c (formerly listed as "H1+0x37..0x47 ressources_files_*") | Diff multiple experiments where file size grows | `[x]` Resolved by [F1](H-experiment-batch-1.md): H1+0x3c is `file_size` u32 LE. Updates by exactly the file growth on every save. |
| T3 | Tail file CRC LSB XOR derivation (`raw[0x03] ^ raw[0x2e] ^ raw[0x3c]`) — already known per Path B; cross-validate on a deliberately-modified file | Any of the experiments where tail CRC is non-trivial | `[x]` per Path B |
| T4 | Resource-directory's third u32 ("reserved" per Path D) — what is it? | Compare resource directories across baseline + experiments that change resource counts (none of the queued experiments touch resources directly) | `[ ]` |
| T5 | Bytecode for unused opcodes (`pic`, `xpic`, `picq`, `xstr`, `crcputh`, `qrcode`, `tswS`, `lcd_dev`, …) | Each requires a specific feature in the project. `15_pic_component` and friends would be future experiments. | `[ ]` |
| T6 | TFT body opcode encoding for `while`/`for`/`goto` | Future experiment: add a small `for` loop to a Press handler, recompile | `[ ]` |
| T7 | The "global memory directory" structure at usercode offset 0 (Path C noted TFTTool mis-decodes it as instructions) | Compare baseline vs `04_red_val_deadbeef` — Variable val placement might be reflected here | `[ ]` |
| T8 | Whether `nxt-1.67.1` instruction set differs from `nxt-1.65.1` for opcodes the project doesn't exercise | Add a project that exercises more opcodes (T5 covers this) | `[ ]` |

## 3. ZI font unknowns

| # | Unknown | How to crack | Status |
|---|---------|-------------|--------|
| Z1 | v6 8-byte-aligned-glyph-offset code path (parser implements it but no fixture exercises it) | Add a v6 font with a glyph table that triggers alignment — needs custom font in editor | `[ ]` |
| Z2 | Per-glyph kerning fields (`klft`, `krht`) — parsed but not applied | Render comparison test against real device output | `[!]` (needs device photo) |
| Z3 | v6 B&W mode opcode `11 www bbb` interpretation — currently both runs treated as ink | Edge case; needs synthetic test font | `[ ]` |

## 4. Drop-and-go experiments user has already saved

These are in `tests/editor outputs/` at the repo root (not in the
numbered subfolders). Files use `<descriptor>.HMI/.tft` naming.

| File | Maps to | Status |
|------|---------|--------|
| `vertical.HMI/.tft` | `01_orientation_flip` (vertical flip) | `[x]` analysed → [F4](H-experiment-batch-1.md): orientation rebakes coords |
| `dim 66.HMI/.tft` | `02_dim_default` (backlight 66%) | `[x]` analysed → [F1, F3](H-experiment-batch-1.md) |
| `230400 baud.HMI/.tft` | `03_baud_change` (115200 → 230400) | `[x]` analysed → [F5, F6](H-experiment-batch-1.md): 2-byte change only |
| `sleep 30.HMI/.tft` | NEW: sleep-timeout 30s — unmodelled experiment, valuable | `[x]` analysed → same H1/H2 footprint as `dim 66` (F1, F3) |
| `save A.HMI/.tft` | de-facto baseline used for batch 1 | `[x]` |
| `save C.HMI/.tft` | save-determinism iter | `[x]` analysed → [F2](H-experiment-batch-1.md): 5-byte ephemeral state per save |
| `save D.HMI/.tft` | save-determinism iter | `[x]` analysed → [F2](H-experiment-batch-1.md) |
| `sim 66.HMI` (no .tft) | unclear; missing TFT | `[!]` need .tft to diff |

**Action:** run `scripts/diff_hmi.py` and `scripts/diff_tft.py --xor-h2`
on each pair against a baseline (we don't have a labelled baseline yet —
need a `baseline.HMI/.tft` saved with no changes from the user's current
project state). Once a baseline lands, we can analyse all of these in
one pass.

## 5. Open queue (run when convenient)

The 14 numbered subfolders in `tests/editor outputs/` each have an
`instructions.md` describing exactly what to do. Status here mirrors
what files are present in each folder.

| # | Name | Status |
|---|------|--------|
| 00 | `baseline` | `[~]` user dropped baseline; awaiting full pair |
| 01 | `orientation_flip` | `[~]` user populated; analysed via top-level `vertical.*` ([F4](H-experiment-batch-1.md)). Re-run against a clean baseline to nail T1. |
| 04 | `red_val_deadbeef` | `[ ]` cracks H1 (page CRC) + H11 (Variable val location) |
| 05 | `text_qqqqqqqq` | `[ ]` cracks H12 (Text txt storage) |
| 06 | `bco_magenta` | `[ ]` cracks H10 (bco position) + H19 (coordinate encoding) |
| 07 | `add_hotspot` | `[ ]` cracks H9 (component overhead) |
| 08 | `delete_component` | `[ ]` cracks H6 (compaction) + H15 (tombstones) |
| 09 | `program_s_page1` | `[ ]` cracks H5 (Program.s blob CRC) |
| 10 | `timer_extra_line` | `[ ]` deeper bytecode encoding |
| 11 | `add_page` | `[ ]` cracks H2, H3, H13, H14 |
| 13 | `save_six_times` | `[ ]` cracks H17 (4-byte hash) + H18 (counter pattern) |
| 15 | _future: add Picture component_ | `[ ]` would crack T5's `pic` opcode |
| 16 | _future: add a `for` loop in a script_ | `[ ]` cracks T6 |
| 17 | _future: change project file version field_ | `[ ]` if editable |
| 18 | _future: project name change_ | `[ ]` to find where the project name is stored |

**Removed** (redundant — fully resolved by batch 1):

- ~~`02_dim_default`~~ — covered by `dim 66.*` ([F1, F3](H-experiment-batch-1.md))
- ~~`03_baud_change`~~ — covered by `230400 baud.*` ([F5, F6](H-experiment-batch-1.md))
- ~~`12_save_no_change`~~ — covered by `save A`/`save C`/`save D` comparison ([F2](H-experiment-batch-1.md))

## 6. Sim feature gaps (what the simulator doesn't yet model)

The simulator covers the runtime surface the miata firmware exercises,
plus most of the commonly-used Nextion command set. These are the
documented features it does NOT yet handle.

### Commands not implemented

| Command | Purpose | Priority |
|---------|---------|----------|
| `pic <x>,<y>,<id>` | draw image from resource | low — project has no images |
| `xpic <x>,<y>,<w>,<h>,<srcX>,<srcY>,<id>` | clipped image | low |
| `picq <id>` | query image attributes | low |
| `qrcode …` | render QR code | low — out of scope for dashboard |
| `crcputh <addr> "..."` | hex put with CRC | medium — diagnostic tool use |
| `tswS …` | touchscreen sleep | medium |
| `wepo …` / `wept …` | external EEPROM write | low |
| `repo …` / `rept …` | external EEPROM read | low |
| `cfgpio <id>,<mode>,<obj>` | GPIO config | low — physical pins, irrelevant |
| `pio<n>=v` / `pwm<n>=v` | GPIO write | low |
| `cur <obj>` | input cursor focus | low |
| `covx`, `covs`, `spstr` | conversion helpers | medium |
| `sendxy=<v>` | enable touch coord events | medium |
| `vid<n>` controls | video playback | out-of-scope |
| `rest`, `reset` | reboot / soft-reset | medium — easy to model |
| `code_c` | code coverage / metadata | low |
| `lcd_dev` / `lcd_devc` | LCD device queries | low |

### Component types not rendered

| Type id | Component | Priority |
|---------|-----------|----------|
| 112 | Picture | low (no images in project) |
| 113 | Crop Picture | low |
| 58 | QR Code | low |
| 0 | Waveform | medium |
| 53 | Dual-state Button | medium — may appear in future projects |
| 56 | Checkbox | medium |
| 57 | Radio | medium |
| 67 | Switch | medium |
| 61 | Combo Box | low |
| 68 | Text Select | low |
| 60 | External Picture | low |
| 2 | Gmov | out-of-scope |
| 3 | Video | out-of-scope |
| 66 | Data Record | low |
| 63 | File Stream | low |
| 65 | File Browser | low |
| 5 | TouchCap | medium |

### Event types not implemented

The simulator runs `codesload`, `codesloadend`, `codesunload`, `codesup`,
`codesdown`, `codestimer`. NOT yet handled:

- `codesslide` — Slider movement event (drag delta)
- Touch coordinate events (`sendxy=1` mode, requires real coord-bearing events)
- `codesoffline` — disconnect handler (rare)

### Sim runtime feature gaps

- **Scrolling Text auto-scroll animation** (component type 55 renders as static text)
- **Effect transitions** (Nextion's `effect` attribute on show/hide)
- **Audio playback** (Nextion has `audio` components and `play` cmds)
- **Component visibility persistence** across page switches (already correct for `vis`, but `visible` persistence on re-load is unverified)
- **Multi-touch gestures** — single-touch only

## 7. Findings landed (resolved)

References everything we already know.

- [`A-hmi-format.md`](A-hmi-format.md) — HMI directory layout, append-only journal, `main.HMI` manifest, page header
- [`B-tft-roundtrip.md`](B-tft-roundtrip.md) — TFT field-mutability map, CRC algorithm, TFTTool's H2-corruption bug
- [`C-bytecode-opcodes.md`](C-bytecode-opcodes.md) — every opcode in this firmware is documented; `nxt-1.67.1 ≡ nxt-1.65.1` for the subset
- [`D-page-rasters.md`](D-page-rasters.md) — TFT contains zero rasters; resource directory layout
- [`E-procedural-preview.md`](E-procedural-preview.md) — static-attribute preview tool
- [`F-zi-fonts.md`](F-zi-fonts.md) — ZI v3/v5/v6 parser + integration

## 8. Progress log

Append a dated entry every time something here changes status.

| Date | What happened |
|------|---------------|
| 2026-05-09 | Roadmap created. Scoped 16 HMI unknowns, 8 TFT unknowns, 3 ZI unknowns, ~20 sim command gaps. 14 numbered experiments queued; user dropped 7 ad-hoc experiments at top level. |
| 2026-05-09 | Batch 1 analysed (`save A` as baseline). 6 findings landed in [`H-experiment-batch-1.md`](H-experiment-batch-1.md): H8 fully resolved (saves leak 5 bytes per save in fixed offsets), T9 resolved (H1+0x3c = file_size), T1 partly progressed (H2 is deterministic from H1). 3 new unknowns surfaced: H17, H18, H19. |
| 2026-05-09 | Findings F1–F6 documented + `findings/H-experiment-batch-1.md` committed alongside roadmap update. |
| 2026-05-09 | Cleanup: removed `02_dim_default`, `03_baud_change`, `12_save_no_change` experiment folders — fully resolved by batch 1. Open queue down from 14 to 11. |
| 2026-05-09 | Caveat added to F4: user reported that the orientation flip required relocating/resizing components to keep them in-screen, so the 21 KB user-code diff includes user-driven layout changes, not just orientation. F1/F2/F3/F5/F6 remain clean. T1 progress unaffected (H1↔H2 mapping doesn't depend on user-code body). |
| 2026-05-09 | Batch 2 analysis (`tests/editor outputs/{01,04..11,13}/`). 13 findings landed in [`I-experiment-batch-2.md`](I-experiment-batch-2.md). Resolved: H8 (rev), H9, H11, H17, H18. Narrowed: H1, H6, H10, H12, H15, T1. New unknown: H20 (orientation field location). Key revision: experiments were cumulative (each builds on previous), and pure no-change saves are byte-identical at the TFT level (revising F2). |
| 2026-05-09 | G10 revised by user clarification: 180° flip didn't need component relocation (only the earlier 90° flip did). So H1+0x14 *is* the orientation byte, and the reason every post-baseline experiment shows it at 0x03 is the cumulative experiment chain — 01 set it to 0x03 (180°) and all later saves inherited. **Crucial:** 180° rotation is runtime-applied (user code unchanged); only 90°/270° rebake coords. H20 resolved. |
| 2026-05-09 | Page CRC brute force: 384 standard CRC-32 variants × 9 payload-range hypotheses, no match. Algorithm is custom — not a textbook CRC. Tabling H1 pending deeper RE; suggested experiments could narrow it (e.g. CRC over zero payload). |
| 2026-05-09 | H2 transform analysis ([J](findings/J-h2-transform-analysis.md)): diffusion map decoded — file_size byte 0 affects H2 bytes {0x00,0x04,0x0c,0x18,0x1c}; byte 1 affects {0x01,0x1d,0x34,0x38,0x3a}. Each input byte → 5 output bytes. Not a fixed-pad XOR. Full inversion needs single-byte fs-delta experiments. |
| 2026-05-09 | TFTTool source review ([K](findings/K-tfttool-source-h2-attack.md)): T0/K0 use 4-byte repeating XOR with model-keyed values. Tested same scheme on F-series with two known-plaintext addresses (ressources_files at H2+0x08 = 0x10000; pictures at H2+0x18 = 0x70000) — keys derived inconsistent, so F-series uses a DIFFERENT cipher (longer key, OTP-like, or non-XOR). Partial pad recovered at H2+0x08..0x0b. Architecture-level insight: the diffusion observations are about which addresses shift when file_size grows; not about cipher structure. |
| 2026-05-09 | **F-series cipher structurally cracked** ([L](findings/L-h2-cipher-cracked-half.md)). Self-correlation analysis found 32-byte cycle (26.2% match at stride 32 vs <2% elsewhere). Cipher is 32-byte repeating XOR. 16 of 32 key bytes recovered using known plaintexts (resources/usercode/videos/audios addresses). The other 16 (positions 0x10..0x1f) remain unknown — a Picture-component experiment would likely crack them. Once full key is recovered, F-series TFTs become fully readable AND modifiable (no more TFTTool corruption bug). |
| 2026-05-09 | Experiments 15 (Picture) + 16 (loop) analysed ([M](findings/M-loop-bytecode-and-pic.md)). 15 added 132 KB (picture data); 16 added 64 bytes. Cross-experiment H2 key derivation produced CONFLICTS at every count-field position, revealing F-series H2 schema doesn't exactly match TFTTool's T0/K0 layout (count fields at different offsets). Found new structural info on Path C's "global memory directory": first u32 is its size, subsequent u32s are TLV-style pointer entries that shift when local int vars are added. T1 still 16/32 — needs F-series schema recovery to decode the count-field positions. |
| 2026-05-10 | **H1 RESOLVED** ([Q](Q-page-crc-cracked.md)). Page CRC algorithm extracted from native `achmi.dll` (itself extracted from the decrypted `achmiface.dll`'s embedded resource). It's a five-segment chained byte-wise CRC-32/MPEG-2: body sweep + extra micro-CRCs over `datasize`, `datainformationqyt`, `pagelock`, `hmiffid`. The byte-wise CRC32 uses 4 mixing rounds per byte instead of the textbook 1, which is why brute force missed it. `scripts/page_crc.py` verifies all 4 baseline pages and exp-04. Same approach (extract+disassemble achmi.dll) is now the obvious move on T1's H2 cipher (199 other subcommands in the same dispatch table). |
