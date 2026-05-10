# Experiments 15 (Picture) and 16 (loop) — what they revealed

## Experiment 15 — adding a Picture

Adding one Picture component changed the project enormously:

- **TFT grew by 132060 bytes** (507600 → 639660). Most of that is the
  picture's pixel data in resources.
- **`ressources_files_size` grew by 0x20000 (128 KB)** — the picture
  added almost exactly one resource sector (consistent with G5's
  64 KB-aligned growth observation, but here doubled).
- **`usercode_address` shifted by 128 KB** (0x70000 → 0x90000), which
  pushed all post-resources content down.
- H1 file_size, ressources_files_size, and CRC all updated.
- **H2 changed at MANY positions**, including positions H2+0x10..0x1f
  (the previously-unknown half of the cipher key).

This is the cleanest "added one resource" data we have. It gives us
the cipher's behavior on the unknown half of the key cycle.

**Useful for cracking T1 in principle**, but cross-experiment plaintext
derivation conflicted with baseline (per [L](L-h2-cipher-cracked-half.md)).
This means the F-series H2 schema doesn't exactly match TFTTool's
T0/K0 layout — count fields are at different offsets, or extra fields
exist.

## Experiment 16 — adding a `for` loop

Adding a `for(int qq=0;qq<5;qq=qq+1) { sys0=qq }` (or similar) to one
event handler:

- **TFT grew by 64 bytes** total. usercode region grew by 64 bytes too.
- **Global memory directory grew by 4 bytes** (the `int qq` local var
  declaration adds a directory entry). The first u32 at usercode+0x00
  changed from 0x48 (72) to 0x4c (76) — the directory size. Two
  internal directory pointers at usercode+0x14 and +0x18 each
  incremented by 4.
- **Loop bytecode is ~60 bytes**: a few directory bytes + the actual
  compiled init/cond/body/step/backjump instructions.

**Difficulty in isolating the new opcodes:** the 4-byte directory
expansion shifted ALL subsequent bytecode by 4 bytes. Diff output
shows a long cascade of "differs by 4 bytes" regions across the
entire usercode. The actual NEW bytecode is somewhere mid-stream.

## What we learned about the global memory directory

Path C noted the directory at usercode+0x00 is mis-decoded by TFTTool
as instructions. From this experiment:

- **First u32 = directory size in bytes.** Baseline = 0x48, exp16 = 0x4c.
- **Subsequent u32s look like (offset, size) or (offset, count) tuples.**
  Two of them shifted by +4 in exp16 (positions 0x14 and 0x18 in the
  directory). They likely point into the directory itself — when the
  directory grew by adding a slot for `qq`, those pointers shifted to
  reflect the new slot's offset.
- The directory has **at least one slot per local int variable** in
  user code (across all event handlers).

This is new structural info about the directory format. The Path C
finding "block 0 is a global memory directory" can now be refined:
**block 0 is a TLV-like table where adding a local var inserts an
entry and shifts pointer fields.**

## Next steps

1. **Isolate the loop bytecode** — write a script that aligns base and
   e16 by tracing the directory shift, then extracts only the truly
   new bytes. The forward jump (loop entry → cond), the condition
   compare, the body, and the backward jump are the missing T6 opcodes.
2. **Re-derive F-series H2 schema** by combining experiment 15's
   pictures_count change (0 → 1) with what we observe at H2+0x30..0x3f.
   The byte that changes by exactly 0x01 between baseline and exp15 in
   that region tells us exactly where pictures_count lives.
3. Already-known: 16 of 32 cipher key bytes (per L). Those are correct
   for any F-series H2; the unknown 16 bytes need a new attack angle.
