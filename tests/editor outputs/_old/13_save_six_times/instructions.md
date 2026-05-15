# 13 — Six saves, no edits

## Goal

Validate Path A's "append-only journal + 512KB-aligned filesystem image"
hypothesis. Six iterations should make the data-area growth obvious.

## Steps in the editor

1. Start from a fresh baseline (load your project; or use this experiment
   in conjunction with 12).
2. **Save iteration 1**: copy `.HMI`/`.tft` to `iter1.HMI`/`iter1.tft`.
3. Make a trivial change (toggle one Variable's val 0→1→0 to mark dirty,
   ending at the same value). Save.
4. **Save iteration 2**: copy as `iter2.HMI`/`iter2.tft`.
5. Repeat 4 more times: small change → save → copy as `iter3.HMI` ...
   `iter6.HMI`.

End state: 6 pairs of files in this folder.

## Files to drop in this folder

- `iter1.HMI`, `iter1.tft`, …, `iter6.HMI`, `iter6.tft`

## What we expect to find

- **Each iteration's directory `count`** at file offset 0 grows by ~2
  per save (a tombstone + a new live copy of `0.pa`).
- **The data area** (everything after 0x700000) grows monotonically. By
  iteration 6 we should see 5 tombstoned copies of the modified entry.
- **The 0x80000 backup directory** stays in sync with the primary on
  every save.
- **The 0x380000 sentinel** stays at exactly that offset — its purpose
  is to mark a sector boundary.
- **At some iteration count**, the data area might cross 0x780000 (the
  next 512KB boundary if the file is laid out as a flash image). What
  happens then? If the editor compacts the file, we'll see the data
  area shrink and the directory `count` reset. If it just keeps
  growing, the filesystem-image hypothesis is wrong (or the limit is
  bigger).

## Analysis

```bash
for i in 1 2 3 4 5 6; do
  echo "=== iter$i ==="
  scripts/tools/diff_hmi.py "tests/editor outputs/00_baseline/baseline.HMI" \
                      "tests/editor outputs/13_save_six_times/iter$i.HMI" \
    | tail -5
done
```
