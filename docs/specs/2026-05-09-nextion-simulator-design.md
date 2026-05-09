# Nextion display simulator — design (P0)

A live Linux process that behaves enough like a Nextion display to (a) accept
the runtime commands the existing miata-dash firmware sends and update its
on-screen state correctly, and (b) provide a fast feedback loop for iterating
the dashboard UI without flashing real hardware.

This document covers the **P0 (MVP)** surface. P1 work — event-handler
execution, expressions, control flow — is sketched at the end of the spec
but deliberately deferred.

## Goals

1. **Protocol fidelity for the commands the miata-dash firmware actually
   sends.** Reading `src/main.cpp` of the parent firmware repo, the runtime
   command set is small: `<obj>.val=<n>`, `<obj>.txt="..."`, `<obj>.bco=…`
   (literal or `name.val` reference), `s0.bco=red.val`-style references,
   `page <id|name>`, all framed by `\xff\xff\xff`.
2. **Render fidelity good enough to iterate the UI.** Layout, colours,
   text, current values — drawn into a window that updates within one frame
   of a state change.
3. **Pluggable transport.** TCP socket by default; PTY for serial-using code;
   stdin for scripted tests.
4. **Touch events round-trip.** Clicking a visible widget that has a Touch
   Press handler emits the canonical Nextion `0x65 <page> <comp> <press>`
   event back to the controller.
5. **Validatable.** Replayable test fixtures exist for the firmware's exact
   command shapes; render output diffable against committed PNGs.

## Non-goals (P0)

- Executing event scripts (`codesload`, `codesup`, `codestimer`, etc.). The
  Timer-driven warning-color logic in the main page won't fire — manual
  `xN.bco=…` commands still work, but auto-reactivity does not.
- Expression evaluation beyond direct attribute references. `x=a+b` is
  rejected with a logged warning. `red.val` and `dim` are supported as
  values; `red.val+1` is not.
- Drawing primitives (`fill`, `xstr`, `line`, `cir`, `cle`). Path C found
  zero such opcodes in user code for this project, so this can wait.
- Audio, Gmov, video, file streams, QR code, scrolling-text animation,
  external picture, gauges with live needle. Most are unused; gauges
  render as a static circle for now (matching the existing previewer).
- TFT-side rendering parity. Substitute fonts (Liberation Mono Bold) are
  used; .zi font integration is a future enhancement. Visible text matches
  layout but not glyph shapes.
- Boot-time `Program.s` execution. Runtime serial config (`baud=`,
  `recmod=`) is acknowledged as a no-op.

## Architecture

```
                          ┌──────────────────────────┐
   bytes in    ─────►     │      Transport            │
   (tcp/pty/stdin)        │  framer (\xff\xff\xff)    │
                          └────────┬─────────────────┘
                                   ▼ List[bytes] frames
                          ┌──────────────────────────┐
                          │      Parser               │ ─► ParseError logged
                          │  text → AST (Mutation,    │
                          │     PageSwitch, …)        │
                          └────────┬─────────────────┘
                                   ▼ Operation
                          ┌──────────────────────────┐
                          │      Executor             │
                          │  applies Operation to     │
                          │  DisplayState; sets dirty │
                          └────────┬─────────────────┘
                                   ▼
                          ┌──────────────────────────┐         render request
                          │      DisplayState         │ ────────────────►
                          │  pages, comps, vars,      │
                          │  active_page, dim, dirty  │
                          └────────┬─────────────────┘
                                   ▲
                                   │ click → ComponentRef
                          ┌────────┴─────────────────┐
                          │       Renderer            │
                          │  Pillow image; Tk         │
                          │  window @ ~30 fps         │
                          └────────┬─────────────────┘
                                   ▼ Tk click event
                          ┌──────────────────────────┐
                          │     EventEmitter          │ ─► back over Transport
                          │  0x65 <page> <comp> p     │
                          └──────────────────────────┘
```

All four units talk through `DisplayState`. The renderer never mutates state;
the executor is the only writer. The transport is bidirectional but framing
is identical in both directions (commands and events both end with three
`0xff`).

## Modules

### `sim/state.py` — model

Pure data, no I/O.

- `RGB565`: int subclass with `.r/.g/.b` accessors and `to_rgb888()`.
- `Component`: name, id, type, full attribute dict (mutable). Carries
  bbox, default value, current value, current colors. Marks `dirty` on
  mutation.
- `Page`: name, id, list of components, page-level attributes (size, bco).
  Lookup helpers: `by_name(str)`, `by_id(int)`.
- `DisplayState`: `pages: Dict[str, Page]`, `pages_by_id: Dict[int, Page]`,
  `active_page: Page`, `dim: int`, `dirty: bool`. Built from an HMI file
  using the existing Nextion2Text-based loader extracted from
  `preview_page.py`.
- Variables (`type 52`) are addressable like components and live in their
  page's component table; `red.val` resolves the same way `t0.txt` does.
  Global-scope (`vscope=1`) variables are also indexed at the
  `DisplayState` level.

### `sim/parser.py` — text → AST

Input: a single command frame (bytes between two `\xff\xff\xff` markers,
decoded as latin-1).

