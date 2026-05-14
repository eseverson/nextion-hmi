# 09 — Program.s startup page

## Goal

A single-character change inside `Program.s`. Pinpoints where Program.s
lives on disk and confirms its byte layout matches what we already think
(plain-text inside its directory entry's payload).

## Steps in the editor

1. Open `Program.s`.
2. Find the line `page 0` (it's the last line in the canonical
   miata-dash project; near the bottom).
3. Change it to `page 1`.
4. Save & compile.

## Files to drop in this folder

- `after.HMI`
- `after.tft`

## What we expect to find

- **HMI**: in the `Program.s` directory entry's blob, exactly one byte
  changes: ASCII `0` (0x30) → `1` (0x31).
- The Program.s payload's directory `size` field is unchanged (same
  length).
- A single tombstoned previous `Program.s` may appear; Path A noted that
  the existing file already has 1 tombstoned older Program.s.
- **TFT body**: the compiled `page` opcode's int operand changes from
  `0x30` → `0x31`. Cross-checks Path C's bytecode mapping that compiled
  `page <n>` literals are the ASCII byte, not a binary integer.
