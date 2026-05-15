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

## Live simulator

Linux process that renders the dashboard and behaves like a real Nextion
panel: accepts the same `\xff\xff\xff`-framed serial commands the firmware
sends, runs HMI event-handler scripts (Timer reactivity, page load/unload,
touch press/release), and emits canonical Nextion events back over the
wire when the user clicks a widget.

### Quick start

```bash
# Default Tk window, listens on tcp://127.0.0.1:9999
python3 scripts/sim/nextion_sim.py

# In another terminal — bundled helper, frames each command for you:
scripts/sim/send.py 'x0.val=12345' 's0.txt="MAP Error"' 'page settings'
scripts/sim/send.py --touch m0                          # click the hotspot
scripts/sim/send.py --state --http-port 8080            # JSON dump of sim state
```

### Transports (`--bind`)

| Spec | Use |
|------|------|
| `tcp:127.0.0.1:9999` (default) | Local TCP socket — the easiest |
| `tcp:0.0.0.0:9999`             | Reachable from another host |
| `pty`                          | Creates `/dev/pts/N` for serial-using code |
| `serial:/dev/ttyUSB0:115200`   | Open an existing serial device — hardware-in-the-loop |
| `stdin`                        | Read commands from stdin (scripted tests) |

### Other flags

- `--scale N` — integer pixel zoom for the Tk window
- `--start-page main` — initial active page
- `--headless [--headless-out work/live.png]` — no Tk; write rendered frames to a file each tick
- `--http 8080` — start an HTTP introspection / control server on the side
  - `GET /` — auto-refreshing live preview
  - `GET /frame.png` — current frame
  - `GET /state.json` — JSON dump (active page, sys vars, components, etc.)
  - `POST /command` — body is a Nextion command (no terminator)
  - `POST /touch` — body is `<target>[ press|release|click]`
- `--record session.jsonl` — capture all framed I/O to JSONL; replay later with `scripts/sim/replay.py`
- `--log-commands` — log every received frame at INFO

### Supported runtime command surface

`<obj>.<attr>=<expr>` (full expression RHS — arithmetic, comparison,
logical, attr refs), `<sys|dim>=<expr>`, `page <id|name>` (fires
`codesload`/`codesunload`), `cls`, `fill`, `line`, `cir`, `cirs`, `cle`,
`xstr`, `vis`, `tsw`, `print`, `printh`, `ref`. Plus the sim-only
extension `touch <target>[ press|release|click]` for scripted touch
injection.

Event handlers from the HMI run in real time: page load/unload, component
press/release, and Timer events. `Program.s` runs once at boot.

### Tests

`pytest tests/sim/` — 140+ tests covering parser, executor, transports,
renderer, scripts, expressions, draws, timer scheduler, headless mode,
HTTP server, recorder, and the firmware-replay snapshot.

### Design / plans

- `docs/specs/2026-05-09-nextion-simulator-design.md`
- `docs/specs/2026-05-09-nextion-simulator-plan.md` (P0)
- `docs/specs/2026-05-09-nextion-simulator-p1-plan.md` (P1)
