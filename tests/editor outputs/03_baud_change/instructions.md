# 03 — Baud rate

## Goal

Test whether the project's baud rate setting lives in H1, in `Program.s`,
or both. If both, the duplication is interesting.

## Steps in the editor

1. Open `Program.s` (or whichever pane shows the global script).
2. Note the current `baud=` line. Change it from `baud=115200` to
   `baud=9600`.
3. Save & compile.

## Files to drop in this folder

- `after.HMI`
- `after.tft`

## What we expect to find

- **HMI `Program.s` payload**: a 6-character substring `115200` becomes
  `9600` (different lengths!). The whole `Program.s` blob shifts; its
  size in the directory changes; the directory entry's `size` field at
  +0x14 of the entry updates.
- **TFT body / usercode**: the compiled `baud=` opcode + literal
  changes. The integer literal value shifts.
- **H1**: probably unchanged (baud isn't an H1 field directly).
- **H2**: likely unchanged. If it changes, we've found another field.

This experiment is also a good cross-check on Path C's bytecode mapping:
the literal `115200` (= `0x1c200`) becomes `9600` (= `0x2580`) in the
compiled stream.
