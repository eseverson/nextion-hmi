from __future__ import annotations
import logging
import time
import tkinter as tk
from PIL import ImageTk

from sim.state import DisplayState, Page, ScriptContext
from sim.parser import parse, PageSwitch, TouchInject
from sim.exec import execute
from sim.renderer import Renderer
from sim.transport import Transport, EventEmitter
from sim.timer import TimerScheduler
from sim import script as sim_script
from sim import draw as sim_draw
from sim.expr import parse as parse_expr, evaluate as eval_expr
from sim.script import _split_top_level

log = logging.getLogger("sim.app")
TICK_MS = 33


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def _ev_args(ctx, args_str: str) -> list:
    """Split args by top-level commas and evaluate each as an expression."""
    s = args_str.strip()
    if not s:
        return []
    pieces = _split_top_level(s, ",")
    return [eval_expr(parse_expr(p.strip()), ctx) for p in pieces]


class App:
    def __init__(
        self,
        state: DisplayState,
        transport: Transport,
        scale: int = 1,
        log_commands: bool = False,
    ):
        self.state = state
        self.transport = transport
        self.events = EventEmitter(transport)
        self.renderer = Renderer()
        self.scale = scale
        self.log_commands = log_commands
        self.timer_sched = TimerScheduler(state)

        self.root = tk.Tk()
        self.root.title("Nextion sim")
        page = state.active_page
        self.canvas = tk.Canvas(
            self.root,
            width=page.attrs["w"] * scale,
            height=page.attrs["h"] * scale,
            highlightthickness=0,
        )
        self.canvas.pack()
        self._tk_image = None
        self._image_id = None
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self._register_procs()
        # Boot: run Program.s once (sets globals, baud, recmod, calls `page 0`).
        self._run_program_s()
        # The Program.s `page 0` may have switched pages; either way fire
        # codesload of whichever page is now active.
        active = self.state.active_page
        self._run_event_block(active.events.get("codesload"))
        self._run_event_block(active.events.get("codesloadend"))
        self.timer_sched.reset(_now_ms())

    def _run_program_s(self) -> None:
        """Execute Program.s at boot. This handles `int sys0=0,...`,
        `baud=...`, `recmod=...`, `printh ...`, `page 0`. Any of those that
        we don't model is a no-op log.

        Program.s declares ints at module scope; those need to land in
        `state.sys`, not in a local frame that vanishes after boot. We do
        that by special-casing names matching `sys0/sys1/sys2`: an
        `int sys0=0` at boot pre-zeros `state.sys[0]` and is otherwise a
        no-op. Other locals from Program.s are simply discarded since
        Nextion has no general module-scope ints.
        """
        text = (self.state.program_s or "").strip()
        if not text:
            return
        ctx = ScriptContext(self.state)
        try:
            sim_script.run(text, ctx)
        except Exception:
            log.exception("Program.s failed")
        # Promote sys0/sys1/sys2 locals to state.sys so they persist.
        for name, idx in (("sys0", 0), ("sys1", 1), ("sys2", 2)):
            if name in ctx.locals:
                self.state.sys[idx] = int(ctx.locals[name])

    # ---------- Event-script execution ----------

    def _run_event_block(self, code: str | None) -> None:
        if not code or not code.strip():
            return
        ctx = ScriptContext(self.state)
        try:
            sim_script.run(code, ctx)
        except Exception:
            log.exception("event handler failed")

    def _run_component_event(self, comp, name: str) -> None:
        if comp is None:
            return
        self._run_event_block(comp.events.get(name))

    # ---------- Procedure registry (called from scripts) ----------

    def _register_procs(self) -> None:
        sim_script.register_proc("page", self._proc_page)
        sim_script.register_proc("ref", lambda ctx, a: None)
        sim_script.register_proc("vis", self._proc_vis)
        sim_script.register_proc("tsw", self._proc_tsw)
        sim_script.register_proc("cls", self._proc_cls)
        sim_script.register_proc("fill", self._proc_fill)
        sim_script.register_proc("line", self._proc_line)
        sim_script.register_proc("cir", self._proc_cir)
        sim_script.register_proc("cirs", self._proc_cirs)
        sim_script.register_proc("cle", self._proc_cle)
        sim_script.register_proc("xstr", self._proc_xstr)
        sim_script.register_proc("print", self._proc_print)
        sim_script.register_proc("printh", self._proc_printh)
        sim_script.register_proc("sendme", lambda ctx, a: None)
        sim_script.register_proc("get", lambda ctx, a: None)

    def _proc_page(self, ctx, args: str) -> None:
        target = args.strip()
        try:
            tgt_int = int(target)
            page = self.state.pages_by_id.get(tgt_int)
        except ValueError:
            page = self.state.pages.get(target)
        if page is None:
            log.warning("page: unknown target %r", target)
            return
        self._switch_page(page)

    def _proc_vis(self, ctx, args: str) -> None:
        # vis <objname>,<v>
        parts = _split_top_level(args, ",")
        if len(parts) != 2:
            return
        name = parts[0].strip()
        v = int(eval_expr(parse_expr(parts[1].strip()), ctx))
        c = self.state.active_page.by_name(name)
        if c is None:
            return
        c.set("vis", v)
        self.state.dirty = True

    def _proc_tsw(self, ctx, args: str) -> None:
        # tsw <objname>,<en>
        parts = _split_top_level(args, ",")
        if len(parts) != 2:
            return
        name = parts[0].strip()
        v = int(eval_expr(parse_expr(parts[1].strip()), ctx))
        c = self.state.active_page.by_name(name)
        if c is None:
            return
        c.set("tsw", v)

    def _proc_cls(self, ctx, args: str) -> None:
        vals = _ev_args(ctx, args)
        if vals:
            sim_draw.cls(self.state, int(vals[0]))

    def _proc_fill(self, ctx, args: str) -> None:
        v = _ev_args(ctx, args)
        if len(v) >= 5:
            sim_draw.fill(self.state, int(v[0]), int(v[1]), int(v[2]), int(v[3]), int(v[4]))

    def _proc_line(self, ctx, args: str) -> None:
        v = _ev_args(ctx, args)
        if len(v) >= 5:
            sim_draw.line(self.state, int(v[0]), int(v[1]), int(v[2]), int(v[3]), int(v[4]))

    def _proc_cir(self, ctx, args: str) -> None:
        v = _ev_args(ctx, args)
        if len(v) >= 4:
            sim_draw.cir(self.state, int(v[0]), int(v[1]), int(v[2]), int(v[3]))

    def _proc_cirs(self, ctx, args: str) -> None:
        v = _ev_args(ctx, args)
        if len(v) >= 4:
            sim_draw.cirs(self.state, int(v[0]), int(v[1]), int(v[2]), int(v[3]))

    def _proc_cle(self, ctx, args: str) -> None:
        v = _ev_args(ctx, args)
        if len(v) >= 4:
            sim_draw.cle(self.state, int(v[0]), int(v[1]), int(v[2]), int(v[3]))

    def _proc_xstr(self, ctx, args: str) -> None:
        # xstr x,y,w,h,font,pco,bco,xcen,ycen,sta,"text"
        pieces = _split_top_level(args, ",")
        if len(pieces) < 11:
            return
        ints = [int(eval_expr(parse_expr(p.strip()), ctx)) for p in pieces[:10]]
        text_expr = pieces[10].strip()
        text_val = eval_expr(parse_expr(text_expr), ctx)
        sim_draw.xstr(self.state, *ints, str(text_val))

    def _proc_print(self, ctx, args: str) -> None:
        s = args.strip()
        try:
            v = eval_expr(parse_expr(s), ctx)
            log.info("print: %s", v)
        except Exception:
            log.info("print: %s", s)

    def _proc_printh(self, ctx, args: str) -> None:
        try:
            payload = bytes(int(p, 16) for p in args.split())
            log.info("printh: %s", payload.hex())
        except ValueError:
            log.info("printh: (invalid) %s", args)

    # ---------- Page switching with events ----------

    def _switch_page(self, target: Page) -> None:
        if target is self.state.active_page:
            return
        old = self.state.active_page
        self._run_event_block(old.events.get("codesunload"))
        self.state.set_active(target)
        # Resize the canvas if the new page has different dimensions.
        if (target.attrs["w"] != old.attrs["w"]
                or target.attrs["h"] != old.attrs["h"]):
            self.canvas.config(
                width=target.attrs["w"] * self.scale,
                height=target.attrs["h"] * self.scale,
            )
        self._run_event_block(target.events.get("codesload"))
        self._run_event_block(target.events.get("codesloadend"))
        self.timer_sched.reset(_now_ms())

    # ---------- Touch handling ----------

    def _resolve_click(self, x: int, y: int):
        page = self.state.active_page
        nx, ny = x // self.scale, y // self.scale
        hit = None
        for c in sorted(page.components, key=lambda c: c.attrs.get("id", 0)):
            cx, cy = c.attrs.get("x", 0), c.attrs.get("y", 0)
            cw, ch = c.attrs.get("w", 0), c.attrs.get("h", 0)
            if cx <= nx < cx + cw and cy <= ny < cy + ch:
                hit = c
        return hit

    def _on_press(self, ev):
        c = self._resolve_click(ev.x, ev.y)
        if c is None:
            return
        page = self.state.active_page
        self.events.touch_press(page.id, c.id)
        self._run_component_event(c, "codesdown")

    def _on_release(self, ev):
        c = self._resolve_click(ev.x, ev.y)
        if c is None:
            return
        self.events.touch_release(self.state.active_page.id, c.id)
        self._run_component_event(c, "codesup")

    # ---------- Tick loop ----------

    def _resolve_touch_target(self, target):
        page = self.state.active_page
        if isinstance(target, int):
            return page.by_id(target)
        return page.by_name(target)

    def _inject_touch(self, action: str, target) -> None:
        c = self._resolve_touch_target(target)
        if c is None:
            log.warning("touch: unknown component %r on page %s", target, self.state.active_page.name)
            return
        if action in ("press", "click"):
            self.events.touch_press(self.state.active_page.id, c.id)
            self._run_component_event(c, "codesdown")
        if action in ("release", "click"):
            # For 'click', emit release on the same active page (which may
            # have changed if codesdown did `page N`). That matches what a
            # real user sees when they touch+release fast on a hotspot.
            self.events.touch_release(self.state.active_page.id, c.id)
            self._run_component_event(c, "codesup")

    def _drain_transport(self) -> None:
        while True:
            frame = self.transport.recv_frame()
            if frame is None:
                return
            if self.log_commands:
                log.info("RX: %r", frame)
            op = parse(frame)
            # Route page switches through _switch_page so codesload etc. fire.
            if isinstance(op, PageSwitch):
                if isinstance(op.target, int):
                    page = self.state.pages_by_id.get(op.target)
                else:
                    page = self.state.pages.get(op.target)
                if page is not None:
                    self._switch_page(page)
                continue
            if isinstance(op, TouchInject):
                self._inject_touch(op.action, op.target)
                continue
            execute(self.state, op)

    def _on_timer_fire(self, comp, event_name: str) -> None:
        self._run_component_event(comp, event_name)

    def _tick(self) -> None:
        try:
            self._drain_transport()
            self.timer_sched.tick(_now_ms(), self._on_timer_fire)
            if self.state.dirty:
                self._redraw()
        except Exception:
            log.exception("tick error")
        self.root.after(TICK_MS, self._tick)

    def _redraw(self) -> None:
        img = self.renderer.render(self.state)
        if self.scale != 1:
            from PIL import Image
            img = img.resize(
                (img.size[0] * self.scale, img.size[1] * self.scale),
                Image.NEAREST,
            )
        self._tk_image = ImageTk.PhotoImage(img)
        if self._image_id is None:
            self._image_id = self.canvas.create_image(
                0, 0, anchor="nw", image=self._tk_image)
        else:
            self.canvas.itemconfig(self._image_id, image=self._tk_image)

    def run(self) -> None:
        self._redraw()
        self.root.after(TICK_MS, self._tick)
        try:
            self.root.mainloop()
        finally:
            self.transport.close()
