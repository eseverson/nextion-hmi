# 07 — Add one Hotspot

## Goal

Measure the per-component byte cost of adding a new component. Cleaner
than adding a Text or XFloat because Hotspot has no font/text/value
attributes — it's just a bbox + event handlers.

## Steps in the editor

1. Go to the **main** page.
2. From the toolbox, drag a new **Hotspot** onto the page. Position it
   anywhere — top-left at (0, 0) is fine.
3. Note its auto-assigned `id` (probably the next free id).
4. Don't add any event scripts. Leave it empty.
5. Save & compile.

## Files to drop in this folder

- `after.HMI`
- `after.tft`

## What we expect to find

- **HMI**: main's `0.pa` blob grows by ~N bytes (one PageContentHeader +
  the Hotspot's attribute record).
- **HMI directory**: `0.pa`'s `size` field grows by the same amount.
  `start` may shift if other entries follow.
- **HMI page header `numberobj`** (at PageHeader offset +0x0C) increments
  by 1.
- **TFT**: similar growth in the usercode and/or resources region.
- **Tombstones**: previous `0.pa` is preserved as a deleted entry per
  Path A's append-only-journal hypothesis.

## Companion to 08

Run this *and* `08_delete_component` to learn whether deletion compacts
the data area or leaves a gap (the journal hypothesis predicts a gap).
