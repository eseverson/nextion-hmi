# 04 — Distinctive Variable val

## Goal

Pinpoint where a Variable component's `val` lives in the page payload,
and crack the page CRC algorithm.

## Steps in the editor

1. Go to the **main** page.
2. Click the `red` Variable component (it's one of the colour-storage
   `Variable (int32)` slots — the one with `val: 64170` in `00_baseline`).
3. In its attributes panel, change **val** from its current value to
   `3735928559` (= `0xDEADBEEF`).
4. Save & compile.

If the editor rejects 3735928559 as out of range for a signed int32,
use `-559038737` instead — same bytes when interpreted as int32 LE
(`ef be ad de`), but signed-positive in the editor.

## Files to drop in this folder

- `after.HMI`
- `after.tft`

## What we expect to find

- **HMI**: the main page's `*.pa` blob will differ in 4 consecutive
  bytes containing the new value `ef be ad de` (LE), plus a different
  4-byte CRC at the start of the page blob. **Find the `de ad be ef`
  pattern, work backwards to find the field's offset within the page
  payload, and we've located Variable val storage.**
- **HMI directory**: the `0.pa` entry's `size` and `tail` fields are
  unchanged because the payload size didn't change.
- **HMI tombstone**: the previous version of `0.pa` should appear as a
  tombstone (deleted=1) — confirms Path A's append-only-journal hypothesis.
- **TFT body**: the compiled value also shifts. Hex-search for
  `ef be ad de` in the .tft to locate it; the surrounding bytes tell us
  the TFT's component-record layout.

## Bonus

Once you locate the page CRC field, the diff between baseline's `0.pa`
and after's `0.pa` is *exactly 8 bytes long* (4 for the val, 4 for the
CRC). That's the clean substrate for cracking the CRC algorithm: try
every CRC32 polynomial / seed combo against the modified payload until
one produces the new CRC value.
