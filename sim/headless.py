"""Headless host for the simulator — same wiring as App but without Tk.

Renders the active page to a PNG every tick (when state is dirty) and runs
forever, draining the transport and ticking timers. Useful in environments
without a display (CI, remote shells) and as a non-Tk smoke target.

Touch event input isn't possible without a UI, but commands flow over the
same transport just as in App, so MCU-side testing works headlessly.
"""
from __future__ import annotations
import logging
import time
from pathlib import Path

from PIL import Image

from sim.state import DisplayState, ScriptContext
from sim.parser import parse, PageSwitch, TouchInject
from sim.exec import execute
from sim.renderer import Renderer
from sim.transport import Transport
from sim.timer import TimerScheduler
from sim import script as sim_script
from sim import procs as sim_procs

log = logging.getLogger("sim.headless")


class HeadlessApp:
    """Mirror of `App` but writes frames to disk instead of a Tk canvas."""

    def __init__(
        self,
        state: DisplayState,
        transport: Transport,
        out_path: str | Path = "work/live.png",
        tick_ms: int = 33,
        log_commands: bool = False,
    ):
        self.state = state
        self.transport = transport
        self.renderer = Renderer()
        self.out_path = Path(out_path)
        self.tick_ms = tick_ms
        self.log_commands = log_commands
        self.timer_sched = TimerScheduler(state)
        self._stopped = False
        # Same procedure surface as App, registered against `self` as the host.
        sim_procs.register_all(self)
        # Boot
        self._run_program_s()
        active = state.active_page
        self._run_event_block(active.events.get("codesload"))
        self._run_event_block(active.events.get("codesloadend"))
        self.timer_sched.reset(self._now_ms())

    # ---- ticking ----

    def _now_ms(self) -> int:
        return int(time.monotonic() * 1000)

    def _run_event_block(self, code) -> None:
        if not code or not code.strip():
            return
        try:
            sim_script.run(code, ScriptContext(self.state))
        except Exception:
            log.exception("event handler failed")

    def _run_component_event(self, comp, name: str) -> None:
        if comp is None:
            return
        self._run_event_block(comp.events.get(name))

    def _run_program_s(self) -> None:
        text = (self.state.program_s or "").strip()
        if not text:
            return
        ctx = ScriptContext(self.state)
        try:
            sim_script.run(text, ctx)
        except Exception:
            log.exception("Program.s failed")
        for name, idx in (("sys0", 0), ("sys1", 1), ("sys2", 2)):
            if name in ctx.locals:
                self.state.sys[idx] = int(ctx.locals[name])

    def _switch_page(self, page) -> None:
        if page is self.state.active_page:
            return
        old = self.state.active_page
        self._run_event_block(old.events.get("codesunload"))
        self.state.set_active(page)
        self._run_event_block(page.events.get("codesload"))
        self._run_event_block(page.events.get("codesloadend"))
        self.timer_sched.reset(self._now_ms())

    def _on_timer_fire(self, comp, event_name: str) -> None:
        self._run_component_event(comp, event_name)

    def _inject_touch(self, action: str, target) -> None:
        page = self.state.active_page
        c = page.by_id(target) if isinstance(target, int) else page.by_name(target)
        if c is None:
            log.warning("touch: unknown component %r on page %s", target, page.name)
            return
        if action in ("press", "click"):
            self._run_component_event(c, "codesdown")
        if action in ("release", "click"):
            self._run_component_event(c, "codesup")

    def handle_frame(self, frame: bytes) -> None:
        """Apply a single command frame; mirror of App.handle_frame."""
        if self.log_commands:
            log.info("RX: %r", frame)
        op = parse(frame)
        if isinstance(op, PageSwitch):
            target = (self.state.pages_by_id.get(op.target)
                      if isinstance(op.target, int)
                      else self.state.pages.get(op.target))
            if target is not None:
                self._switch_page(target)
            return
        if isinstance(op, TouchInject):
            self._inject_touch(op.action, op.target)
            return
        execute(self.state, op)

    def _drain(self) -> None:
        while True:
            frame = self.transport.recv_frame()
            if frame is None:
                return
            self.handle_frame(frame)

    def _redraw(self) -> None:
        img = self.renderer.render(self.state)
        # Atomic write so a watcher doesn't read a half-flushed file.
        # Pass format explicitly because the .tmp suffix would defeat
        # Pillow's extension-based format detection.
        tmp = self.out_path.with_suffix(self.out_path.suffix + ".tmp")
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(tmp, format="PNG")
        tmp.replace(self.out_path)

    def stop(self) -> None:
        self._stopped = True

    def step(self) -> bool:
        """Run one tick. Returns True if a redraw happened."""
        self._drain()
        self.timer_sched.tick(self._now_ms(), self._on_timer_fire)
        if self.state.dirty:
            self._redraw()
            return True
        return False

    def run(self) -> None:
        try:
            self._redraw()  # initial render
            while not self._stopped:
                self.step()
                time.sleep(self.tick_ms / 1000.0)
        finally:
            self.transport.close()