Tokeniser handles: identifiers, dotted names (`red.val`, `t0.txt`), numbers,
quoted strings (with `\"` escapes per Nextion's grammar), `=`, commas.

Output: one of
- `Mutation(target: ComponentRef, attr: str, value: Value)`
- `PageSwitch(target: int | str)`
- `GlobalSet(name: "dim"|"dims"|"baud"|"recmod", value: int)`
- `Refresh(target: ComponentRef)` (logged, otherwise no-op — render is
  always live)
- `ClearScreen(color: RGB565)`
- `Print(text: str)` / `PrintH(bytes: bytes)`
- `NoOp(reason)` (e.g. `cls 0` issued before active page set)
- `Unsupported(text: str)` — anything we don't handle yet, logged at WARN

`Value` is a tagged union: integer literal | string literal | attribute
reference (`ComponentRef.attr`).

Parser is total: every input string returns one of the above. Malformed
syntax → `Unsupported(reason="parse error: …")`.

### `sim/exec.py` — AST → state mutation

Pure functions, given `(state, op) -> Optional[Event]`. Side-effect: marks
`state.dirty=True` when something visible changed.

Resolves `Value` references against state. A `Mutation` of `s0.bco=red.val`
reads the current `val` of the variable named `red` and assigns its
RGB565-interpreted result to `s0.bco`. Type coercion follows Nextion's
loose rules (numbers → `.val`, strings → `.txt`, RGB565 ints → color
attrs).

Unknown component → log warning, drop. (Real device returns `0x02 0x*`
"invalid component" event; emitting that is a stretch goal — log for now.)

### `sim/transport.py` — bytes I/O + event emission

Three backends, all expose:

```python
class Transport:
    def recv_frame(self) -> Optional[bytes]: ...   # \xff\xff\xff stripped
    def send_frame(self, payload: bytes) -> None: ...  # adds \xff\xff\xff
    def close(self) -> None: ...
```

Plus an `EventEmitter` helper in the same module that takes a `Transport`
and a `DisplayState` and exposes typed methods like `touch_press(comp)`,
`touch_release(comp)`, each of which constructs the right Nextion event
bytes and calls `send_frame`. The architecture diagram shows it as a
distinct box for clarity; in code it's a small class alongside `Transport`.

- `TcpTransport(host, port)`: `socket.create_server()`, accepts one
  connection at a time. New connection replaces the previous one.
- `PtyTransport()`: `os.openpty()`, prints the slave path
  (`/dev/pts/N`) on stdout.
- `StdinTransport()`: reads from stdin, writes events to stdout. For
  scripted tests.

Selected via `--bind tcp:127.0.0.1:9999 | pty | stdin`. TCP is default.

Framing is shared across backends: an internal buffer keeps unconsumed
bytes; `recv_frame` returns the next complete frame or `None` if
incomplete (non-blocking). The Tk loop polls.

### `sim/renderer.py` — DisplayState → pixels

Refactored from `scripts/preview_page.py`. Same `render_component` logic;
now takes a `DisplayState` and renders its `active_page` into a Pillow
image. Caller (Tk app) decides what to do with the image.

Adds: dim-aware rendering (multiplies output luminance by `state.dim/100`
clamped to `[0.05, 1.0]`).

### `sim/app.py` — Tk loop

Single window. Composition root:

1. Parse args, build `DisplayState` from the HMI file.
2. Construct the chosen `Transport`.
3. Tk root window, canvas the size of `state.active_page` × scale.
4. Tick at 33 ms (`root.after(33, tick)`):
   a. Drain the transport: every available frame → parser → executor →
      maybe send event back.
   b. If `state.dirty`: render, blit to canvas, clear dirty.
5. Mouse bindings on canvas: button-1 press → resolve to component →
   if it has a `Touch Press Event` → `EventEmitter.touch_press(state, c)` →
   sends `0x65 page comp 0x01 \xff\xff\xff` over transport. Button-1
   release does the same with `0x00`.

### `scripts/nextion_sim.py` — entry point

```
python3 scripts/nextion_sim.py \
    [--hmi source/nextion.hmi.HMI] \
    [--bind tcp:127.0.0.1:9999 | pty | stdin] \
    [--scale 1] \
    [--start-page main] \
    [--log-commands]
```

## Data flow examples

### Firmware sends `x0.val=12345\xff\xff\xff`

1. Transport accumulates bytes, returns one frame: `b"x0.val=12345"`.
2. Parser → `Mutation(ComponentRef("x0"), "val", IntLiteral(12345))`.
3. Executor finds `x0` on `active_page`, sets `c.attrs["val"]=12345`,
   marks dirty.
4. Next tick: renderer redraws active page, the XFloat at `x0`'s bbox
   shows `12345`.

### User clicks the m0 hotspot on `main`

1. Tk binding fires `<ButtonPress-1>` at canvas coords (445, 290).
2. App scales coords to native (445, 290 → same since scale=1) and
   resolves which component contains that point: `m0` (Hotspot, id=21).
3. m0 has `Touch Press Event` = `page 1`. P0 doesn't execute scripts —
   instead, the simulator's behaviour is: emit the `0x65` event AND, as
   a hardcoded convenience, treat single-line `page <n>` press handlers
   as a navigation hint. (This is a small concession that matches what
   the device does anyway.) State updates `active_page = pages["settings"]`.
4. Event bytes `0x65 0x00 0x15 0x01 \xff\xff\xff` go back to the
   controller. (page 0, comp 21 = 0x15, press = 1.)

The "execute single-line `page N` handlers" hack is the only piece of
script execution allowed in P0; it's narrow enough to spec exactly and
delivers visible navigation without dragging in the expression evaluator.

## Error handling

- Parser errors → log at WARN, drop frame, continue.
- Unknown component / attribute → log at WARN, drop, continue.
- Transport disconnect → wait for reconnect (TCP), or exit (stdin EOF).
- Renderer exceptions → log full traceback, render a red error overlay
  with the exception type, keep ticking.

The simulator is a debug/dev tool; failing fast is worse than logging and
continuing.

## Testing

`tests/sim/` directory:

- `test_parser.py` — table-driven parsing of every command shape from
  `src/main.cpp`, plus malformed input.
- `test_exec.py` — apply parsed ops to a fixture state, assert post-state.
- `test_replay_firmware.py` — replays the exact byte stream the firmware
  emits during one update cycle, asserts the resulting render matches a
  committed reference PNG (`tests/sim/fixtures/firmware_replay.png`).
- `test_transport_tcp.py` — round-trips frames through a real TCP socket.

Run: `pytest tests/sim/`. Tests are headless — they construct
`DisplayState` directly, never start Tk.

## Validation criteria for P0 done

- [ ] `python3 scripts/nextion_sim.py` opens a window showing the `main`
      page.
- [ ] Connecting to TCP and sending `x0.val=12345\xff\xff\xff` updates
      the visible "12345" within ~33 ms.
- [ ] Sending `s0.txt="MAP Error"\xff\xff\xff s0.bco=red.val\xff\xff\xff`
      changes the bottom warning text and its background color.
- [ ] Sending `page 1\xff\xff\xff` switches to settings.
- [ ] Clicking the settings page's `m0` hotspot emits a `0x65` event
      back over TCP.
- [ ] `pytest tests/sim/` passes including the firmware replay test.
- [ ] Total wall time from "press up arrow on firmware" → visible change
      ≤ 100 ms (subjective).

## P1 — deferred

The next planned phase. Out of scope here; mentioned for context.

- **Expression evaluator**: arithmetic (`+ - * /`), comparison (`< > == != >= <=`),
  logical (`&& ||`), parentheses. Operates on int values; string concat
  via the Nextion `.txt+="..."` form.
- **Control flow**: `if`/`else if`/`else`, `while`, `for`, `goto` labels.
  Match Nextion's slightly idiosyncratic syntax; the AST exists in the
  HMI as plain text already, so this is a parse-and-walk pass.
- **Event-handler execution**: `codesload`, `codesup`, `codesdown`,
  `codestimer`, `codesunload` runs at the right moments. Timer events
  fire at `tim` ms intervals when `en=1`.
- **Local variables**: `int sys0=0,sys1=0` in `Program.s`; per-event-frame
  locals if any.
- **Drawing primitives**: `fill`, `xstr`, `line`, `cir`. The renderer
  gets an "overlay buffer" that scripts draw into; reset on page switch
  unless `cls` is called.
- **System variables**: `dp` (current page id), `tch0..tch3` (touch
  coords), `sys0..sys2` globals, etc.

When P1 lands, the Timer event on the main page will fire its warning-color
reactivity automatically — currently the firmware would have to send those
`xN.bco=red.val` writes itself for them to be visible.

## Implementation strategy

Three parallel subagents, each on its own worktree:

1. **state + parser + executor** (no I/O, no rendering) — pure logic,
   easy to unit-test. Has the heaviest design surface area but no
   external deps.
2. **renderer extraction** — refactor `preview_page.py` into a
   `Renderer(state) -> Image` callable. State-aware, no Tk. Tests via
   PNG snapshot diffs.
3. **transport + Tk app + entry script** — wires the other two. Has
   the integration-test surface.

Then I integrate, run the firmware replay test, and commit.

## Risks / open questions

- **Tk on this system**: `python3-tk` may need to be installed. If
  unavailable, the app falls back to headless mode (renders to PNG on
  every dirty tick, writes to `work/live.png`). Cheap fallback.
- **PTY + Linux serial-port code**: a real `pyserial` client should
  open the printed `/dev/pts/N` path. Verified during transport tests.
- **Multi-frame parsing**: if the firmware sends two commands in one
  TCP write, framer must split correctly. Already in design — buffer +
  scan for `\xff\xff\xff`.
- **Event hotspot priority**: if multiple components overlap a click
  point, the highest-id (last-drawn) wins. Matches Nextion's behaviour
  per the docs.
- **Coordinate system**: this panel is `NX4832F035_011` (320×480) but
  the HMI declares `lcd_resolution_x=480, lcd_resolution_y=320` because
  it's mounted rotated. The renderer respects the HMI's coordinates;
  no rotation transform applied. The firmware code matches.
