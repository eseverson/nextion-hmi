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
        self.active_page = page
        self.dirty = True
