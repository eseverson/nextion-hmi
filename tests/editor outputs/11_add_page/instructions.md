# 11 — Add a new page

## Goal

Top-level directory growth. Tells us how the editor allocates page IDs,
how `main.HMI` (the resource manifest) reacts, and whether the
0x80000-mirrored backup directory still gets re-mirrored on every save.

## Steps in the editor

1. In the page list, right-click → **Add Page** (or use Tools menu /
   page-add toolbar). Name the new page `scratch`.
2. Don't add any components — leave it empty.
3. Save & compile.

## Files to drop in this folder

- `after.HMI`
- `after.tft`

## What we expect to find

- **HMI directory header** at offset 0: `count` increments from 22 to
  somewhere higher (might be 23 + a tombstone, or +2 if `4.pa` and its
  metadata both get entries).
- **HMI directory at 0x80000 (backup)**: should be byte-identical to the
  primary copy. Confirms Path A's mirror hypothesis.
- **`main.HMI` blob (the resource manifest)**: gains a 16-byte entry for
  `4.pa` (or whatever the new id is). Its directory `size` field
  increases by 16.
- **HMI data area**: a new `<N>.pa` blob appears at the end. Its name in
  the directory is something like `4.pa`.
- **`Program.s`** is unchanged unless you also explicitly switched the
  startup page.

## Hypotheses to test

- Path A flagged the 0x380000 sentinel as a possible filesystem-image
  artefact. Does adding pages move the sentinel? (Probably not, but
  worth confirming.)
- Does the new page's id come from `len(pages)` or from the lowest-free
  numeric prefix? (We saw `0.pa = main`, `1.pa = settings`, etc.; new
  page likely `4.pa`.)
