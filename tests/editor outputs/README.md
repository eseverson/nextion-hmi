# Editor experiment outputs

A structured bundle of single-variable experiments to run in the Nextion
Editor. Each subdirectory contains an `instructions.md` describing one
isolated change. Drop the resulting `.HMI` + `.tft` pair into the same
folder and the diff helpers will tell us what bytes moved.

## Workflow

1. Start in the editor with whatever your *current* project state is. **You
   don't need to revert to the original** — your modified state is fine
   as a baseline as long as you save it once before making changes for an
   experiment.
2. **Do `00_baseline/` first.** It's just "save the project as-is" so we
   have a reference. Every other experiment diffs against this baseline.
3. For each numbered experiment, follow `instructions.md` exactly. Make
   one change, save, copy the resulting files into the folder.
4. Don't worry about doing them all at once — they're independent. Pick the
   ones whose hypothesis you care about most.

## File naming inside each folder

```
<NN>_<name>/
├── instructions.md       # what to do in the editor
├── after.HMI             # the project after the change (you save this)
└── after.tft             # the compiled output (you save this)
```

`00_baseline/baseline.HMI` and `00_baseline/baseline.tft` are the
reference; each experiment's `after.*` is diffed against those.

## Running the analysis

After dropping a pair into an experiment folder:

```bash
# From the repo root:
EXP="01_orientation_flip"
BL="tests/editor outputs/00_baseline"
NEW="tests/editor outputs/$EXP"

scripts/diff_tft.py "$BL/baseline.tft" "$NEW/after.tft" --xor-h2
scripts/diff_hmi.py "$BL/baseline.HMI" "$NEW/after.HMI"
diff <(scripts/inspect_hmi.py "$BL/baseline.HMI" --json) \
     <(scripts/inspect_hmi.py "$NEW/after.HMI" --json)
```

Or run all of them at once:

```bash
scripts/analyze_editor_outputs.py
```

That walks every `tests/editor outputs/*` folder containing an `after.*`
pair and produces a single combined report.

## Experiment matrix

| # | Name | Edits | Cracks |
|---|------|-------|--------|
| 00 | baseline | none | reference |
| 01 | orientation_flip | rotate 180° | H1↔H2 propagation, F-series XOR key |
| 02 | dim_default | change initial backlight | small H1 numeric field |
| 03 | baud_change | change project baud rate | H1 vs Program.s split |
| 04 | red_val_deadbeef | one Variable's val → 0xDEADBEEF | Variable layout in page payload, page CRC |
| 05 | text_qqqqqqqq | one Text's `txt` → `QQQQQQQQ` | known plaintext for body scanning |
| 06 | bco_magenta | one component bco → 0xF81F | colour encoding location |
| 07 | add_hotspot | add one Hotspot to main | per-component overhead, page directory growth |
| 08 | delete_component | delete one component | inverse of 07; tests deletion vs tombstoning |
| 09 | program_s_page1 | Program.s `page 0` → `page 1` | Program.s storage format |
| 10 | timer_extra_line | add one assignment line to main's Timer event | event-script payload growth |
| 11 | add_page | add an empty page | top-level directory growth |
| 12 | save_no_change | open + save, no change | save determinism / timestamp leakage |
| 13 | save_six_times | save → close → open → save → ... | filesystem-image hypothesis (Path A) |
