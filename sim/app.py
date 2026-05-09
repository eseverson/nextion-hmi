from __future__ import annotations
import logging
import tkinter as tk
from PIL import ImageTk

from sim.state import DisplayState
from sim.parser import parse
from sim.exec import execute
from sim.renderer import Renderer
from sim.transport import Transport, EventEmitter

log = logging.getLogger("sim.app")
TICK_MS = 33


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
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

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
        # P0 navigation hack: if Touch Press handler (codesdown) is exactly
        # `page <n>`, honour it locally so navigation works without the
        # full script executor that lands in P1.
        code = (c.events.get("codesdown") or "").strip()
        if code:
            lines = [l.strip() for l in code.splitlines() if l.strip()]
            if len(lines) == 1 and lines[0].startswith("page "):
                execute(self.state, parse(lines[0].encode("latin-1")))

    def _on_release(self, ev):
        c = self._resolve_click(ev.x, ev.y)
        if c is None:
            return
        self.events.touch_release(self.state.active_page.id, c.id)

    def _drain_transport(self) -> None:
        while True:
            frame = self.transport.recv_frame()
            if frame is None:
                return
            if self.log_commands:
                log.info("RX: %r", frame)
            execute(self.state, parse(frame))

    def _tick(self) -> None:
        try:
            self._drain_transport()
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
        self.canvas.create_image(0, 0, anchor="nw", image=self._tk_image)

    def run(self) -> None:
        self._redraw()
        self.root.after(TICK_MS, self._tick)
        try:
            self.root.mainloop()
        finally:
            self.transport.close()
