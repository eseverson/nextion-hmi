# Experiment batch 1 — first round of editor experiment results

Source files: `tests/editor outputs/{save A,vertical,dim 66,230400 baud,
sleep 30,save C,save D}.{HMI,tft}`. `save A` used as implicit baseline.
Diffs run with `scripts/diff_tft.py`.

## Confirmed findings

### F1. `H1+0x3c` is the file size / end-of-content offset

Three experiments grew the file by exactly 12 bytes (`dim 66`,
`sleep 30`) or 500 bytes (`vertical`). In every case **H1+0x3c
incremented by exactly that amount**:

| Experiment | File grew by | H1+0x3c bytes (a → b) | Δ as u32 LE |
|------------|--------------|------------------------|-------------|
| dim 66     | +12          | `d0be0700` → `dcbe0700` | +12 |
| sleep 30   | +12          | `d0be0700` → `dcbe0700` | +12 |
| vertical   | +500         | `d0be0700` → `c4c00700` | +500 |

So the field at H1+0x3c is `file_size` (or `end_of_data_offset`) — a
4-byte little-endian u32. Updates automatically on every save where the
file grows or shrinks. Path B noted "H1+0x37..0x47 contains
ressources_files_*"; this experiment narrows the file_size sub-field to
exactly H1+0x3c.

### F2. Saves leak ephemeral state into a tiny region of usercode

`save A`, `save C`, and `save D` are diff-only consecutive saves of the
same project. Their H1, H2, h2-gap, resources, and the bulk of usercode
are byte-identical. They differ in **only two locations** of the
usercode section, plus the auto-computed tail CRC:

- **`0x715f4` (4 bytes)** — high-entropy random-looking bytes that
  change every save. `5de86e96` → `6271f9b6` (save C → save D).
  Hypothesis: a per-save hash, RNG salt, or wall-clock timestamp.
- **`0x71634` (1 byte)** — `0x00` → `0x01` between save C and D.
  Hypothesis: a save counter that increments by 1 each save.

This is the answer to roadmap **H8** ("Whether saves leak ephemeral
state"): yes, two locations totalling 5 bytes per save, in the usercode
section.

The "save A is identical to itself" test confirms the user code's bulk
is deterministic — only those 5 bytes are non-deterministic.

### F3. `H2` is deterministic from H1 + project content

When two experiments produce IDENTICAL H1 changes, they produce
IDENTICAL H2 changes. Concretely:

- `dim 66` and `sleep 30` both shifted H1+0x3c by +12 (file grew 12 B).
- They have the SAME H2 diff against `save A` (identical 9-byte change
  in the same 6 runs at H2+0x00, +0x04, +0x0c, +0x18, +0x1c, +0xc4).
- Their *user-code* changes are distinct (they encode different settings).

Independently:

- `230400 baud` made no H1 change (just 2 bytes in the user code).
- Its H2 is byte-identical to `save A`.

**Conclusion:** H2 is a deterministic function of H1 (most likely an
encrypted re-encoding of H1 metadata). The "F-series XOR key" we're
hunting is whatever transforms H1 into H2.

This significantly constrains the search. Pair this with experiment 01
(orientation_flip) — orientation cleanly mutates one H1 field at a known
offset, so the corresponding H2 difference IS the key applied to that
known H1 mutation. **Knowing what changed in H1 + observing what
changed in H2 directly reveals where the key is applied.**

### F4. Orientation rebuilds the user code with rotated coordinates

The `vertical` experiment was expected to be a single-bit flag flip.
Instead the TFT grew by 500 bytes and 21 KB of user code differed.
Investigation: the bytes that changed include literal coordinate values:

```
a=302c302c3438302c3332    "0,0,480,32"
b=302c302c3332302c3438    "0,0,320,48"
```

So the editor doesn't store "orientation" as a runtime hint to be
applied to a fixed coordinate system — it **bakes coordinate rotation
into the compiled output** at compile time. Components that were
declared at (0,0,480,320) now compile to (0,0,320,480) literals.

This affects roadmap **H1** indirectly: the page CRC is on the
post-rotation coordinates, so two project files that look "the same"
to the user but have different orientation produce wildly different
page payloads. To probe page CRC algorithm, we must keep orientation
constant.

### F5. `230400 baud` is a 2-byte-only change to compiled Program.s

The most surgical diff in the batch:
- One run, 2 bytes, at usercode offset 0x70058
- Bytes change from `c2 01` (= `0x01c2 << 8` ... wait, decoded as a
  Nextion bytecode int literal it's a 4-byte little-endian after the
  `03` opcode prefix, so `00c2 0100` = 115200, and `0084 0300` = 230400)

This validates Path C's bytecode encoding: ints are stored as
size-prefix + LE u32, exactly as documented.

### F6. H2 inert when H1 inert

Confirmed by the `230400 baud`, `save C`, and `save D` experiments —
all three left H1 byte-identical to `save A`, and H2 was byte-identical
in all three. This is the corollary of F3.

## Updates to roadmap

Marking these unknowns resolved (`[x]`):

- **H1 specific sub-field** (which u32 in the H1+0x37..0x47 range is
  file_size): F1
- **H8 saves leak ephemeral state**: F2 — yes, 5 bytes total per save
  at fixed offsets
- **H1 inert => H2 inert** (the key being deterministic): F3 + F6

New unknowns surfaced:

- **H17**: What does the 4-byte field at usercode+0x715f4 encode? (per-save hash? compile timestamp? RNG salt?)
- **H18**: What does the 1-byte counter at usercode+0x71634 mean? (save count? compile sequence number?)
- **H19**: How are coordinates encoded in the page payload such that orientation rebakes them? Layout to be mapped via `06_bco_magenta` + `07_add_hotspot`.

## Recommended next experiments

1. **Run experiment `01_orientation_flip` PROPERLY** — i.e., expect a
   500-byte change in usercode. Use the diff to map out where coordinate
   literals appear. The H2 diff should reveal H1↔H2 propagation
   precisely (since orientation only changes 7 H1 bytes at known offsets).

2. **`02_dim_default` and the existing `dim 66`** — once we have a clean
   `00_baseline`, diff against it to isolate where the dim default is
   stored in H1 / `Program.s` / both.

3. **Six saves with no changes** (`13_save_six_times`) — should confirm
   F2 by showing the 1-byte counter at 0x71634 incrementing by 1 each
   save.

4. **Cross-experiment XOR analysis** — XOR `dim 66`'s H2 with `sleep 30`'s
   H2; should yield zeros (since H1 changed identically). XOR with
   `vertical`'s H2 yields the H2 transform of orientation's H1 change.
