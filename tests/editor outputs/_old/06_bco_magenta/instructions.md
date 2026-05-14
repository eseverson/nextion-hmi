# 06 — Distinctive bco color

## Goal

Locate where component `bco` (background colour) values live in the page
payload, with a colour value distinctive enough to grep for.

## Steps in the editor

1. Go to the **main** page.
2. Click the `x0` XFloat component.
3. Change its **bco** to `63519` (= `0xF81F`, pure magenta in RGB565).
   Some editor builds let you type the integer directly; others require
   the colour picker — pick a pure magenta there.
4. Save & compile.

## Files to drop in this folder

- `after.HMI`
- `after.tft`

## What we expect to find

- **HMI**: in main's `0.pa` blob, find the 16-bit value `1f f8` (LE) — it
  used to be `46 29` (decimal 10566 in LE bytes). The position is the
  `bco` field of `x0`'s component record. Path A's writeup notes the
  PageContentHeader layout (`<III` → 12 bytes per component pointer);
  this experiment pins down the per-component attribute layout.
- **TFT body**: the magenta value also appears in the resources/usercode
  region, in the same component's record.

## Why magenta

`0xF81F` is `1111 1000 0001 1111` in binary (R=31, G=0, B=31). It's
visually distinctive in a render and unlikely to collide with anything
else in the file (the project's palette is mostly dark `#282a36`-ish
blues and a few warning reds/cyans).
