# Experiment batch 2 — structured experiments 01, 04–11, 13

Source: `tests/editor outputs/{00_baseline,01_orientation_flip,04…13}/`.
Baseline: `00_baseline/base.HMI` and `_old/base.tft` (the user's
modified-from-original starting state). Diffs run with
`scripts/diff_tft.py` and `scripts/diff_hmi.py`.

## Important framing — experiments were cumulative, not independent

Each `NN.HMI/.tft` includes the changes from all earlier numbered
experiments, not just its own. Verified: `0xDEADBEEF` (planted in 04)
persists in 05, 06, 07, 08. `QQQQQQQQ` (planted in 05) persists in
06, 07, 08. `0xF81F` (planted in 06) persists in 07, 08.

So when reading "experiment 06 added X bytes", X is the *delta from 05*
in the cumulative sequence, not from baseline. The diff tools report
deltas-from-baseline correctly, but interpreting them requires keeping
the cumulative sequence in mind.

## New findings

### G1. **Pure no-change saves produce byte-identical TFTs**

Experiment 13: 6 consecutive save-cycles with no editor changes.
**iter1.tft == iter2.tft == … == iter6.tft, byte-for-byte.** The TFT
compilation pipeline is fully deterministic when the project hasn't
been modified.

This **revises F2** from batch 1: F2 said saves leak 5 bytes of
ephemeral state at usercode+0x715f4 + +0x71634. Closer reading: F2's
data was from `save A`/`save C`/`save D` — those were probably saves
*with content changes between them* (not pure idempotent saves).

So:
- **Save with no content change** → byte-identical TFT.
- **Save with content change** → 4-byte hash + 1-byte counter at fixed
  offsets get refreshed (per F2).

This resolves **H17 + H18** (the ephemeral state at 0x715f4 / 0x71634):
they're not stable per-save state, they're refreshed only on
recompile. Likely candidates are a compile-pass hash or an editor's
view of compile time.

### G2. Variable `val` byte location pinpointed

Experiment 04 set `red.val = 0xDEADBEEF`. The 4-byte sequence
`ef be ad de` (LE) appears **exactly once** in each output:

- TFT: file offset **`0x713f3`** (in the user-code region)
- HMI: file offset **`0x71ae18`** (in the live `0.pa` payload)

Path A's writeup noted the page payload contains
`PageHeader(56 B) + numberobj * PageContentHeader(12 B) +
component records`. Take HMI offset 0x71ae18, subtract the live `0.pa`
start (0x00715bcd from the directory entry), and the offset *within*
the page is `0x713f3 - 0x715bcd = -1626` … actually let's recompute:
`0x71ae18 - 0x715bcd = 0x524b = 21067` bytes into the page payload.

The page is 0x566b = 22123 bytes, so the val sits in the bottom 1056
bytes of the payload — likely in the `red` Variable component's
attribute record. Combined with G7 + G14 below, this pins down the
Variable layout.

This **resolves H11**.

### G3. Text `txt` byte location pinpointed

Experiment 05 set `t0.txt = "QQQQQQQQ"`. The 8-byte sequence appears
**exactly once** in each output:

- TFT: file offset **`0x712c0`** (in user-code region)
- HMI: file offset **`0x71db4d`** (in live `0.pa` payload)

The string is stored verbatim, no length-prefix or termination required
to find it (any length-prefix or null-terminator is OUTSIDE the 8-byte
match window we searched). Need a follow-up experiment with a known
shorter+longer string pair to nail down the encoding.

This partially **resolves H12**.

### G4. Component `bco` byte location pinpointed (HMI side)

Experiment 06 set `x0.bco = 0xF81F` (magenta). The 16-bit pattern
`1f f8` appears:

