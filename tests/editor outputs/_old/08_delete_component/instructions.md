# 08 — Delete one component

## Goal

Inverse of 07. Tests whether the editor compacts a page's payload when a
component is removed, or just rewrites the page with one fewer record.

## Steps in the editor

1. Go to the **main** page.
2. Click any non-load-bearing component — pick `s0` (the
   "Danger to manifold!!" Text at the bottom). Right-click → Delete (or
   press Delete).
3. Save & compile.

## Files to drop in this folder

- `after.HMI`
- `after.tft`

## What we expect to find

- **HMI**: main's `0.pa` blob shrinks. Its directory entry's `size` field
  decreases. `numberobj` decrements by 1.
- **HMI tombstone**: the previous `0.pa` (with the deleted Text still in
  it) lives on as a `deleted=1` entry — the data area grew, not shrunk,
  even though the live size shrunk. This is the journal-hypothesis tell.
- **TFT body**: shrinks correspondingly.

## Tip

If you want to do 07 *and* 08 cleanly, do them in different sessions
(reload baseline between). Otherwise the second experiment also tracks
the changes from the first.
