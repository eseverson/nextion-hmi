# 00 — Baseline

## Goal

Capture your current project state as the reference every other
experiment will diff against. **No changes** — just save.

## Steps in the editor

1. Open your project in the Nextion Editor.
2. (Optional) `File → Save` to make sure the on-disk state matches what
   you see.
3. `File → Compile` to produce the `.tft`.

## Files to drop in this folder

- `baseline.HMI` — copy your project's `.HMI` file here
- `baseline.tft` — copy the compiled output here (it'll be next to the `.HMI`)

## Why this matters

Every other experiment diffs against this. If you redo experiments after
making any other edit to the project, **re-do this baseline first**.

## What we expect to learn

Nothing on its own. Reference only.
