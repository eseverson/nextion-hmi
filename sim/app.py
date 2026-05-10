from __future__ import annotations
import json
import logging
import os
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from PIL import ImageTk

from sim.state import Component, DisplayState, Page, ScriptContext
from sim.parser import parse, PageSwitch, TouchInject
from sim.exec import execute
from sim.loader import load_hmi
from sim.tft_loader import load_tft
from sim.renderer import Renderer
from sim.transport import Transport, EventEmitter
from sim.timer import TimerScheduler
from sim import script as sim_script
from sim import procs as sim_procs

log = logging.getLogger("sim.app")
TICK_MS = 33

LOG_LINES = 12     # recent commands kept in scrollback
T_SLIDER = 1       # component-type id for sliders (matches renderer)
T_CHECKBOX = 56
T_RADIO = 57


def _settings_path() -> Path:
    """User-scoped settings file. XDG_CONFIG_HOME on Linux, ~/.config fallback."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "nextion-sim" / "settings.json"


# Orientation-byte mapping in the .tft H1+0x14 field. Mirrors nextion_sim.py.
_TFT_ORIENT = {0x00: 90, 0x01: 0, 0x02: 270, 0x03: 180}


def _detect_orientation(hmi_path: str) -> int | None:
    """Read the orientation byte from a sibling .tft, if one exists.
    Returns None if no .tft is alongside or the byte can't be parsed."""
    tft = Path(hmi_path).with_suffix(".tft")
    if not tft.exists():
        tft = Path(str(hmi_path).removesuffix(".HMI") + ".tft")
    if not tft.exists():
        return None
    try:
        with open(tft, "rb") as f:
            f.seek(0x14)
            byte = f.read(1)[0]
        return _TFT_ORIENT.get(byte, 0)
    except OSError:
        return None


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
        self._last_scripts_text = ""
        self._drag_slider = None
        # Currently-selected component for the SCRIPTS panel; set by tapping
        # on the canvas or clicking a row in the COMPONENTS list. None means
        # show only page-level + Program.s scripts.
        self._selected_comp: Component | None = None
        # Tracks the last HMI path the user opened (None for the initial
        # state, which load_hmi was called for outside the App). Used as
        # the initial directory for File → Open... and persisted to the
        # settings file so it stays as the default across runs.
        self._last_hmi_path: str | None = None
        # Loaded settings (display options + last paths). Populated lazily
        # near the end of __init__ so widgets exist when we apply.
        self._settings: dict = self._load_settings()
        # Suppress saves while we're applying loaded settings; we don't
        # want the cascade of BooleanVar.set() and section toggles during
        # boot to rewrite the file repeatedly.
        self._suppress_settings_save = True

        self.root = tk.Tk()
        self.root.title("Nextion sim")
        self.root.configure(bg="#1e1e1e")

        # Page selector bar (sits above the canvas — always visible so the
        # user can see all pages in the loaded file at a glance and switch
        # between them in one click).
        self._build_page_bar(self.root)

        # Top: canvas (alone, full-width row)
        canvas_frame = tk.Frame(self.root, bg="#1e1e1e")
        canvas_frame.pack(side=tk.TOP, fill=tk.X)

        page = state.active_page
        self.canvas = tk.Canvas(
            canvas_frame,
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
        self.canvas.bind("<B1-Motion>", self._on_motion)

        # Bottom: command input strip — pack BEFORE inspector so it claims
        # the bottom edge regardless of inspector size.
        cmd_frame = tk.Frame(self.root, bg="#1e1e1e", padx=4, pady=4)
        cmd_frame.pack(side=tk.BOTTOM, fill=tk.X)
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
        tk.Button(
            cmd_frame, text="Restart", command=self._restart,
            bg="#3a3d41", fg="#ffffff", relief=tk.FLAT, padx=10,
        ).pack(side=tk.LEFT, padx=(4, 0))

        self.show_outlines = tk.BooleanVar(value=False)
        tk.Checkbutton(
            cmd_frame, text="outlines", variable=self.show_outlines,
            command=self._on_overlay_toggle,
            bg="#1e1e1e", fg="#dcdcdc", selectcolor="#252526",
            activebackground="#1e1e1e", activeforeground="#dcdcdc",
            font=("monospace", 9), bd=0, highlightthickness=0,
        ).pack(side=tk.LEFT, padx=(8, 0))
        self.show_ids = tk.BooleanVar(value=False)
        tk.Checkbutton(
            cmd_frame, text="ids", variable=self.show_ids,
            command=self._on_overlay_toggle,
            bg="#1e1e1e", fg="#dcdcdc", selectcolor="#252526",
            activebackground="#1e1e1e", activeforeground="#dcdcdc",
            font=("monospace", 9), bd=0, highlightthickness=0,
        ).pack(side=tk.LEFT, padx=(4, 0))

        # Middle: inspector — fills remaining space between canvas and cmd bar
        self._build_inspector(self.root)
        self._build_menubar()
        # Now that both _page_option (page bar) and _pages_menu (menubar)
        # exist, populate them in sync with the loaded state.
        self._refresh_page_widgets()
        self._apply_settings(self._settings)
        # Settings applied — re-enable saving for any later UI interaction.
        self._suppress_settings_save = False
        # Save geometry / sash positions / overlay flags on window close.
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._register_procs()
        # Boot: run Program.s once (sets globals, baud, recmod, calls `page 0`).
        self._run_program_s()
        active = self.state.active_page
        self._run_event_block(active.events.get("codesload"))
        self._run_event_block(active.events.get("codesloadend"))
        self.timer_sched.reset(_now_ms())

    # ---------- Inspector panel ----------

    # Canonical column order — used both for build and for re-inserting
    # at the right position when re-showing a hidden section.
    _SECTION_ORDER = ("STATE", "COMPONENTS", "SCRIPTS", "RECENT")

    # ---------- Page selector ----------

    def _build_page_bar(self, parent) -> None:
        """A slim 'Page: [dropdown]' bar above the canvas, plus the
        plumbing for the matching menubar Pages submenu (built later)."""
        bar = tk.Frame(parent, bg="#1e1e1e", padx=4, pady=2)
        bar.pack(side=tk.TOP, fill=tk.X)
        tk.Label(
            bar, text="Page:", fg="#9cdcfe", bg="#1e1e1e",
            font=("monospace", 10),
        ).pack(side=tk.LEFT)
        self._page_var = tk.StringVar(value=self.state.active_page.name)
        # OptionMenu (vs ttk.Combobox) — simpler tk widget that respects
        # bg/fg colours so it blends with the dark theme. Items get
        # rebuilt on every state change via `_refresh_page_widgets`; we
        # seed it with the initial page list here.
        initial = [p.name for p in
                   sorted(self.state.pages.values(), key=lambda p: p.id)]
        self._page_option = tk.OptionMenu(
            bar, self._page_var, *initial,
            command=self._on_page_dropdown,
        )
        self._page_option.config(
            bg="#252526", fg="#dcdcdc", activebackground="#264f78",
            activeforeground="#ffffff", relief=tk.FLAT,
            font=("monospace", 10), bd=0, highlightthickness=0,
        )
        self._page_option["menu"].config(
            bg="#252526", fg="#dcdcdc", relief=tk.FLAT,
            activebackground="#264f78", activeforeground="#ffffff",
            font=("monospace", 10),
        )
        self._page_option.pack(side=tk.LEFT, padx=(4, 0))
        # Filled in by _build_menubar; kept on self so _refresh_page_widgets
        # can rebuild it after a file open.
        self._pages_menu: tk.Menu | None = None
        self._page_menu_var: tk.StringVar | None = None

    def _refresh_page_widgets(self) -> None:
        """Re-sync the page selector + menubar Pages submenu against the
        current `state.pages` and the active page. Call after every file
        open and from `_switch_page` so external page switches propagate
        back into the UI."""
        pages_sorted = sorted(self.state.pages.values(), key=lambda p: p.id)
        active_name = self.state.active_page.name

        # Rebuild the OptionMenu items.
        menu = self._page_option["menu"]
        menu.delete(0, "end")
        for p in pages_sorted:
            menu.add_command(
                label=p.name,
                command=lambda n=p.name: self._on_page_dropdown(n),
            )
        self._page_var.set(active_name)

        # Rebuild the menubar Pages submenu (if it's been built yet).
        if self._pages_menu is not None and self._page_menu_var is not None:
            self._pages_menu.delete(0, "end")
            for p in pages_sorted:
                self._pages_menu.add_radiobutton(
                    label=f"{p.name} (id={p.id})",
                    value=p.name,
                    variable=self._page_menu_var,
                    command=lambda n=p.name: self._switch_to_page_by_name(n),
                )
            self._page_menu_var.set(active_name)

    def _on_page_dropdown(self, name: str) -> None:
        """OptionMenu's command — fires when the user picks an item."""
        self._switch_to_page_by_name(name)

    def _switch_to_page_by_name(self, name: str) -> None:
        target = self.state.pages.get(name)
        if target is not None and target is not self.state.active_page:
            self._switch_page(target)

    def _build_inspector(self, parent) -> None:
        """Four side-by-side columns in a PanedWindow so the user can drag
        the sashes to resize each column independently:

            STATE | COMPONENTS | SCRIPTS | RECENT

        SCRIPTS shows the active page's event handlers + Program.s; when a
        component is tapped (canvas) or clicked (list), its event scripts
        are appended too and its row is highlighted in COMPONENTS.

        Each column is also independently hidable via the View menu — see
        `_set_section_visible`.
        """
        panel = tk.PanedWindow(
            parent,
            orient=tk.HORIZONTAL,
            bg="#1e1e1e",
            sashwidth=4,
            sashrelief=tk.FLAT,
            sashpad=0,
            bd=0,
            showhandle=False,
            opaqueresize=True,
        )
        panel.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._inspector_panel = panel

        def col(title: str, fg: str = "#dcdcdc", with_scroll: bool = False,
                wrap: str = tk.NONE):
            f = tk.Frame(panel, bg="#252526", padx=4, pady=4)
            tk.Label(
                f, text=title, fg="#569cd6", bg="#252526",
                font=("monospace", 10, "bold"), anchor="w",
            ).pack(side=tk.TOP, fill=tk.X, pady=(0, 2))
            body = tk.Frame(f, bg="#252526")
            body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            text = tk.Text(
                body, bg="#1e1e1e", fg=fg,
                font=("monospace", 9), relief=tk.FLAT, padx=4, pady=2,
                wrap=wrap, width=1, height=1,
            )
            if with_scroll:
                sb = tk.Scrollbar(body, command=text.yview)
                text.configure(yscrollcommand=sb.set)
                sb.pack(side=tk.RIGHT, fill=tk.Y)
            text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            text.config(state=tk.DISABLED)
            panel.add(f, stretch="always", minsize=80)
            return text, f

        self._state_text, state_frame = col("STATE")
        self._comp_text, comp_frame = col("COMPONENTS", with_scroll=True)
        self._scripts_text, scripts_frame = col(
            "SCRIPTS", with_scroll=True, wrap=tk.WORD)
        self._log_text, log_frame = col(
            "RECENT", fg="#9cdcfe", with_scroll=True)

        # Track each section's frame + visibility so View-menu toggles can
        # add/remove panes while keeping the canonical column order.
        self._section_frames: dict[str, tk.Frame] = {
            "STATE": state_frame,
            "COMPONENTS": comp_frame,
            "SCRIPTS": scripts_frame,
            "RECENT": log_frame,
        }
        self._section_visible: dict[str, bool] = {
            n: True for n in self._SECTION_ORDER
        }

        self._log_text.tag_configure("rx", foreground="#dcdcaa")
        self._log_text.tag_configure("tx", foreground="#9cdcfe")
        self._log_text.tag_configure("ui", foreground="#c586c0")
        self._log_text.tag_configure("err", foreground="#f48771")

        self._comp_text.tag_configure(
            "sel", background="#264f78", foreground="#ffffff")
        self._comp_text.bind("<Button-1>", self._on_comp_list_click)
        self._scripts_text.tag_configure(
            "header", foreground="#569cd6", font=("monospace", 9, "bold"))
        self._scripts_text.tag_configure(
            "subheader", foreground="#9cdcfe")
        self._scripts_text.tag_configure(
            "muted", foreground="#808080")

    # ---------- Settings persistence ----------
    #
    # The settings file holds user preferences that should survive across
    # process restarts: which inspector columns are visible, whether the
    # canvas overlays are on, the window geometry, the sash positions
    # between columns, and the last HMI path opened via File → Open.
    # It does *not* hold per-conversation state (command history, RECENT
    # log, current page, selected component, dirty bits) — those are
    # reconstructed from the live state each session.

    def _load_settings(self) -> dict:
        path = _settings_path()
        try:
            if path.exists():
                return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            log.warning("could not read settings file at %s", path)
        return {}

    def _save_settings(self) -> None:
        if self._suppress_settings_save:
            return
        sashes: list[int] = []
        try:
            n_panes = len(self._inspector_panel.panes())
            for i in range(max(0, n_panes - 1)):
                sashes.append(self._inspector_panel.sash_coord(i)[0])
        except (tk.TclError, AttributeError):
            pass
        data = {
            "sections": dict(self._section_visible),
            "show_outlines": bool(self.show_outlines.get()),
            "show_ids": bool(self.show_ids.get()),
            "geometry": self.root.winfo_geometry(),
            "sashes": sashes,
            "last_hmi_path": self._last_hmi_path,
        }
        path = _settings_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2))
        except OSError:
            log.warning("could not save settings to %s", path)

    def _apply_settings(self, settings: dict) -> None:
        """Apply a loaded-settings dict to the UI. Called once after the
        widgets are built and the menubar wired up so all the BooleanVars
        and section frames exist."""
        sections = settings.get("sections") or {}
        for name in self._SECTION_ORDER:
            visible = bool(sections.get(name, True))
            if name in self._section_menu_vars:
                self._section_menu_vars[name].set(visible)
            self._set_section_visible(name, visible)
        if "show_outlines" in settings:
            self.show_outlines.set(bool(settings["show_outlines"]))
        if "show_ids" in settings:
            self.show_ids.set(bool(settings["show_ids"]))
        if settings.get("geometry"):
            try:
                self.root.geometry(settings["geometry"])
            except tk.TclError:
                pass
        sashes = settings.get("sashes") or []
        if sashes:
            # Sash positions only stick once the window is fully mapped
            # and the PanedWindow has been laid out. Defer to idle.
            self.root.after_idle(lambda: self._apply_sash_positions(sashes))
        last_path = settings.get("last_hmi_path")
        if isinstance(last_path, str):
            self._last_hmi_path = last_path

    def _apply_sash_positions(self, sashes: list[int]) -> None:
        try:
            n_panes = len(self._inspector_panel.panes())
            for i, x in enumerate(sashes):
                if i < n_panes - 1:
                    self._inspector_panel.sash_place(i, int(x), 1)
        except (tk.TclError, ValueError):
            pass

    def _on_close(self) -> None:
        """WM_DELETE_WINDOW + File → Quit. Captures geometry / sash
        positions before the window is gone."""
        try:
            self._save_settings()
        finally:
            self.root.destroy()

    def _restart(self) -> None:
        """Save state, tear down the Tk root, and exec a fresh sim
        process with the same argv. Used by the toolbar Restart button
        for fast iteration when editing the HMI/TFT or renderer code."""
        try:
            self._save_settings()
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ---------- File open ----------

    def _on_file_open(self) -> None:
        initial_dir = None
        if self._last_hmi_path and os.path.dirname(self._last_hmi_path):
            initial_dir = os.path.dirname(self._last_hmi_path)
        path = filedialog.askopenfilename(
            title="Open Nextion file",
            initialdir=initial_dir,
            filetypes=[
                ("Nextion HMI / TFT", "*.HMI *.hmi *.tft *.TFT"),
                ("HMI source",        "*.HMI *.hmi"),
                ("TFT runtime",       "*.tft *.TFT"),
                ("All files",         "*.*"),
            ],
        )
        if not path:
            return
        self._open_file(path)

    def _open_file(self, path: str) -> None:
        """Replace the live DisplayState with one loaded from *path*.

        Dispatches to `load_hmi` or `load_tft` based on the file extension
        (`.HMI` is the editor source — full fidelity; `.tft` is the
        compiled runtime — partial fidelity, components-only). Anything
        else is tried as HMI on the assumption it's a renamed source file;
        if both loaders fail the user gets a clear error in RECENT.

        Keeps the transport, command history, RECENT log, scale, and
        overlay toggles intact so the swap is "open another file" rather
        than a full restart. Runs codesunload on the old page and the
        codesload boot sequence on the new one, mirroring page-switch
        semantics.
        """
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".tft":
                new_state = load_tft(path)
            else:
                new_state = load_hmi(path)
        except Exception as exc:  # noqa: BLE001
            log.exception("failed to load %s", path)
            hint = ""
            if ext not in (".hmi", ".tft"):
                hint = (" (expected .HMI or .tft — pass the editor source"
                        " or compiled runtime, not a sibling file)")
            self._log("err",
                      f"open failed: {os.path.basename(path)}: "
                      f"{type(exc).__name__}: {exc}{hint}")
            return
        if ext != ".tft":
            orient = _detect_orientation(path)
            if orient is not None:
                new_state.orientation = orient

        # Tear down the old page cleanly before swapping.
        old = self.state.active_page
        self._run_event_block(old.events.get("codesunload"))

        self.state = new_state
        self.timer_sched = TimerScheduler(new_state)
        self._selected_comp = None
        self._last_state_text = ""
        self._last_comp_text = ""
        self._last_scripts_text = ""
        self._last_hmi_path = path

        page = new_state.active_page
        self.canvas.config(
            width=page.attrs["w"] * self.scale,
            height=page.attrs["h"] * self.scale,
        )
        # New file → potentially different set of pages. Rebuild the
        # page-bar dropdown + menubar Pages submenu before booting the
        # new state so any script-driven page switch sees the right widgets.
        self._refresh_page_widgets()

        # Boot the new state — Program.s, then the active page's load events.
        self._run_program_s()
        self._run_event_block(page.events.get("codesload"))
        self._run_event_block(page.events.get("codesloadend"))
        self.timer_sched.reset(_now_ms())
        new_state.dirty = True
        self._log("ui", f"loaded {path}")
        self._save_settings()

    def _set_section_visible(self, name: str, visible: bool) -> None:
        """Show or hide an inspector column without losing its contents.

        PanedWindow.forget detaches a pane (the widget keeps its state);
        we re-add at the canonical position by finding the next visible
        section to the right and inserting before it.
        """
        if self._section_visible.get(name) == visible:
            return
        target = self._section_frames[name]
        panel = self._inspector_panel
        if not visible:
            panel.forget(target)
        else:
            order = list(self._SECTION_ORDER)
            i = order.index(name)
            before = None
            for n in order[i + 1:]:
                if self._section_visible.get(n):
                    before = self._section_frames[n]
                    break
            kw = {"stretch": "always", "minsize": 80}
            if before is not None:
                panel.add(target, before=before, **kw)
            else:
                panel.add(target, **kw)
        self._section_visible[name] = visible

    def _build_menubar(self) -> None:
        """Top menu bar: File / View. The View menu mirrors the cmd-bar
        overlay checkboxes (so they stay in sync via shared BooleanVar)
        and adds toggles for each inspector column."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(
            label="Open...", accelerator="Ctrl+O",
            command=self._on_file_open,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Quit", accelerator="Ctrl+Q",
            command=self._on_close,
        )
        self.root.bind_all("<Control-q>", lambda _e: self._on_close())
        self.root.bind_all("<Control-o>", lambda _e: self._on_file_open())

        # Pages — one entry per page in the loaded file (rebuilt by
        # `_refresh_page_widgets` whenever state.pages changes).
        self._page_menu_var = tk.StringVar(value=self.state.active_page.name)
        self._pages_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Pages", menu=self._pages_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="View", menu=view_menu)
        self._section_menu_vars: dict[str, tk.BooleanVar] = {}
        for name in self._SECTION_ORDER:
            v = tk.BooleanVar(value=True)
            self._section_menu_vars[name] = v
            view_menu.add_checkbutton(
                label=f"Show {name.title()}",
                variable=v,
                command=lambda n=name: self._on_section_toggle(n),
            )
        view_menu.add_separator()
        view_menu.add_checkbutton(
            label="Component outlines",
            variable=self.show_outlines,
            command=self._on_overlay_toggle,
        )
        view_menu.add_checkbutton(
            label="Component IDs",
            variable=self.show_ids,
            command=self._on_overlay_toggle,
        )
        view_menu.add_separator()
        view_menu.add_command(
            label="Clear selection",
            command=lambda: self._select_component(None),
        )

    def _on_section_toggle(self, name: str) -> None:
        self._set_section_visible(
            name, self._section_menu_vars[name].get())
        self._save_settings()

    def _sorted_components(self) -> list[Component]:
        """Components on the active page, sorted as displayed in the list."""
        return sorted(self.state.active_page.components,
                      key=lambda c: c.attrs.get("id", 0))

    def _refresh_inspector(self) -> None:
        """Refresh the state + components + scripts panels, but ONLY when
        content actually changed. We compare against a cached string so the
        user's selection / scroll / cursor isn't blown away every 33 ms when
        nothing meaningful has happened.
        """
        page = self.state.active_page
        # Drop a stale selection (e.g. after a page switch).
        if (self._selected_comp is not None
                and self._selected_comp not in page.components):
            self._selected_comp = None

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
        sorted_comps = self._sorted_components()
        rows = []
        for c in sorted_comps:
            a = c.attrs
            marker = "*" if c.events else " "
            line = f"{marker}{c.id:>3} {c.name:<10}"
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

        # Highlight the selected row. tag_remove + tag_add is cheap and
        # always correct (re-applies after any list rebuild). Tag ops work
        # on disabled Text widgets, so we don't need to toggle state.
        self._comp_text.tag_remove("sel", "1.0", tk.END)
        if self._selected_comp is not None:
            try:
                idx = sorted_comps.index(self._selected_comp)
                self._comp_text.tag_add(
                    "sel", f"{idx + 1}.0", f"{idx + 2}.0")
            except ValueError:
                pass

        # Scripts panel — Program.s + active page events + (if any) the
        # selected component's event handlers.
        self._refresh_scripts()

    def _refresh_scripts(self) -> None:
        """Re-render the SCRIPTS column. Diffed against cache so we don't
        clobber scroll/selection."""
        page = self.state.active_page
        sections: list[tuple[str, str, str]] = []  # (header, subheader, body)
        prog = (self.state.program_s or "").strip()
        if prog:
            sections.append(("Program.s", "", prog))
        for ev_name in ("codesload", "codesloadend", "codesunload"):
            body = (page.events.get(ev_name) or "").strip()
            if body:
                sections.append((f"{page.name}.{ev_name}", "", body))
        c = self._selected_comp
        if c is not None:
            type_label = self._component_type_label(c)
            header = f"{c.name} (#{c.id}, type {c.type}{type_label})"
            if not c.events:
                sections.append((header, "(no event scripts)", ""))
            else:
                for i, ev_name in enumerate(sorted(c.events)):
                    body = (c.events[ev_name] or "").strip()
                    sections.append((
                        header if i == 0 else "",
                        ev_name,
                        body,
                    ))

        # Build a flat string for cache comparison; if it changed, rebuild
        # the widget with tagged segments.
        flat = "\n\n".join(
            f"=== {h} ===\n--- {s} ---\n{b}" if s else f"=== {h} ===\n{b}"
            for h, s, b in sections
        )
        if flat == self._last_scripts_text:
            return
        self._last_scripts_text = flat
        widget = self._scripts_text
        yview = widget.yview()
        widget.config(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        if not sections:
            widget.insert(tk.END, "(no scripts on this page)\n", "muted")
        else:
            for h, s, b in sections:
                if h:
                    widget.insert(tk.END, f"=== {h} ===\n", "header")
                if s:
                    if b:
                        widget.insert(tk.END, f"--- {s} ---\n", "subheader")
                    else:
                        widget.insert(tk.END, f"{s}\n", "muted")
                if b:
                    widget.insert(tk.END, b + "\n\n")
                elif h or s:
                    widget.insert(tk.END, "\n")
        widget.config(state=tk.DISABLED)
        widget.yview_moveto(yview[0])

    @staticmethod
    def _component_type_label(c: Component) -> str:
        # Light hint for common component types; quiet on unknowns.
        names = {
            1: " slider", 51: " timer", 52: " variable",
            54: " text", 55: " button", 5: " text",
            53: " number", 121: " page",
        }
        return names.get(c.type, "")

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
        # Selection was per-page; drop it so SCRIPTS doesn't show stale info.
        self._selected_comp = None
        if (target.attrs["w"] != old.attrs["w"]
                or target.attrs["h"] != old.attrs["h"]):
            self.canvas.config(
                width=target.attrs["w"] * self.scale,
                height=target.attrs["h"] * self.scale,
            )
        # Mirror into the page-bar dropdown + menubar Pages radio so they
        # reflect the new active page even when the switch came from a
        # script / serial command rather than the UI.
        self._page_var.set(target.name)
        if self._page_menu_var is not None:
            self._page_menu_var.set(target.name)
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
        self._select_component(c)
        self.events.touch_press(page.id, c.id)
        self._log("tx", f"touch_press({page.name}.{c.name})")
        if c.type == T_SLIDER:
            self._drag_slider = c
            self._update_slider_val(c, ev.x, ev.y)
        self._run_component_event(c, "codesdown")

    def _on_motion(self, ev):
        if self._drag_slider is not None:
            self._update_slider_val(self._drag_slider, ev.x, ev.y)

    def _on_release(self, ev):
        slider = self._drag_slider
        self._drag_slider = None
        if slider is not None:
            self._update_slider_val(slider, ev.x, ev.y)
            self.events.touch_release(self.state.active_page.id, slider.id)
            self._log("tx", f"touch_release({self.state.active_page.name}.{slider.name})")
            self._run_component_event(slider, "codesup")
            return
        c = self._resolve_click(ev.x, ev.y)
        if c is None:
            return
        self._toggle_check_or_radio(c)
        self.events.touch_release(self.state.active_page.id, c.id)
        self._log("tx", f"touch_release({self.state.active_page.name}.{c.name})")
        self._run_component_event(c, "codesup")

    def _toggle_check_or_radio(self, c) -> None:
        if c.type == T_CHECKBOX:
            c.set("val", 0 if c.attrs.get("val", 0) else 1)
            self.state.dirty = True
            return
        if c.type == T_RADIO:
            # Page-wide mutual exclusion: clear every other radio and
            # set this one. Nextion has no explicit grouping attribute,
            # so all radios on a page form one group.
            for other in self.state.active_page.components:
                if other.type == T_RADIO and other is not c:
                    if other.attrs.get("val", 0):
                        other.set("val", 0)
            c.set("val", 1)
            self.state.dirty = True

    def _update_slider_val(self, c, x: int, y: int) -> None:
        """Map a canvas-coord touch to a slider's val and store it."""
        nx, ny = x // self.scale, y // self.scale
        a = c.attrs
        cx, cy = a.get("x", 0), a.get("y", 0)
        cw, ch = a.get("w", 0), a.get("h", 0)
        minval = a.get("minval", 0) or 0
        maxval = a.get("maxval", 100) or 100
        if maxval <= minval:
            maxval = minval + 100
        rng = maxval - minval
        if cw >= ch:  # horizontal: 0 at left, max at right
            denom = max(1, cw - 1)
            rel = max(0, min(cw - 1, nx - cx))
            new_val = minval + rel * rng // denom
        else:         # vertical: 0 at bottom, max at top (Nextion convention)
            denom = max(1, ch - 1)
            rel = max(0, min(ch - 1, ny - cy))
            new_val = maxval - rel * rng // denom
        if new_val != a.get("val"):
            c.set("val", new_val)
            self.state.dirty = True

    def _on_overlay_toggle(self) -> None:
        self.state.dirty = True
        self._save_settings()

    def _select_component(self, c: Component | None) -> None:
        """Select *c* (or clear with None) for the SCRIPTS panel + row
        highlight. The inspector refresh is gated on `state.dirty`, so we
        flip it to force a re-render."""
        if c is self._selected_comp:
            return
        self._selected_comp = c
        self.state.dirty = True

    def _on_comp_list_click(self, event) -> str | None:
        idx = self._comp_text.index(f"@{event.x},{event.y}")
        line_num = int(idx.split(".")[0]) - 1  # 0-based row index
        sorted_comps = self._sorted_components()
        if 0 <= line_num < len(sorted_comps):
            self._select_component(sorted_comps[line_num])
        else:
            # Clicked past the last row — deselect.
            self._select_component(None)
        return "break"  # don't move the disabled-Text caret

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
            self._toggle_check_or_radio(c)
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
            # Advance the animation clock and force a redraw if the
            # active page has any time-animated components (Scrolling
            # Text, etc.).
            self.state.time_ms += TICK_MS
            if any(c.type == 55 for c in self.state.active_page.components):
                self.state.dirty = True
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
        img = self.renderer.render(
            self.state,
            show_outlines=self.show_outlines.get(),
            show_ids=self.show_ids.get(),
        )
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
        # Tk's mainloop swallows SIGINT in C, so install a Python-side
        # signal handler that schedules a clean shutdown on the Tk
        # event loop. Without this, Ctrl+C only takes effect when Tk
        # next yields to Python — and surfaces as a noisy traceback.
        import signal
        def _on_sigint(signum, frame):
            try:
                self.root.after(0, self.root.destroy)
            except Exception:
                pass
        signal.signal(signal.SIGINT, _on_sigint)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            self.transport.close()
