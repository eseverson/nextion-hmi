# 10 — Extra Timer event line

## Goal

Append a single new line to a complex existing event script. Pinpoints
where event-script bodies live within the component's att record, and
whether they're stored as plain text (HMI) and compiled bytecode (TFT)
both.

## Steps in the editor

1. Go to the **main** page.
2. Click the `tm0` Timer component.
3. Open its **Timer Event** code (in the editor's event panel).
4. Append a new line at the very end:

   ```
   sys2=42
   ```

   (This is a no-op effect-wise — `sys2` isn't read anywhere — but it
   adds a small known compilation to the event-handler payload.)
5. Save & compile.

## Files to drop in this folder

- `after.HMI`
- `after.tft`

## What we expect to find

- **HMI**: in main's `0.pa` blob, the `tm0` component's `codestimer`
  field grows by ~9 bytes (`\nsys2=42`). The page payload size grows
  accordingly; the directory entry's `size` increases.
- **TFT body**: the compiled bytecode for the Timer event grows by an
  expected amount: a sysvar reference (5 bytes: `04 02 00 00 00` for
  `sys2`), an `=` operator (1 byte), and an int literal (5 bytes:
  `03 2a 00 00 00` for `42`). About 11 bytes — gives us a clean
  experiment for verifying the size-prefix bytes around an instruction
  block.
