from __future__ import annotations
import logging
import time
import tkinter as tk
from tkinter import ttk
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

# Right-side inspector dimensions
INSPECTOR_W = 320  # pixels
LOG_LINES = 12     # recent commands kept in scrollback


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def _replace_text(widget: tk.Text, new_text: str, preserve_scroll: bool = False) -> None:
    """Replace a Text widget's contents while preserving scroll position
    (and not flickering the focus-ring). Caller is responsible for only
    invoking this when content has actually changed."""
    yview = widget.yview() if preserve_scroll else None
    widget.config(state=tk.NORMAL)
    widget.delete("1.0", tk.END)
    widget.insert(tk.END, new_text)
    widget.config(state=tk.DISABLED)
    if yview is not None:
        widget.yview_moveto(yview[0])


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
        self._command_log: list[tuple[str, str]] = []
        self._command_history: list[str] = []
        self._history_idx: int | None = None
        # Cache last rendered content so we don't trample the user's
        # selection / scroll position by rewriting widgets every tick.
        self._last_state_text = ""
        self._last_comp_text = ""

        self.root = tk.Tk()
        self.root.title("Nextion sim")
        self.root.configure(bg="#1e1e1e")

        # Top: split into [canvas | inspector]
        top = tk.Frame(self.root, bg="#1e1e1e")
        top.pack(fill=tk.BOTH, expand=True)

        page = state.active_page
        self.canvas = tk.Canvas(
            top,
            width=page.attrs["w"] * scale,
            height=page.attrs["h"] * scale,
            highlightthickness=0,
            bg="#000000",
        )
        self.canvas.pack(side=tk.LEFT)
        self._tk_image = None
        self._image_id = None
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self._build_inspector(top)

        # Bottom: command input strip
        cmd_frame = tk.Frame(self.root, bg="#1e1e1e", padx=4, pady=4)
        cmd_frame.pack(fill=tk.X)
        tk.Label(cmd_frame, text=">", fg="#9cdcfe",
                 bg="#1e1e1e", font=("monospace", 11)).pack(side=tk.LEFT)
        self.cmd_var = tk.StringVar()
        self.cmd_entry = tk.Entry(
            cmd_frame, textvariable=self.cmd_var,
            bg="#252526", fg="#dcdcdc", insertbackground="#dcdcdc",
            font=("monospace", 10), relief=tk.FLAT,
        )
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))
        self.cmd_entry.bind("<Return>", self._on_command_enter)
        self.cmd_entry.bind("<Up>", self._on_history_up)
        self.cmd_entry.bind("<Down>", self._on_history_down)
        send_btn = tk.Button(
            cmd_frame, text="Send", command=self._send_current_command,
            bg="#0e639c", fg="#ffffff", relief=tk.FLAT, padx=10,
        )
        send_btn.pack(side=tk.LEFT)

        self._register_procs()
        # Boot: run Program.s once (sets globals, baud, recmod, calls `page 0`).
        self._run_program_s()
        active = self.state.active_page
        self._run_event_block(active.events.get("codesload"))
        self._run_event_block(active.events.get("codesloadend"))
        self.timer_sched.reset(_now_ms())

    # ---------- Inspector panel ----------

    def _build_inspector(self, parent) -> None:
        """Right-side panel showing live state + recent commands."""
        panel = tk.Frame(parent, bg="#252526", width=INSPECTOR_W,
                         padx=6, pady=6)
        panel.pack(side=tk.RIGHT, fill=tk.Y)
        panel.pack_propagate(False)

        def hdr(parent, text):
            return tk.Label(
                parent, text=text, fg="#569cd6", bg="#252526",
                font=("monospace", 10, "bold"), anchor="w",
            )

        # Pack order matters: pack STATE at the top first, RECENT at the
        # bottom (which grabs the bottom space regardless of window size),
        # then COMPONENTS gets whatever's left.
        hdr(panel, "STATE").pack(side=tk.TOP, fill=tk.X, pady=(0, 2))
        self._state_text = tk.Text(
            panel, height=8, bg="#1e1e1e", fg="#dcdcdc",
            font=("monospace", 9), relief=tk.FLAT, padx=4, pady=2,
            wrap=tk.NONE,
        )
        self._state_text.pack(side=tk.TOP, fill=tk.X)
        self._state_text.config(state=tk.DISABLED)

        # RECENT — bottom of panel, fixed height. Pack BEFORE components
        # so it claims the bottom strip even on short windows.
        self._log_text = tk.Text(
            panel, height=LOG_LINES, bg="#1e1e1e", fg="#9cdcfe",
            font=("monospace", 9), relief=tk.FLAT, padx=4, pady=2,
            wrap=tk.NONE,
        )
        self._log_text.pack(side=tk.BOTTOM, fill=tk.X)
        self._log_text.config(state=tk.DISABLED)
        hdr(panel, "RECENT").pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 2))

        # COMPONENTS — middle, takes whatever's left.
        hdr(panel, "COMPONENTS").pack(side=tk.TOP, fill=tk.X, pady=(8, 2))
        comp_frame = tk.Frame(panel, bg="#252526")
        comp_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._comp_text = tk.Text(
            comp_frame, bg="#1e1e1e", fg="#dcdcdc",
            font=("monospace", 9), relief=tk.FLAT, padx=4, pady=2,
            wrap=tk.NONE,
        )
        comp_scroll = tk.Scrollbar(comp_frame, command=self._comp_text.yview)
        self._comp_text.configure(yscrollcommand=comp_scroll.set)
        comp_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._comp_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._comp_text.config(state=tk.DISABLED)
        # Tags for direction colouring
        self._log_text.tag_configure("rx", foreground="#dcdcaa")
        self._log_text.tag_configure("tx", foreground="#9cdcfe")
        self._log_text.tag_configure("ui", foreground="#c586c0")
        self._log_text.tag_configure("err", foreground="#f48771")

    def _refresh_inspector(self) -> None:
        """Refresh the state + components panels, but ONLY when content
        actually changed. We compare against a cached string so the user's
        selection / scroll / cursor isn't blown away every 33 ms when
        nothing meaningful has happened.
        """
        page = self.state.active_page
        new_state = (
            f"page    : {page.name} (id={page.id})\n"
            f"size    : {page.attrs.get('w')}x{page.attrs.get('h')}\n"
            f"dim     : {self.state.dim}\n"
            f"sys[0]  : {self.state.sys[0]}\n"
            f"sys[1]  : {self.state.sys[1]}\n"
            f"sys[2]  : {self.state.sys[2]}\n"
        )
        if new_state != self._last_state_text:
            _replace_text(self._state_text, new_state)
            self._last_state_text = new_state

        # Components — render to a string first, diff against cache.
        rows = []
        for c in sorted(page.components, key=lambda c: c.attrs.get("id", 0)):
            a = c.attrs
            line = f"{c.id:>3} {c.name:<10}"
            if a.get("txt") is not None:
                line += f" txt={a['txt']!r}"
            if a.get("val") is not None:
                line += f" val={a['val']}"
            if a.get("bco") is not None:
                line += f" bco={a['bco']}"
            rows.append(line)
        new_comp = "\n".join(rows) + "\n"
        if new_comp != self._last_comp_text:
            _replace_text(self._comp_text, new_comp, preserve_scroll=True)
            self._last_comp_text = new_comp

    def _log(self, direction: str, text: str) -> None:
        self._command_log.append((direction, text))
        if len(self._command_log) > LOG_LINES:
            self._command_log = self._command_log[-LOG_LINES:]
        # Append-only — never wipe the existing log content (which would
        # clobber any selection the user has). We just truncate from the top
        # if we exceed the line cap.
        self._log_text.config(state=tk.NORMAL)
        prefix = {"rx": "<-", "tx": "->", "ui": ">>", "err": "!!"}.get(direction, "  ")
        # If buffer has too many lines, drop the oldest from the widget too.
        line_count = int(self._log_text.index("end-1c").split(".")[0])
        if line_count > LOG_LINES:
            self._log_text.delete("1.0", f"{line_count - LOG_LINES + 1}.0")
        self._log_text.insert(tk.END, f"{prefix} {text}\n", direction)
        self._log_text.see(tk.END)
        self._log_text.config(state=tk.DISABLED)

    # ---------- Command-input handling ----------

    def _send_current_command(self) -> None:
        text = self.cmd_var.get().strip()
        if not text:
            return
        self.cmd_var.set("")
        self._command_history.append(text)
        self._history_idx = None
        self._log("ui", text)
        try:
            self.handle_frame(text.encode("latin-1"))
        except Exception as exc:
            log.exception("UI command failed")
            self._log("err", f"{type(exc).__name__}: {exc}")

    def _on_command_enter(self, _event) -> str | None:
        self._send_current_command()
        return "break"

    def _on_history_up(self, _event) -> str | None:
        if not self._command_history:
            return "break"
        if self._history_idx is None:
            self._history_idx = len(self._command_history) - 1
        else:
            self._history_idx = max(0, self._history_idx - 1)
        self.cmd_var.set(self._command_history[self._history_idx])
        self.cmd_entry.icursor(tk.END)
        return "break"

    def _on_history_down(self, _event) -> str | None:
        if not self._command_history or self._history_idx is None:
            return "break"
        self._history_idx += 1
        if self._history_idx >= len(self._command_history):
            self._history_idx = None
            self.cmd_var.set("")
        else:
            self.cmd_var.set(self._command_history[self._history_idx])
        self.cmd_entry.icursor(tk.END)
        return "break"

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

    def _register_procs(self) -> None:
        sim_procs.register_all(self)

    # ---------- Page switching with events ----------

    def _switch_page(self, target: Page) -> None:
        if target is self.state.active_page:
            return
        old = self.state.active_page
        self._run_event_block(old.events.get("codesunload"))
        self.state.set_active(target)
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
        self._log("tx", f"touch_press({page.name}.{c.name})")
        self._run_component_event(c, "codesdown")

    def _on_release(self, ev):
        c = self._resolve_click(ev.x, ev.y)
        if c is None:
            return
        self.events.touch_release(self.state.active_page.id, c.id)
        self._log("tx", f"touch_release({self.state.active_page.name}.{c.name})")
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
            self.events.touch_release(self.state.active_page.id, c.id)
            self._run_component_event(c, "codesup")

    def handle_frame(self, frame: bytes) -> None:
        """Apply a single command frame as if it had arrived over transport."""
        if self.log_commands:
            log.info("RX: %r", frame)
        self._log("rx", frame.decode("latin-1", "replace"))
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
            # Capture dirty BEFORE _redraw clears it; gate the inspector on
            # the same flag so we don't rewrite text widgets every 33 ms.
            was_dirty = self.state.dirty
            if was_dirty:
                self._redraw()
                self._refresh_inspector()
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
        self._refresh_inspector()
        self.root.after(TICK_MS, self._tick)
        try:
            self.root.mainloop()
        finally:
            self.transport.close()
