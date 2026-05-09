# 02 — Default backlight

## Goal

Single small numeric H1 field change. Pairs with experiment 01 to map
where H1 numeric fields land in H2.

## Steps in the editor

1. `Tools → Settings`.
2. Find **Backlight** (or "Initial dim" / "Brightness on power-up").
3. Note the current value (probably 100). Change it to **50**.
4. Click OK.
5. `File → Save`. `File → Compile`.

## Files to drop in this folder

- `after.HMI`
- `after.tft`

## What we expect to find

- **H1**: one byte changes (the backlight value byte). H1 CRC updates.
- **H2**: one or two bytes differ.
- **Body**: identical.
- **`Program.s` (in HMI)**: probably unchanged — the editor stores the
  default in H1, not in `Program.s`. If we DO see a change in `Program.s`
  body, we've learned something about which fields propagate to it.
