# P1 Implementation Plan — Nextion simulator

Scope from spec section "P1 — deferred". Builds on P0 (commits up through
`791f909`).

**Goal:** Execute Nextion event-handler scripts on the simulator. Specifically:
- arithmetic / comparison / logical / parenthesised expressions
- `if` / `else if` / `else`, `while`, `for` control flow
- event-handler execution: `codesload`, `codesup`, `codesdown`, `codestimer`,
  `codesunload`
- local `int` variables
- drawing primitives `fill`, `xstr`, `line`, `cir`, `cls`, `cirs`, `cle`
- system variables `sys0`/`sys1`/`sys2` (RW), `dp` (RO)

After P1 the Timer event on the main page automatically fires its warning-color
reactivity — the per-XFloat `bco` flips to red/orange/yellow at value
thresholds without the firmware sending those writes.

## Module additions / changes

```
sim/
├── expr.py          NEW   tokenize + parse + evaluate Nextion expressions
├── script.py        NEW   statement parser + block executor (if/while/for, decls)
├── draw.py          NEW   per-page overlay buffer + fill/xstr/line/cir/cls
├── timer.py         NEW   periodic Timer event firing
├── state.py         EDIT  add ScriptContext, sys/dp, overlay attachment
├── parser.py        EDIT  Mutation RHS now accepts expressions (delegate to expr)
├── exec.py          EDIT  use expr.evaluate; expand Mutation; handle Print/PrintH
├── app.py           EDIT  invoke event handlers; tick the timer scheduler
└── renderer.py      EDIT  composite overlay onto rendered image
tests/sim/
├── test_expr.py     NEW
├── test_script.py   NEW
├── test_draw.py     NEW
├── test_timer.py    NEW
└── test_replay_firmware.py   EDIT — Timer reactivity reference render
```

## Tasks (10 total)

### T1 `sim/expr.py` — expression evaluator

Tokenizer (numbers, strings, idents, dotted `obj.attr`, `+ - * / %`, `< > <= >=
== != && || !`, parens, commas), recursive-descent parser, evaluator that takes
a `ScriptContext` and returns int or str. Comparison/logical produce 0/1.
Strings only support `+` (concat) and comparisons. Division on ints is integer
division (Nextion semantics).

### T2 `sim/script.py` — statements + control flow

Statement types: `IntDecl(names, exprs)`, `Assign(target, expr)`, `If(cond,
then_block, elif_blocks, else_block)`, `While(cond, block)`, `For(init, cond,
step, block)`, `Call(name, args)`, `Goto(label)`, `Label(name)`. Parser
operates on the plain-text source from `codes*` blobs, line-based with
`{` / `}` for blocks. Executor walks the AST against a `ScriptContext`,
delegating leaf assignments to `sim.exec.execute_assign(...)` so writes go
through the same path as TCP-driven mutations and dirty-marking stays
consistent.

`Call` covers procedural ops: `vis`, `tsw`, `cls`, `fill`, `xstr`, `line`,
`cir`, `cirs`, `cle`, `print`, `printh`, `page`, `ref`, `dim`, `sendme`,
`get`. Most are unimplemented stubs that log; the few drawing primitives are
delegated to `sim.draw`.

### T3 `sim/state.py` — ScriptContext + system variables

`ScriptContext` carries the active state, a per-frame `locals: dict[str,int]`,
and helpers to read/write a `name` (resolves locals first, then sys vars,
then component attrs). `state.sys` is a 3-int register `[sys0, sys1, sys2]`.
`state.read_attr("dp", None)` returns `state.active_page.id`. `dp` is read-only.

Page also gains `state.active_page.overlay: Optional[Image]` — set lazily by
the first draw primitive on the page; cleared on page switch.

### T4 `sim/parser.py` — RHS expressions

`_parse_value` is replaced by a routine that returns either a literal/AttrRef
(unchanged) or an `Expr` node from `sim.expr`. The `Unsupported` reject for
operators in RHS is removed. `sim.exec.execute` resolves either form
identically by calling `sim.expr.evaluate(value, ctx)`.

### T5 `sim/draw.py` — primitives

Lazy-initialised `Image.new("RGBA", (page.w, page.h), (0,0,0,0))` per page.
Functions: `fill(state, x,y,w,h, color565)`, `cls(state, color565)`,
`xstr(state, x,y,w,h, font_id, pco, bco, xcen, ycen, sta, text)`, `line`,
`cir`, `cirs`, `cle(state, x,y,w,h)`. Marks state dirty.

### T6 `sim/timer.py` — Timer scheduling

`TimerScheduler(state)` enumerates Timer components on every page (type 51),
holds per-component next-fire timestamps, exposes `tick(now_ms, run_block)`.
The app's tick loop calls it on every Tk frame. Only fires when the timer's
`en` attribute is 1 and the timer is on the active page (or has `vscope=1`,
but the miata project has none of those — local-only behaviour is fine for P1).

### T7 `sim/app.py` — event wiring

On page switch (set via `execute(PageSwitch)`), run `codesunload` of the old
page, then `codesload` then `codesloadend` of the new page. On press, run
`codesdown` of the hit component (replacing the P0 single-line navigation
hack). On release, run `codesup`. Per tick, call `TimerScheduler.tick`. Each
script run uses a fresh `ScriptContext` (`Program.s`'s globals are seeded into
`state.sys` once at boot).

### T8 `sim/renderer.py` — overlay compositing

After rendering components, alpha-composite the active page's `overlay`
(if any) on top, then apply the dim filter as before.

### T9 `tests/sim/` — new tests + replay update

`test_expr.py`, `test_script.py`, `test_draw.py`, `test_timer.py` each cover
their module's parse/eval/exec contract. `test_replay_firmware.py` gets a new
case that drives the firmware frames *plus* one Timer tick and asserts the
warning-color logic fired (e.g. high-RPM x1.bco flipped to red).

### T10 `Program.s` boot

On `load_hmi`, parse `Program.s` and run it once with a boot context (handles
`int sys0=0,...`, `baud=`, `recmod=`, `printh ...`, `page 0`). Currently
`Program.s` is loaded but not executed.

## Execution shape

- Sequential: T1 (expr) → T2 (script) → T3 (state additions). Each builds on
  the previous and the public API is small.
- Parallel: T4 (parser RHS), T5 (draw), T6 (timer) can run in three subagents
  on isolated worktrees once T1+T2+T3 are on main. None of them touch the
  same files.
- Sequential: T7 (app), T8 (renderer), T9 (tests), T10 (boot) wire it all up.

## Validation

- `pytest tests/sim/` ≥ 60 tests passing.
- `python3 scripts/sim/nextion_sim.py` boot + send `x1.val=7000` → x1 background
  goes red within ≤ 500 ms.
- Replay test snapshot updates to show the Timer reactivity result.
