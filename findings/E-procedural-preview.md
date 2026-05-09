# Path E — Linux page preview from HMI

Follow-up #4 from `REPORT.md`. Goal: render each Nextion page as a PNG on
Linux without running the official editor or the firmware.

**Result: 4/4 pages render correctly.** See `findings/E-preview-main.png` and
`findings/E-preview-settings.png` for sample output.

## What got built

`scripts/preview_page.py` — single Python entry point. Loads the HMI via the
`Nextion2Text` library (using its component-attribute parser), then walks
each page's components and draws them into a Pillow image at the panel's
native resolution.

```
python3 scripts/preview_page.py [--hmi PATH] [--out DIR] [--scale N]
```

PNGs land in `work/preview_<pagename>.png`.

## Approach: static-attribute rendering, not VM execution

The original follow-up scoped this as "partial Nextion VM that walks user-code
`page N` routines and executes `fill`/text/`progress-bar` draws". The
implementation is simpler than that and turned out to be sufficient for this
project: it reads each component's declared attributes (position, size,
RGB565 colors, default text/value, font index, alignment) and renders the
component directly. No bytecode execution.

Why this works here: Path D characterised this dashboard as "fully
procedural", but on closer inspection the procedural part is the editor's
*internal* rendering of components (XFloat / Text / Progress Bar). The HMI
still describes everything as components — there's no user-code that draws
the page from scratch via raw `fill`/`xstr` calls. So the components are the
source of truth and a static renderer captures them.

For projects that *do* draw entirely from user code (some Nextion demos and
older projects do), a real VM would be required. That's a strict
generalisation of this work.

## Coverage by component type

Implemented (renders):
- **Page** — fills bg with `bco` (or white when `sta=0`)
- **Text / Scrolling Text** — bg rect + aligned text in `pco`
- **XFloat / Number** — bg rect + formatted value, respecting `vvs0`/`vvs1`
- **Progress Bar** — outline + filled portion at `val%`
- **Button** — bg rect + centered label
- **Slider** — track + handle at `val`/`maxval`
- **Gauge** — outlined circle + radial pointer at angle = val−90°

Skipped (no static visual): **Variable, Hotspot, Timer**.

Unhandled types fall through to a red outline + `<type N>` label so they
remain visible in previews.

## Approximations vs. the real device

- **Fonts:** Liberation Mono Bold (or Pillow default) at ~70% of component
  height, not the project's `.zi` fonts (`liber-font.zi`, `liber-48.zi`).
  Decoding ZI fonts is doable via `hagronnestad/nextion-font-editor` and
  could be wired in for a higher-fidelity render. The current substitute is
  good enough for layout review.
- **Default values only:** XFloat `val=123456` is a placeholder you'd see
  in the editor; the device shows live values from the MCU. The preview is
  *what the page looks like at editor-default state*.
- **No event-script execution:** the per-XFloat color reactivity (e.g.
  `if(x0.val>2000) { x0.bco=red.val }`) is a `Timer` event — not run.
  Previews show the static `bco` set in the HMI.
- **`sta=0` ("no background")** is rendered as opaque white per nxt-doc.
  The real Nextion firmware leaves whatever was previously on screen
  visible; static previews can't model that.

## Per-page outcomes (this project)

| Page     | Components | Rendered | Notes |
|----------|------------|----------|-------|
| main     | 30         | yes      | Full dashboard — 9 XFloats, 9 Text labels, Progress Bar, big "Danger" warning |
| settings | 5          | yes      | "Brightness" label + Slider + "Update" button |
| gauge    | 2          | (blank)  | Only a full-page Hotspot routing to itself. Correctly blank. |
| error    | 2          | (blank)  | Only a full-page Hotspot routing to main. Correctly blank. |

The two "blank" pages aren't a renderer bug — they really have no visible
widgets in the HMI. They're navigation-only. A future VM-execution variant
might find draw calls in `codesload` event handlers, but for this project
those are empty too (verified — only `settings` has a `codesload`, and it's
`h0.val=dim`, no draws).

## Reuse / extension ideas

1. **ZI font integration** for accurate text. Plug
   `hagronnestad/nextion-font-editor`'s parser into `load_font()` and render
   from the project's actual glyphs.
2. **Timer-event preview snapshots:** parse the simple `if (cond) {x.bco=...}`
   patterns out of the Timer event script and render multiple previews,
   one per logical state (warning vs. nominal). Cheap, surfaces the design.
3. **Live preview server:** `--watch` mode that re-renders on HMI change.
   Useful when iterating outside the official editor.
4. **Component-aware editor backend:** the same component-attribute model
   used here is exactly what a Linux HMI editor would consume. The renderer
   becomes the editor's preview pane.
