# 01 — Orientation flip

## Goal

Change one H1 field cleanly. The most likely propagator into H2 — and
therefore our best shot at the F-series H2 XOR key.

## Steps in the editor

1. Open your project (the same baseline state from `00_baseline/`).
2. `Tools → Settings`.
3. Find **Display Direction** (or "UI Orientation" — exact label depends
   on editor version). Note the current value.
4. Flip it 180° (e.g., if it was `0°` set it to `180°`; if it was
   `Vertical` set it to `Inverted Vertical`). Either flip is fine — the
   point is to change the field by *one step*.
5. Click OK / apply.
6. **If the editor complains that components are out of bounds, you
   must reposition or resize them — that's a confound that pollutes the
   user-code diff** (the H1/H2 part of the diff is still clean per F3).
   If you want a perfectly clean orientation experiment, build a
   throwaway project with everything inside a small central rectangle
   so a 180° flip never pushes anything off-screen.
7. `File → Save`.
8. `File → Compile`.

## Files to drop in this folder

- `after.HMI`
- `after.tft`

## What we expect to find

- **H1**: a single byte should differ at H1+0x00 (`old_lcd_orientation`)
  and/or H1+0x14 (`ui_orientation`).
- **H1 CRC** at H1+0xC4 should differ (auto-recomputed).
- **H2**: should also differ — this is the interesting part.
- **Tail file CRC** at the last 4 bytes should differ.
- **Body** (resources / usercode) should be byte-identical.

If the H2 region's diff has the same byte pattern as the H1 diff, we've
found a fixed-pad XOR (i.e., the same key XORs both). If H2's diff
pattern differs, the key is more complex.

After saving, run:

```bash
scripts/tools/diff_tft.py "tests/editor outputs/00_baseline/baseline.tft" \
                    "tests/editor outputs/01_orientation_flip/after.tft" \
                    --xor-h2
```
