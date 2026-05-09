# 05 — Known plaintext text

## Goal

Plant a distinctive byte sequence we can grep for in the binary, to
locate where Text component `txt` strings live in both the HMI page
payload and the TFT body.

## Steps in the editor

1. Go to the **main** page.
2. Click any Text component (e.g., `t0` which currently says `kPa`).
3. Change its `txt` from `kPa` to `QQQQQQQQ` (8 capital Q's).
4. Save & compile.

## Files to drop in this folder

- `after.HMI`
- `after.tft`

## What we expect to find

- **HMI**: the byte sequence `51 51 51 51 51 51 51 51` ("QQQQQQQQ")
  appears verbatim somewhere in main's `0.pa` blob. Find it; it's
  preceded by a length prefix or terminated by a null/length byte.
- **TFT body**: the same `QQQQQQQQ` appears verbatim in the resources or
  usercode region. Its surrounding bytes tell us the TFT's text-storage
  layout.
- **Page CRC** changes (4 bytes at start of `0.pa`). Pair with experiment
  04 if you want two same-page diffs to validate CRC algorithm guesses.

## Tip

If the previous Text content was `kPa` (3 chars) and the new is
`QQQQQQQQ` (8 chars), the page payload size changes, which means
**other component records after this one shift** — the directory entry's
`size` updates accordingly. To keep the diff minimal, use a 3-character
distinctive string like `QQQ` instead.
