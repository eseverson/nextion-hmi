from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


class RGB565(int):
    """16-bit RGB565 color value as stored in Nextion attributes."""

    @property
    def r(self) -> int:
        return (int(self) >> 11) & 0x1F

    @property
    def g(self) -> int:
        return (int(self) >> 5) & 0x3F

    @property
    def b(self) -> int:
        return int(self) & 0x1F

    def to_rgb888(self) -> tuple[int, int, int]:
        return (self.r * 255 // 31, self.g * 255 // 63, self.b * 255 // 31)


@dataclass
class ComponentRef:
    name: str


@dataclass
class Component:
    name: str
    id: int
    type: int
    attrs: dict
    events: dict = field(default_factory=dict)
    dirty: bool = False

    def set(self, attr: str, value) -> None:
        if self.attrs.get(attr) == value:
            return
        self.attrs[attr] = value
        self.dirty = True


@dataclass
class Page:
    name: str
    id: int
    attrs: dict
    components: list[Component]
    overlay: object = None  # PIL.Image.Image or None — lazy per-page draw layer
    events: dict = field(default_factory=dict)  # codesload / codesloadend / codesunload

    def __post_init__(self):
        self._by_name = {c.name: c for c in self.components}
        self._by_id = {c.id: c for c in self.components}

    def by_name(self, name: str) -> Optional[Component]:
        return self._by_name.get(name)

    def by_id(self, cid: int) -> Optional[Component]:
        return self._by_id.get(cid)


@dataclass
class DisplayState:
    pages: dict[str, Page]
    pages_by_id: dict[int, Page] = field(init=False)
    active_page: Page = field(init=False)
    dim: int = 100
    dirty: bool = True
    globals: dict[str, Component] = field(init=False)
    # P1: 3 system int registers used by event scripts (sys0/sys1/sys2)
    sys: list[int] = field(default_factory=lambda: [0, 0, 0])
    # P1: program-script lines (Program.s), parsed lazily by app boot
    program_s: str = ""
    # ZI fonts pulled out of the HMI, keyed by font id (the integer prefix
    # of the .zi filename — e.g. `0.zi` => 0). Empty when fonts couldn't be
    # parsed; renderer falls back to a TTF substitute in that case.
    fonts: dict = field(default_factory=dict)

    def __post_init__(self):
        self.pages_by_id = {p.id: p for p in self.pages.values()}
        self.active_page = min(self.pages.values(), key=lambda p: p.id)
        self.globals = {}
        for p in self.pages.values():
            for c in p.components:
                if c.attrs.get("vscope") == 1:
                    self.globals[c.name] = c

    def resolve(self, ref: ComponentRef) -> Optional[Component]:
        c = self.active_page.by_name(ref.name)
        if c is not None:
            return c
        if ref.name in self.globals:
            return self.globals[ref.name]
        for p in self.pages.values():
            c = p.by_name(ref.name)
            if c is not None:
                return c
        return None

    def read_attr(self, name: str, attr: str):
        c = self.resolve(ComponentRef(name))
        if c is None:
            return None
        return c.attrs.get(attr)

    def set_active(self, page: Page) -> None:
        if self.active_page is page:
            return
        # Clear the previous page's draw overlay — Nextion does the same on
        # page switch.
        self.active_page.overlay = None
        self.active_page = page
        self.dirty = True


# ---------- ScriptContext (P1) ----------

# System variables addressable as bare names from event scripts.
# `dp` is read-only (current page id). `sys0..sys2` are mutable globals.
_SYS_NAMES = {"sys0": 0, "sys1": 1, "sys2": 2}


@dataclass
class ScriptContext:
    """Per-script-execution context.

    Locals (`int x=...`) live for the duration of one event-handler run.
    Reads fall through: locals → sys vars → component attrs (treated as a
    single `.val` shorthand for variable components).
    Writes resolve the same way: assigning to `x` looks for it as a local
    first, then a sys var, then a Variable component's `.val`, else creates
    a new local. This matches Nextion's loose name resolution.
    """
    state: "DisplayState"
    locals: dict = field(default_factory=dict)

    def read_name(self, name: str):
        if name in self.locals:
            return self.locals[name]
        if name in _SYS_NAMES:
            return self.state.sys[_SYS_NAMES[name]]
        if name == "dp":
            return self.state.active_page.id
        if name in ("dim", "dims"):
            return self.state.dim
        # Bare component name → take its .val (for Variable components and
        # short-form attribute reads in scripts).
        c = self.state.resolve(ComponentRef(name))
        if c is not None:
            return c.attrs.get("val")
        return None

    def read_attr(self, obj: str, attr: str):
        if obj in _SYS_NAMES and attr == "val":
            return self.state.sys[_SYS_NAMES[obj]]
        return self.state.read_attr(obj, attr)

    def write_name(self, name: str, value) -> None:
        if name in _SYS_NAMES:
            self.state.sys[_SYS_NAMES[name]] = int(value)
            return
        if name == "dp":
            return  # read-only
        if name in ("dim", "dims"):
            self.state.dim = max(0, min(100, int(value)))
            self.state.dirty = True
            return
        # Existing local → update; component name → write its .val; else new local
        if name in self.locals:
            self.locals[name] = value
            return
        c = self.state.resolve(ComponentRef(name))
        if c is not None:
            c.set("val", value)
            self.state.dirty = True
            return
        self.locals[name] = value

    def write_attr(self, obj: str, attr: str, value) -> None:
        if obj in _SYS_NAMES and attr == "val":
            self.state.sys[_SYS_NAMES[obj]] = int(value)
            return
        c = self.state.resolve(ComponentRef(obj))
        if c is None:
            return
        c.set(attr, value)
        self.state.dirty = True

    def declare_local(self, name: str, value) -> None:
        self.locals[name] = value
