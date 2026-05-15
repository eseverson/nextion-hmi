# Research saves

Drop pairs of HMI/TFT files here when running reverse-engineering
experiments. Use descriptive names that describe the *single* thing that
varies between saves so the diff helpers below can isolate each change.

## Naming convention

```
<batch>_<change-description>.HMI
<batch>_<change-description>.tft
```

Example:

```
xor1_baseline.HMI                # untouched starting point
xor1_baseline.tft
xor1_resolution_481x320.HMI      # only resolution changed
xor1_resolution_481x320.tft
xor1_distinctive_text.HMI        # added one Text "QQQQQQQQ"
xor1_distinctive_text.tft
```

## Helpers

- `scripts/tools/diff_hmi.py a.HMI b.HMI` — structured diff of two HMIs
- `scripts/tools/diff_tft.py a.tft b.tft` — structured diff of two TFTs (with
  H2-region focus for F-series XOR-key recovery)
- `scripts/tools/diff_bytes.py a b` — generic per-byte / per-region diff

Run from the repo root. All three scripts also accept `--out work/<name>.json`
to dump structured output for later analysis.

## Suggested experiment batches

See `findings/G-research-batches.md` (created when first batch lands) for
the catalogue.
