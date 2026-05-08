# Nextion HMI/TFT exploration

Exploratory project to learn what we can about the Nextion `.HMI` and `.tft`
file formats using existing open-source tooling, and document any new findings.

## Scope

- **In scope:** static analysis, parsing, modifying-and-repacking, byte-level
  diffing, decoding undocumented fields.
- **Out of scope (for now):** flashing modified TFTs to a real Nextion device.
  Modifications stay on disk unless the operator explicitly opts in.

## Layout

```
source/      Reference HMI + compiled TFT from the miata-dash project
scripts/     Setup + helper scripts
tools/       Third-party tooling (gitignored — run scripts/setup.sh)
findings/    Per-path writeups produced during exploration
```

## Reference assets

`source/nextion.hmi.HMI` — 7.5 MB project file (Nextion Editor source)
`source/nextion.hmi.tft` — 493 KB compiled artifact, 480x320, model `CN2E`

## Bootstrap

```bash
./scripts/setup.sh    # clones TFTTool, nxt-doc, Nextion2Text into tools/
```

## Exploration paths

| ID | Path | Goal |
|----|------|------|
| A  | HMI directory format | Decode the binary directory header in the HMI file; extend nxt-doc's TBD spec |
| B  | TFTTool round-trip | Mutate-and-repack to map which TFT fields are losslessly mutable |
| C  | Bytecode opcodes | Cross-reference HMI source script with TFT bytecode; identify unknown opcodes |
| D  | Page raster extraction | Extract pre-rendered page imagery to PNG for Linux preview |

Each path's findings live in `findings/<path>-*.md`. The synthesis lives in
`REPORT.md` at the repo root after all paths complete.

## Known existing tools

- [UNUF/TFTTool](https://github.com/UNUF/TFTTool) — read/modify TFT, no encode
- [UNUF/nxt-doc](https://github.com/UNUF/nxt-doc) — format docs (TFT mostly, HMI/ZI TBD)
- [MMMZZZZ/Nextion2Text](https://github.com/MMMZZZZ/Nextion2Text) — read-only HMI dump
- [hagronnestad/nextion-font-editor](https://github.com/hagronnestad/nextion-font-editor) — ZI fonts (full read+write for v3, partial v5/v6)