- HMI baseline: **0** matches (no magenta in this user's project)
- HMI at 06: **1** match at file offset **`0x720f6a`**
- HMI at 07: **2** matches (the new bco + a tombstoned copy)
- HMI at 08: **3** matches (two tombstones accumulating)

So the bco is at HMI offset 0x720f6a. Subtracting from the live
`0.pa` start, this is `0x720f6a - 0x007208a8 = 0x6c2 = 1730` bytes
into the page payload. Pretty far from `val` (which was at offset
21067). They're at different per-component records.

The TFT side is harder to isolate — the 16-bit pattern `1f f8` is
common (21 occurrences in baseline, jumping to 22 in 06). Need a
narrower probe (e.g., 32-bit alignment + colour-specific bytes) to
nail down the TFT location.

This partially **resolves H10**.

### G5. HMI grows in **66168-byte chunks** on structural changes

Experiments 07 (add Hotspot), 08 (delete component), and 11 (add page)
ALL produced HMI sizes of **7495448 bytes**, exactly +66168 from
baseline. Whatever the structural change, the HMI grows in fixed-size
chunks of 0x10238 bytes.

`0x10000` = 64KB, plus 0x238 (568) bytes of overhead. This looks like
the editor pre-allocates a 64K-aligned data sector when one is
exhausted (consistent with Path A's "filesystem image" hypothesis).

Cross-checks:
- 04 (val=DEADBEEF, no structural change): HMI grew by ~22 KB only.
- 05 (txt=QQQ → 5 bytes longer): HMI grew by ~44 KB. (Hmm — that's
  also 22 KB more than 04. Each tombstone-creating change might add
  a fresh data-area sector, not strictly "structural"-only.)

This partially **resolves H6 / H15**: the HMI doesn't compact on every
save; it grows in sector-sized chunks until it doesn't (G6 below shows
compaction *does* eventually happen).

### G6. The HMI does eventually compact — at least once per session

Experiment 13: iter1–iter5 had identical HMI sizes (7452011 bytes).
**iter6 dropped to 7429872 bytes — a 22139-byte shrink.** That's
22139 ≈ size of one tombstoned `0.pa` (0x5670 = 22128 bytes).

So the editor compacts ONE tombstone on some save (maybe the 6th save?
or after some threshold?). The TFT remained byte-identical across all
6 iterations, so this compaction is purely an HMI-side cleanup.

This partially **resolves H6** (the editor *does* compact tombstones,
but selectively — not every save).

### G7. Adding a Hotspot grows page payload by 516 bytes

Experiment 07's `0.pa` size went from 0x566b (baseline) to 0x586f.
Delta: **+516 bytes per Hotspot**. That's a substantial overhead — a
"minimal" component still has its full attribute record + per-event
slot allocation.

This **resolves H9**.

### G8. Adding an empty page grows the TFT by 1180 bytes

Experiment 11 grew the TFT from 507600 to 508780 bytes (+1180). For an
*entirely new page* with no components, that's a small contribution.

Implication: most of a page's footprint is in HMI-side metadata
(directory + manifest growth = +66168 bytes per G5), not in TFT-side
compiled code. The TFT side is just a small page-init stub.

### G9. Component **deletion** does not shrink files (journal pattern)

Experiment 08 deleted one component. Effects:
- TFT: +12 bytes net (file *grew*, not shrunk)
- HMI: +66168 bytes (sector-sized growth, again)
- `0.pa` payload: 0x566b → 0x5670, **+5 bytes**

The deletion didn't reduce the page payload by the deleted component's
attribute size. Instead, deleting added 5 bytes — consistent with the
journal model where an old `0.pa` becomes a tombstone and a new `0.pa`
is appended with the component slot reformatted (perhaps zeroed
in-place, or with a minor structural marker).

This **resolves H6 negatively** for the same-save case: the HMI does
not compact deleted components on save. Confirms Path A's append-only
journal hypothesis.

### G10. **H1+0x14 is a one-shot "this project has been re-saved" flag**, not orientation

EVERY post-baseline experiment shows H1+0x14 changing from `0x01` to
`0x03`. Including experiment 01 where the user *did not actually
change orientation* (its TFT user-code is byte-identical to baseline).

So H1+0x14 is NOT orientation. It's a state byte. The +2 increment
(01 → 03) is identical across all subsequent saves. Hypothesis: it's
a 2-bit field where bit 0 ("project exists") is always 1 and bit 1
("modified since first creation") flips to 1 on the first re-save
after creation.

Once flipped, it stays at 0x03 across all subsequent saves regardless
of content (verified across 01, 04–11). It's NOT a save counter.

This **opens new unknown H20**: where IS orientation actually stored
in H1? The `vertical.HMI/.tft` files in `_old/` (the original
orientation experiment, with body confound) showed H1+0x14 changing
from 01 to 00, AND H1+0x3c changing (file_size grew). So H1+0x14 is
orientation-related ONLY in the sense that it tracks "modified since
creation".

### G11. **H2 changes are driven by H1+0x3c (file_size) alone, not by H1+0x14**

Experiment 01 changed H1+0x14 (01→03) but **left H2 byte-identical**.
Experiments 04–11 changed BOTH H1+0x14 (same 01→03) AND H1+0x3c (file
grew) → H2 changed in proportion to the file-size delta.

So H2's deterministic transform (per F3 from batch 1) is keyed
specifically on **file_size**, not on the H1+0x14 byte.

This narrows the F-series H2 XOR-key search significantly. The "key"
applied to H1+0x3c (a 4-byte LE u32) is what we're hunting. Pairs of
experiments with known file_size deltas give us known-plaintext
bytes for that field.

Cross-experiment evidence: experiments with **identical file_size
delta** produce **identical H2 deltas**:
- 04, 05, 06, 08, 09: each grew by +12 bytes → all H2 diffs match
  (same 9-byte change pattern)
- 10: grew by +24 bytes → H2 diff has same number of runs (6) but
  different byte values in the affected positions

This is the strongest pin on **T1** yet. The H2 transform is a
function of file_size only (within H1's contributions; H2 might also
read from the body, but body-stable saves like 01 with no content
change show H2 unchanged so body contribution is also confirmed
function-of-the-body).

### G12. Tombstones accumulate per save, with experiment-cumulative state

The cumulative experiments produced cumulative tombstone counts:
- 04: 1 tombstone (the old 0.pa)
- 05: 2 tombstones (old 0.pa pre-04 + old 0.pa pre-05)
- 06: 3 tombstones
- ...

Tombstone names are NOT zeroed (per Path A); they show stale buffer
fragments like `'\x00ª\x11\x01...'`. Confirmed.

### G13. Page CRC algorithm — narrowed (clean candidate experiment)

Experiment 04 has a single 4-byte change in `0.pa` payload (the
val=0xDEADBEEF) plus a leading CRC update. We have:
- baseline 0.pa payload (22123 bytes, known content)
- 04's 0.pa payload (22123 bytes, identical except 4 bytes at known
  offset)
