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
from sim import procs as sim_procs

log = logging.getLogger("sim.app")
TICK_MS = 33


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


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
        # Procedure handlers live in sim.procs and are shared with HeadlessApp
        # to keep the surface in lock-step. We're the "host" they close over.
        sim_procs.register_all(self)

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

    def handle_frame(self, frame: bytes) -> None:
        """Apply a single command frame as if it had arrived over transport.

        Public so introspection / HTTP control planes can inject commands
        without poking transport internals.
        """
        if self.log_commands:
            log.info("RX: %r", frame)
        op = parse(frame)
        if isinstance(op, PageSwitch):
            if isinstance(op.target, int):
                page = self.state.pages_by_id.get(op.target)
            else:
                page = self.state.pages.get(op.target)
            if page is not None:
                self._switch_page(page)
            return
        if isinstance(op, TouchInject):
            self._inject_touch(op.action, op.target)
            return
        execute(self.state, op)

    def _drain_transport(self) -> None:
        while True:
            frame = self.transport.recv_frame()
            if frame is None:
                return
            self.handle_frame(frame)

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
