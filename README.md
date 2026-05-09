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

## Live simulator (P0 + P1)

Linux process that renders the dashboard and accepts the same
`\xff\xff\xff`-framed serial commands the firmware sends. Pluggable transport
(TCP / PTY / stdin), Tk window, click-to-touch event emission. **P1 adds**
expression evaluation, `if`/`while`/`for`, event-handler execution, drawing
primitives, and the `sys0/sys1/sys2` + `dp` system variables. The Timer
event on the main page now fires automatically — the per-XFloat warning
colours flip on threshold without the firmware sending those writes.

```bash
# Start the simulator (listens on tcp://127.0.0.1:9999 by default).
python3 scripts/nextion_sim.py

# In another terminal — bundled helper, frames each command for you:
scripts/send.py 'x0.val=12345' 's0.txt="MAP Error"' 'page settings'

# Or by hand. Note: portable framing matters — OpenBSD nc takes -N to close
# stdin after EOF; nmap ncat uses --send-only; the helper above avoids both.
printf 'x0.val=12345\xff\xff\xff' | ncat --send-only 127.0.0.1 9999
```

`--bind pty` creates a `/dev/pts/N` path you can point real serial-using
code at (open it as a regular tty). `--bind stdin` reads commands from
stdin — useful for scripted tests.

Supported runtime command surface (TCP / PTY / stdin):
`<obj>.<attr>=<expr>` (with full expression RHS), `<sys|dim>=<expr>`,
`page <id|name>` (fires `codesload`/`codesunload`), `cls`, `fill`, `line`,
`cir`, `cirs`, `cle`, `xstr`, `vis`, `tsw`, `print`, `printh`, `ref`.
Event handlers from the HMI run in real time: page load/unload, component
press/release, and Timer events. `Program.s` runs once at boot.

Tests: `pytest tests/sim/` (116 passing). Design + plans:
`docs/specs/2026-05-09-nextion-simulator-{design,plan,p1-plan}.md`.