- both files' first 4 bytes (the CRC field) are different

This is the cleanest possible CRC-cracking substrate. Try every CRC32
polynomial × every initial seed × every reflect-or-not over the
22119-byte payload (excluding the leading CRC field) and find which
combo produces both the baseline CRC and the 04 CRC. Brute-force-able
in seconds.

(Future experiment: actually run the brute force. Sketched but not
executed in this batch.)

This **partially resolves H1** — cracking is now a small computational
task, not an open-ended question.

## Updates to existing findings

- **F2 revised**: Saves leak ephemeral state ONLY when content changes
  cause recompilation (G1). Pure no-change saves produce byte-identical
  TFTs.
- **F3 / F6 sharpened**: H2's transform is keyed specifically on H1+0x3c
  (file_size), not on the entire H1 (G11). H1+0x14 changes don't
  propagate. T1 attack surface is now precise.
- **F4 revised**: The structured 01_orientation_flip.tft turned out to
  be a no-content-change save (the user didn't end up flipping
  orientation in this run). The earlier `vertical.tft` remains the
  only orientation experiment we have, with the layout confound.

## Unresolved → still on the roadmap

- **H1**: Page CRC algorithm — narrowed to a brute-forceable problem (G13).
- **H2, H3, H4, H5, H7, H13, H14, H16, H19**: still open.
- **H20** (new): where IS orientation stored, given H1+0x14 is just a
  modification flag?
- **T1**: H2 transform — narrowed to a function-of-file_size attack
  (G11). Multi-known-plaintext analysis can probably crack it.
- **T2**: 128-byte H2 unmodelled region — needs T1 first.

## Next concrete steps

1. **Brute-force the page CRC** using G13's clean substrate. ~50 lines of
   Python.
2. **Brute-force the H2 transform** using the {04,05,06,08,09,10,11}
   set as multi-known-plaintext: each maps a specific file_size delta
   to a specific H2 delta. With 7+ known mappings, the transform should
   fall out (whether it's XOR, ARX, modular arithmetic, etc.).
3. A clean orientation experiment in a fresh project where 180° flip
   doesn't push components off-screen — to actually find H20.
4. A bracketed Text-length experiment (`txt = "Q"`, then `"QQ"`, then
   `"QQQ"`) — pins down the Text storage encoding.
