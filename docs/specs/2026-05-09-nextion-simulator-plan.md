# Nextion Display Simulator (P0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Linux process that renders the Nextion dashboard and accepts the same `\xff\xff\xff`-framed serial commands the miata-dash firmware sends, plus emits canonical Nextion touch events when the user clicks visible widgets.

**Architecture:** Single Python process. Pure-data `DisplayState` model fed by a parser-then-executor pipeline. Pluggable `Transport` (TCP / PTY / stdin) is the only I/O. Renderer extracted from the existing `scripts/preview_page.py` reads `DisplayState` and paints into a Tk window each tick. P0 is non-scripted (no expression evaluator, no `if`/`while`); P1 will add that.

**Tech Stack:** Python 3.10+, Pillow (already used by `preview_page.py`), `tkinter` (stdlib), `socket` / `os.openpty` (stdlib), `pytest` for tests. No new third-party deps beyond what `setup.sh` already brings in for tooling.

---

## Layout

```
nextion/
├── sim/                                # new package
│   ├── __init__.py
│   ├── state.py                        # DisplayState, Page, Component, RGB565
│   ├── loader.py                       # HMI -> DisplayState (extracted from preview_page.py)
│   ├── parser.py                       # text frame -> Operation AST
│   ├── exec.py                         # apply Operation -> DisplayState mutation
│   ├── transport.py                    # TCP / PTY / stdin transport + EventEmitter
│   ├── renderer.py                     # DisplayState -> Pillow image (refactor of preview_page.py)
│   └── app.py                          # Tk window + tick loop
├── scripts/
│   └── nextion_sim.py                  # entry point (argparse → app.run)
└── tests/sim/
    ├── conftest.py                     # shared HMI fixture
    ├── test_state.py
    ├── test_loader.py
    ├── test_parser.py
    ├── test_exec.py
    ├── test_transport.py
    ├── test_renderer.py
    ├── test_replay_firmware.py
    └── fixtures/
        └── firmware_replay.png         # committed reference render
```

`scripts/preview_page.py` keeps working — Task 8 introduces `sim/renderer.py`
and rewires `preview_page.py` to call it (no behaviour change to the
existing previewer).

---

## Task 1: Bootstrap the `sim` package and pytest

**Files:**
- Create: `sim/__init__.py`
- Create: `tests/sim/__init__.py`
- Create: `tests/sim/conftest.py`
- Create: `pyproject.toml`
- Create: `tests/sim/test_smoke.py`

- [ ] **Step 1: Create empty package files**

```python
# sim/__init__.py
"""Nextion display simulator (P0)."""
```

```python
# tests/sim/__init__.py
```

- [ ] **Step 2: Add pyproject.toml so pytest finds the package**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "nextion-sim"
version = "0.0.1"
requires-python = ">=3.10"
dependencies = ["Pillow>=9"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 3: Add a shared HMI fixture**

```python
# tests/sim/conftest.py
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HMI_PATH = REPO_ROOT / "source" / "nextion.hmi.HMI"


@pytest.fixture(scope="session")
def hmi_path() -> Path:
    assert HMI_PATH.exists(), f"missing reference HMI at {HMI_PATH}"
    return HMI_PATH
```

- [ ] **Step 4: Write a failing smoke test**

```python
# tests/sim/test_smoke.py
import sim


def test_package_imports():
    assert sim is not None
```

- [ ] **Step 5: Run pytest, confirm pass**

```
pytest tests/sim/test_smoke.py -v
```

Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add sim/ tests/sim/__init__.py tests/sim/conftest.py tests/sim/test_smoke.py pyproject.toml
git commit -m "sim: bootstrap package and pytest"
```

---

## Task 2: `sim/state.py` — RGB565, Component, Page, DisplayState

**Files:**
- Create: `sim/state.py`
- Create: `tests/sim/test_state.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/sim/test_state.py
from sim.state import DisplayState, Page, Component, RGB565, ComponentRef


def test_rgb565_decodes_to_rgb888():
    # 0x2946 = (5,10,6) in RGB565 → ~ (41, 40, 49)
    c = RGB565(0x2946)
    assert c.to_rgb888() == (5 * 255 // 31, 10 * 255 // 63, 6 * 255 // 31)


def test_component_has_writeable_attrs():
    c = Component(name="x0", id=1, type=59, attrs={"val": 0, "bco": 10566})
    c.set("val", 42)
    assert c.attrs["val"] == 42
    assert c.dirty


def test_page_lookup_by_name_and_id():
    c0 = Component(name="x0", id=1, type=59, attrs={})
    c1 = Component(name="x1", id=2, type=59, attrs={})
    p = Page(name="main", id=0, attrs={"w": 480, "h": 320}, components=[c0, c1])
    assert p.by_name("x1") is c1
    assert p.by_id(1) is c0
    assert p.by_name("nope") is None


def test_display_state_active_page_starts_at_zero():
    main = Page(name="main", id=0, attrs={"w": 480, "h": 320}, components=[])
    settings = Page(name="settings", id=1, attrs={"w": 480, "h": 320}, components=[])
    state = DisplayState(pages={"main": main, "settings": settings})
    assert state.active_page is main


def test_display_state_resolves_global_variable():
    red = Component(name="red", id=25, type=52, attrs={"val": 64170, "vscope": 1})
    main = Page(name="main", id=0, attrs={"w": 480, "h": 320}, components=[red])
    state = DisplayState(pages={"main": main})
    assert state.resolve(ComponentRef("red")).attrs["val"] == 64170


def test_display_state_resolves_dotted_attribute():
    red = Component(name="red", id=25, type=52, attrs={"val": 64170, "vscope": 1})
    main = Page(name="main", id=0, attrs={"w": 480, "h": 320}, components=[red])
    state = DisplayState(pages={"main": main})
    val = state.read_attr("red", "val")
    assert val == 64170
```

- [ ] **Step 2: Run, see them fail**

```
pytest tests/sim/test_state.py -v
```

Expected: ImportError (module not found).

- [ ] **Step 3: Implement `sim/state.py`**

```python
# sim/state.py
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
    events: dict = field(default_factory=dict)  # event-script source by handler name
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
        # Active page = lowest id (matches Nextion's default startup behaviour)
        self.active_page = min(self.pages.values(), key=lambda p: p.id)
        self.globals = {}
        for p in self.pages.values():
            for c in p.components:
                if c.attrs.get("vscope") == 1:
                    self.globals[c.name] = c

    def resolve(self, ref: ComponentRef) -> Optional[Component]:
        # Active page first, then globals, then any page (to support cross-page refs)
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
```

- [ ] **Step 4: Run, see all tests pass**

```
pytest tests/sim/test_state.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add sim/state.py tests/sim/test_state.py
git commit -m "sim: state model (RGB565, Component, Page, DisplayState)"
```

---

## Task 3: `sim/loader.py` — HMI → DisplayState

**Files:**
- Create: `sim/loader.py`
- Create: `tests/sim/test_loader.py`

This task extracts the HMI-loading logic from `scripts/preview_page.py` into a reusable function. The previewer keeps working (Task 8 will route it through this).

- [ ] **Step 1: Write the failing test**

```python
# tests/sim/test_loader.py
from sim.loader import load_hmi


def test_loader_returns_display_state(hmi_path):
    state = load_hmi(hmi_path)
    assert "main" in state.pages
    assert "settings" in state.pages
    assert state.active_page.name in state.pages

    main = state.pages["main"]
    assert main.attrs["w"] == 480
    assert main.attrs["h"] == 320
    assert main.by_name("x0") is not None
    assert main.by_name("t1").attrs["txt"] == "RPM"
    assert main.by_name("j0").attrs["val"] == 50  # progress bar default


def test_loader_indexes_global_color_vars(hmi_path):
    state = load_hmi(hmi_path)
    # red, blu, bco etc. are vscope=local in this HMI; they only exist
    # under the main page. Validate the per-page lookup works.
    main = state.pages["main"]
    assert main.by_name("red").attrs["val"] == 64170
```

- [ ] **Step 2: Run, see them fail**

```
pytest tests/sim/test_loader.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `sim/loader.py`**

```python
# sim/loader.py
from __future__ import annotations
from pathlib import Path
import sys

from sim.state import DisplayState, Page, Component


def _ensure_nextion2text_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    n2t = repo_root / "tools" / "Nextion2Text"
    if not n2t.exists():
        raise FileNotFoundError(
            f"Nextion2Text not found at {n2t}. Run scripts/setup.sh first."
        )
    if str(n2t) not in sys.path:
        sys.path.insert(0, str(n2t))


def load_hmi(path: str | Path) -> DisplayState:
    """Load a Nextion HMI file and return a populated DisplayState."""
    _ensure_nextion2text_on_path()
    from Nextion2Text import HMI

    hmi = HMI(str(path))
    pages: dict[str, Page] = {}
    for n2t_page in hmi.pages:
        page_comp = next(
            (c for c in n2t_page.components if c.rawData["att"].get("type") == 121),
            None,
        )
        if page_comp is None:
            continue
        pa = page_comp.rawData["att"]
        page_name = pa.get("objname") or f"page{len(pages)}"
        components: list[Component] = []
        for c in n2t_page.components:
            ca = c.rawData["att"]
            ctype = ca.get("type")
            if ctype == 121:
                # Page meta — captured at the Page level, not a Component
                continue
            # Pull event-script blobs (codesdown/codesup/codesload/...) — they
            # live alongside "att" in the Nextion2Text rawData dict.
            events = {
                k: v for k, v in c.rawData.items()
                if k.startswith("codes") and isinstance(v, str) and v.strip()
            }
            components.append(
                Component(
                    name=ca.get("objname") or f"c{ca.get('id')}",
                    id=ca.get("id") or 0,
                    type=ctype,
                    attrs=dict(ca),
                    events=events,
                )
            )
        pages[page_name] = Page(
            name=page_name,
            id=pa.get("id") or 0,
            attrs=dict(pa),
            components=components,
        )
    if not pages:
        raise ValueError(f"no pages parsed from {path}")
    return DisplayState(pages=pages)
```

- [ ] **Step 4: Run, see tests pass**

```
pytest tests/sim/test_loader.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add sim/loader.py tests/sim/test_loader.py
git commit -m "sim: HMI loader builds DisplayState from Nextion2Text"
```

---

## Task 4: `sim/parser.py` — text frame → Operation

**Files:**
- Create: `sim/parser.py`
- Create: `tests/sim/test_parser.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/sim/test_parser.py
import pytest
from sim.parser import (
    parse,
    Mutation,
    PageSwitch,
    GlobalSet,
    Refresh,
    ClearScreen,
    Print,
    PrintH,
    Unsupported,
    IntLiteral,
    StrLiteral,
    AttrRef,
)


def test_int_attribute_set():
    op = parse(b"x0.val=12345")
    assert op == Mutation("x0", "val", IntLiteral(12345))


def test_negative_int():
    op = parse(b"x0.val=-7")
    assert op == Mutation("x0", "val", IntLiteral(-7))


def test_string_attribute_set():
    op = parse(b's0.txt="MAP Error"')
    assert op == Mutation("s0", "txt", StrLiteral("MAP Error"))


def test_string_with_escaped_quote():
    op = parse(b's0.txt="he said \\"hi\\""')
    assert op == Mutation("s0", "txt", StrLiteral('he said "hi"'))


def test_attribute_reference_value():
    op = parse(b"s0.bco=red.val")
    assert op == Mutation("s0", "bco", AttrRef("red", "val"))


def test_page_switch_by_id():
    assert parse(b"page 1") == PageSwitch(1)


def test_page_switch_by_name():
    assert parse(b"page settings") == PageSwitch("settings")


def test_dim_global():
    assert parse(b"dim=80") == GlobalSet("dim", 80)


def test_baud_global_acknowledged():
    assert parse(b"baud=115200") == GlobalSet("baud", 115200)


def test_refresh():
    assert parse(b"ref t0") == Refresh("t0")


def test_cls_with_color():
    assert parse(b"cls 0") == ClearScreen(0)


def test_print_string():
    assert parse(b'print "hi"') == Print("hi")


def test_printh_bytes():
    assert parse(b"printh 00 ff 7f") == PrintH(b"\x00\xff\x7f")


def test_unrecognised_returns_unsupported():
    op = parse(b"sys0=x7.val-x4.val")
    assert isinstance(op, Unsupported)
    assert "expression" in op.reason or "parse" in op.reason


def test_empty_frame_is_unsupported():
    op = parse(b"")
    assert isinstance(op, Unsupported)
```

- [ ] **Step 2: Run, see them fail**

```
pytest tests/sim/test_parser.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `sim/parser.py`**

```python
# sim/parser.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Union
import re


@dataclass(frozen=True)
class IntLiteral:
    value: int


@dataclass(frozen=True)
class StrLiteral:
    value: str


@dataclass(frozen=True)
class AttrRef:
    obj: str
    attr: str


Value = Union[IntLiteral, StrLiteral, AttrRef]


@dataclass(frozen=True)
class Mutation:
    target: str
    attr: str
    value: Value


@dataclass(frozen=True)
class PageSwitch:
    target: int | str


@dataclass(frozen=True)
class GlobalSet:
    name: str
    value: int


@dataclass(frozen=True)
class Refresh:
    target: str


@dataclass(frozen=True)
class ClearScreen:
    color: int


@dataclass(frozen=True)
class Print:
    text: str


@dataclass(frozen=True)
class PrintH:
    payload: bytes


@dataclass(frozen=True)
class Unsupported:
    text: str
    reason: str


Operation = Union[
    Mutation, PageSwitch, GlobalSet, Refresh,
    ClearScreen, Print, PrintH, Unsupported,
]


_GLOBAL_NAMES = {"dim", "dims", "baud", "recmod", "thup", "usup"}
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INT_RE = re.compile(r"-?\d+")


def _parse_value(rhs: str) -> Value | None:
    rhs = rhs.strip()
    if not rhs:
        return None
    # String literal
    if rhs.startswith('"'):
        # Walk characters honouring \" escapes
        out = []
        i = 1
        while i < len(rhs):
            ch = rhs[i]
            if ch == "\\" and i + 1 < len(rhs):
                out.append(rhs[i + 1])
                i += 2
                continue
            if ch == '"':
                # End of literal; reject anything after
                if i != len(rhs) - 1:
                    return None
                return StrLiteral("".join(out))
            out.append(ch)
            i += 1
        return None
    # Integer literal
    if _INT_RE.fullmatch(rhs):
        return IntLiteral(int(rhs))
    # Attribute reference: ident.ident
    if "." in rhs:
        obj, _, attr = rhs.partition(".")
        if _IDENT_RE.fullmatch(obj) and _IDENT_RE.fullmatch(attr):
            return AttrRef(obj, attr)
    # Bare identifier (rare; treat as 0-arg, unsupported for now)
    return None


def parse(frame: bytes) -> Operation:
    """Parse one Nextion command frame (bytes between \\xff markers)."""
    text = frame.decode("latin-1").strip()
    if not text:
        return Unsupported(text, "empty frame")

    # `print "..."`
    if text.startswith("print ") and not text.startswith("printh"):
        rhs = text[len("print "):].strip()
        v = _parse_value(rhs)
        if isinstance(v, StrLiteral):
            return Print(v.value)
        if isinstance(v, IntLiteral):
            return Print(str(v.value))
        return Unsupported(text, "print: expected string literal")

    # `printh AA BB CC ...`
    if text.startswith("printh "):
        parts = text[len("printh "):].split()
        try:
            return PrintH(bytes(int(p, 16) for p in parts))
        except ValueError:
            return Unsupported(text, "printh: bad hex")

    # `page <id|name>`
    if text.startswith("page "):
        target = text[len("page "):].strip()
        if _INT_RE.fullmatch(target):
            return PageSwitch(int(target))
        if _IDENT_RE.fullmatch(target):
            return PageSwitch(target)
        return Unsupported(text, "page: bad target")

    # `ref <obj>`
    if text.startswith("ref "):
        target = text[len("ref "):].strip()
        if _IDENT_RE.fullmatch(target):
            return Refresh(target)
        return Unsupported(text, "ref: bad target")

    # `cls <color>`
    if text.startswith("cls "):
        rhs = text[len("cls "):].strip()
        if _INT_RE.fullmatch(rhs):
            return ClearScreen(int(rhs))
        return Unsupported(text, "cls: expected int")

    # Assignment: lhs=rhs
    if "=" in text:
        lhs, _, rhs = text.partition("=")
        lhs = lhs.strip()
        rhs = rhs.strip()
        # Reject expressions containing operators outside string literals
        if not rhs.startswith('"'):
            for op in ("+", "-", "*", "/", "<", ">", "&&", "||"):
                # Allow leading minus on a numeric
                if op == "-" and _INT_RE.fullmatch(rhs):
                    continue
                if op in rhs:
                    return Unsupported(text, "expression unsupported in P0")
        v = _parse_value(rhs)
        if v is None:
            return Unsupported(text, "parse: bad value")
        if "." in lhs:
            obj, _, attr = lhs.partition(".")
            if _IDENT_RE.fullmatch(obj) and _IDENT_RE.fullmatch(attr):
                return Mutation(obj, attr, v)
            return Unsupported(text, "parse: bad target")
        if lhs in _GLOBAL_NAMES:
            if isinstance(v, IntLiteral):
                return GlobalSet(lhs, v.value)
            return Unsupported(text, "global: expected int")
        return Unsupported(text, "parse: bare identifier lhs")

    return Unsupported(text, "parse: unrecognised form")
```

- [ ] **Step 4: Run, see all tests pass**

```
pytest tests/sim/test_parser.py -v
```

Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add sim/parser.py tests/sim/test_parser.py
git commit -m "sim: command parser for runtime instruction subset"
```

---

## Task 5: `sim/exec.py` — apply Operation to DisplayState

**Files:**
- Create: `sim/exec.py`
- Create: `tests/sim/test_exec.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/sim/test_exec.py
from sim.loader import load_hmi
from sim.parser import parse
from sim.exec import execute


def test_set_xfloat_val(hmi_path):
    state = load_hmi(hmi_path)
    main = state.pages["main"]
    assert main.by_name("x0").attrs["val"] == 123456
    execute(state, parse(b"x0.val=42"))
    assert main.by_name("x0").attrs["val"] == 42
    assert state.dirty


def test_set_text_string(hmi_path):
    state = load_hmi(hmi_path)
    state.dirty = False
    execute(state, parse(b's0.txt="MAP Error"'))
    assert state.pages["main"].by_name("s0").attrs["txt"] == "MAP Error"
    assert state.dirty


def test_set_attr_via_reference(hmi_path):
    state = load_hmi(hmi_path)
    red_val = state.pages["main"].by_name("red").attrs["val"]
    execute(state, parse(b"s0.bco=red.val"))
    assert state.pages["main"].by_name("s0").attrs["bco"] == red_val


def test_page_switch_by_name(hmi_path):
    state = load_hmi(hmi_path)
    execute(state, parse(b"page settings"))
    assert state.active_page.name == "settings"


def test_page_switch_by_id(hmi_path):
    state = load_hmi(hmi_path)
    target_id = state.pages["settings"].id
    execute(state, parse(f"page {target_id}".encode()))
    assert state.active_page.name == "settings"


def test_dim_writes_state(hmi_path):
    state = load_hmi(hmi_path)
    execute(state, parse(b"dim=50"))
    assert state.dim == 50
    assert state.dirty


def test_unknown_component_logs_no_crash(hmi_path, caplog):
    state = load_hmi(hmi_path)
    execute(state, parse(b"qqq.val=1"))
    # No crash; warning logged
    assert any("qqq" in r.message for r in caplog.records)


def test_unsupported_op_logs(hmi_path, caplog):
    state = load_hmi(hmi_path)
    execute(state, parse(b"sys0=x7.val-x4.val"))
    assert any("expression" in r.message or "Unsupported" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run, see them fail**

```
pytest tests/sim/test_exec.py -v
```

- [ ] **Step 3: Implement `sim/exec.py`**

```python
# sim/exec.py
from __future__ import annotations
import logging
from typing import Optional

from sim.state import DisplayState, ComponentRef
from sim.parser import (
    Operation, Mutation, PageSwitch, GlobalSet, Refresh, ClearScreen,
    Print, PrintH, Unsupported, IntLiteral, StrLiteral, AttrRef,
)

log = logging.getLogger("sim.exec")


def _resolve_value(state: DisplayState, value):
    if isinstance(value, IntLiteral):
        return value.value
    if isinstance(value, StrLiteral):
        return value.value
    if isinstance(value, AttrRef):
        v = state.read_attr(value.obj, value.attr)
        if v is None:
            log.warning("unresolved reference %s.%s", value.obj, value.attr)
        return v
    return None


def execute(state: DisplayState, op: Operation) -> None:
    if isinstance(op, Mutation):
        comp = state.resolve(ComponentRef(op.target))
        if comp is None:
            log.warning("unknown component '%s'", op.target)
            return
        v = _resolve_value(state, op.value)
        if v is None:
            return
        comp.set(op.attr, v)
        state.dirty = True
        return

    if isinstance(op, PageSwitch):
        if isinstance(op.target, int):
            page = state.pages_by_id.get(op.target)
        else:
            page = state.pages.get(op.target)
        if page is None:
            log.warning("unknown page '%s'", op.target)
            return
        state.set_active(page)
        return

    if isinstance(op, GlobalSet):
        if op.name in ("dim", "dims"):
            state.dim = max(0, min(100, op.value))
            state.dirty = True
        # baud / recmod / thup / usup: acknowledged, no-op
        return

    if isinstance(op, Refresh):
        # We always render live; nothing to do.
        return

    if isinstance(op, ClearScreen):
        state.active_page.attrs["bco"] = op.color
        for c in state.active_page.components:
            c.dirty = True
        state.dirty = True
        return

    if isinstance(op, Print):
        log.info("print: %s", op.text)
        return

    if isinstance(op, PrintH):
        log.info("printh: %s", op.payload.hex())
        return

    if isinstance(op, Unsupported):
        log.warning("Unsupported op: %r (%s)", op.text, op.reason)
        return

    log.warning("unhandled op type: %r", op)
```

- [ ] **Step 4: Run, see tests pass**

```
pytest tests/sim/test_exec.py -v
```

Expected: 8 passed. (`caplog` requires `--log-cli-level=WARNING` is not needed; pytest captures by default.)

- [ ] **Step 5: Commit**

```bash
git add sim/exec.py tests/sim/test_exec.py
git commit -m "sim: executor applies parsed Operations to DisplayState"
```

---

## Task 6: `sim/transport.py` — TCP / PTY / stdin + EventEmitter

**Files:**
- Create: `sim/transport.py`
- Create: `tests/sim/test_transport.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/sim/test_transport.py
import socket
import threading
import time

import pytest

from sim.transport import (
    Transport,
    TcpTransport,
    StdinTransport,
    PtyTransport,
    EventEmitter,
)


def test_framer_strips_trailing_marker():
    t = Transport()
    t._buf.extend(b"x0.val=1\xff\xff\xffpage 1\xff\xff\xff")
    assert t._next_frame_from_buffer() == b"x0.val=1"
    assert t._next_frame_from_buffer() == b"page 1"
    assert t._next_frame_from_buffer() is None


def test_framer_holds_partial_frame():
    t = Transport()
    t._buf.extend(b"x0.val=1\xff\xff")  # only two of three terminators
    assert t._next_frame_from_buffer() is None


def test_send_frame_appends_terminators(monkeypatch):
    sent = []
    t = Transport()
    t._write_raw = lambda b: sent.append(b)
    t.send_frame(b"\x65\x00\x15\x01")
    assert sent == [b"\x65\x00\x15\x01\xff\xff\xff"]


def test_tcp_transport_round_trip():
    t = TcpTransport(host="127.0.0.1", port=0)  # 0 = ephemeral
    t.start()
    try:
        port = t.port
        # Client connects
        client = socket.create_connection(("127.0.0.1", port))
        client.sendall(b"x0.val=99\xff\xff\xff")
        # Server (transport) reads
        deadline = time.monotonic() + 1.0
        frame = None
        while time.monotonic() < deadline:
            frame = t.recv_frame()
            if frame is not None:
                break
            time.sleep(0.01)
        assert frame == b"x0.val=99"
        # Server sends event back
        t.send_frame(b"\x65\x00\x15\x01")
        client.settimeout(1.0)
        data = client.recv(64)
        assert data == b"\x65\x00\x15\x01\xff\xff\xff"
        client.close()
    finally:
        t.close()


def test_event_emitter_touch_press():
    sent = []

    class _Stub:
        def send_frame(self, payload):
            sent.append(payload)

    emitter = EventEmitter(_Stub())
    # page id 0, comp id 21 (= 0x15)
    emitter.touch_press(page_id=0, comp_id=21)
    emitter.touch_release(page_id=0, comp_id=21)
    assert sent == [b"\x65\x00\x15\x01", b"\x65\x00\x15\x00"]
```

- [ ] **Step 2: Run, see them fail**

- [ ] **Step 3: Implement `sim/transport.py`**

```python
# sim/transport.py
from __future__ import annotations
import os
import select
import socket
import sys
import threading
from typing import Optional


_TERMINATOR = b"\xff\xff\xff"


class Transport:
    """Base framer; subclasses provide bytes I/O."""

    def __init__(self):
        self._buf = bytearray()
        self._lock = threading.Lock()

    # ---- Framing ----
    def _next_frame_from_buffer(self) -> Optional[bytes]:
        idx = self._buf.find(_TERMINATOR)
        if idx == -1:
            return None
        frame = bytes(self._buf[:idx])
        del self._buf[: idx + len(_TERMINATOR)]
        return frame

    def recv_frame(self) -> Optional[bytes]:
        with self._lock:
            self._pump_into_buffer()
            return self._next_frame_from_buffer()

    def _pump_into_buffer(self) -> None:
        """Subclass hook: read any available bytes into self._buf without blocking."""
        pass

    def _write_raw(self, payload: bytes) -> None:
        raise NotImplementedError

    def send_frame(self, payload: bytes) -> None:
        self._write_raw(payload + _TERMINATOR)

    def close(self) -> None:
        pass


class TcpTransport(Transport):
    def __init__(self, host: str = "127.0.0.1", port: int = 9999):
        super().__init__()
        self._host = host
        self._port_requested = port
        self._server: Optional[socket.socket] = None
        self._client: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.port = port

    def start(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self._host, self._port_requested))
        s.listen(1)
        s.settimeout(0.1)
        self._server = s
        self.port = s.getsockname()[1]
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            conn.setblocking(False)
            with self._lock:
                if self._client is not None:
                    self._client.close()
                self._client = conn

    def _pump_into_buffer(self) -> None:
        c = self._client
        if c is None:
            return
        try:
            while True:
                chunk = c.recv(4096)
                if not chunk:
                    self._client = None
                    return
                self._buf.extend(chunk)
        except BlockingIOError:
            return
        except (ConnectionResetError, OSError):
            self._client = None

    def _write_raw(self, payload: bytes) -> None:
        with self._lock:
            c = self._client
            if c is None:
                return
            try:
                c.sendall(payload)
            except OSError:
                self._client = None

    def close(self) -> None:
        self._stop.set()
        if self._client is not None:
            try:
                self._client.close()
            except OSError:
                pass
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass


class StdinTransport(Transport):
    def __init__(self):
        super().__init__()
        self._fd = sys.stdin.fileno()

    def _pump_into_buffer(self) -> None:
        r, _, _ = select.select([self._fd], [], [], 0)
        if r:
            chunk = os.read(self._fd, 4096)
            if chunk:
                self._buf.extend(chunk)

    def _write_raw(self, payload: bytes) -> None:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()


class PtyTransport(Transport):
    def __init__(self):
        super().__init__()
        self._master, self._slave = os.openpty()
        self.path = os.ttyname(self._slave)

    def _pump_into_buffer(self) -> None:
        r, _, _ = select.select([self._master], [], [], 0)
        if r:
            try:
                chunk = os.read(self._master, 4096)
                if chunk:
                    self._buf.extend(chunk)
            except OSError:
                pass

    def _write_raw(self, payload: bytes) -> None:
        try:
            os.write(self._master, payload)
        except OSError:
            pass

    def close(self) -> None:
        for fd in (self._master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass


class EventEmitter:
    """Constructs Nextion event byte sequences and sends them via a Transport."""

    def __init__(self, transport):
        self._t = transport

    def touch_press(self, page_id: int, comp_id: int) -> None:
        self._t.send_frame(bytes([0x65, page_id & 0xFF, comp_id & 0xFF, 0x01]))

    def touch_release(self, page_id: int, comp_id: int) -> None:
        self._t.send_frame(bytes([0x65, page_id & 0xFF, comp_id & 0xFF, 0x00]))
```

- [ ] **Step 4: Run, see tests pass**

```
pytest tests/sim/test_transport.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add sim/transport.py tests/sim/test_transport.py
git commit -m "sim: transports (tcp/stdin/pty) + EventEmitter"
```

---

## Task 7: `sim/renderer.py` — DisplayState → Image

**Files:**
- Create: `sim/renderer.py`
- Modify: `scripts/preview_page.py` (route through new renderer)
- Create: `tests/sim/test_renderer.py`

This refactors the rendering logic out of `scripts/preview_page.py` so the
sim can reuse it. The previewer becomes a thin wrapper.

- [ ] **Step 1: Write the failing test**

```python
# tests/sim/test_renderer.py
from sim.loader import load_hmi
from sim.renderer import Renderer


def test_renderer_produces_image_at_page_size(hmi_path):
    state = load_hmi(hmi_path)
    img = Renderer().render(state)
    main = state.pages["main"]
    assert img.size == (main.attrs["w"], main.attrs["h"])


def test_renderer_respects_dim(hmi_path):
    state = load_hmi(hmi_path)
    img_full = Renderer().render(state)
    state.dim = 20
    img_dim = Renderer().render(state)
    # At 20% dim, average pixel intensity should be lower
    avg_full = sum(sum(p) for p in img_full.getdata()) / (img_full.size[0] * img_full.size[1] * 3)
    avg_dim = sum(sum(p) for p in img_dim.getdata()) / (img_dim.size[0] * img_dim.size[1] * 3)
    assert avg_dim < avg_full * 0.6
```

- [ ] **Step 2: Run, see them fail**

- [ ] **Step 3: Implement `sim/renderer.py` by extracting from `preview_page.py`**

Copy the rendering helpers (`rgb565_to_rgb888`, `find_font_file`, `load_font`, `font_size_for`, `align_text`, `format_xfloat`, `render_component`) from `scripts/preview_page.py` into `sim/renderer.py`. Then add the class:

```python
# At the bottom of sim/renderer.py
from PIL import Image, ImageDraw

class Renderer:
    def render(self, state) -> Image.Image:
        page = state.active_page
        w = page.attrs.get("w", 480)
        h = page.attrs.get("h", 320)
        sta = page.attrs.get("sta", 1)
        if sta == 1:
            bg = rgb565_to_rgb888(page.attrs.get("bco")) or (0, 0, 0)
        else:
            bg = (255, 255, 255)
        img = Image.new("RGB", (w, h), bg)
        draw = ImageDraw.Draw(img)
        # Render in id order (matches Nextion paint order)
        for c in sorted(page.components, key=lambda c: c.attrs.get("id", 0)):
            # Adapt: render_component expects a Nextion2Text-style component
            # with c.rawData["att"]. Build a tiny shim.
            shim = type("Shim", (), {"rawData": {"att": c.attrs}})()
            render_component(draw, shim, bg)
        # Apply dim
        dim = max(0, min(100, getattr(state, "dim", 100)))
        if dim < 100:
            factor = max(0.05, dim / 100.0)
            img = Image.eval(img, lambda v: int(v * factor))
        state.dirty = False
        return img
```

- [ ] **Step 4: Rewire `scripts/preview_page.py`**

Replace its `render_page` function body with:

```python
from sim.loader import load_hmi
from sim.renderer import Renderer


def render_page(page, scale: int = 1):
    # Kept for backwards compat with existing CLI; route through Renderer.
    raise NotImplementedError("preview_page.py now uses sim.renderer; call main()")
```

And update `main()` to:

```python
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hmi", default=str(REPO_ROOT / "source" / "nextion.hmi.HMI"))
    ap.add_argument("--out", default=str(REPO_ROOT / "work"))
    ap.add_argument("--scale", type=int, default=1)
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    state = load_hmi(args.hmi)
    renderer = Renderer()
    rendered = 0
    for name, page in state.pages.items():
        state.active_page = page
        state.dirty = True
        img = renderer.render(state)
        if args.scale != 1:
            img = img.resize(
                (img.size[0] * args.scale, img.size[1] * args.scale),
                Image.NEAREST,
            )
        out = out_dir / f"preview_{name}.png"
        img.save(out)
        print(f"  rendered {name}: {img.size[0]}x{img.size[1]} -> {out}")
        rendered += 1
    print(f"done. {rendered} page(s) rendered to {out_dir}")
    return 0
```

- [ ] **Step 5: Run renderer tests + previewer**

```
pytest tests/sim/test_renderer.py -v
python3 scripts/preview_page.py
```

Expected: tests pass, previewer prints same output as before, PNGs in `work/` look the same as before.

- [ ] **Step 6: Commit**

```bash
git add sim/renderer.py tests/sim/test_renderer.py scripts/preview_page.py
git commit -m "sim: renderer extracted from preview_page; preview routes through it"
```

---

## Task 8: `sim/app.py` — Tk window + tick loop

**Files:**
- Create: `sim/app.py`

This task is integration glue with limited automated test coverage; the
firmware-replay test in Task 10 exercises it end-to-end.

- [ ] **Step 1: Implement `sim/app.py`**

```python
# sim/app.py
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
        # Highest-id component containing the point wins (Nextion paint order).
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
        # P0 navigation hack: if the Touch Press handler (codesdown) is
        # exactly `page <n>`, honour it locally so navigation works without
        # the full script executor that lands in P1.
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
```

- [ ] **Step 2: Smoke-import the module**

```
python3 -c "import sim.app; print('ok')"
```

Expected: `ok`. (No tests yet; Task 10's replay test exercises the loop.)

- [ ] **Step 3: Commit**

```bash
git add sim/app.py
git commit -m "sim: Tk app loop wires transport, parser, executor, renderer"
```

---

## Task 9: `scripts/nextion_sim.py` — entry point

**Files:**
- Create: `scripts/nextion_sim.py`

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""nextion_sim — live Linux simulator for the dashboard.

Loads the HMI and starts a window that responds to Nextion-style serial
commands over TCP / PTY / stdin. Use --bind to choose the transport.
"""
from __future__ import annotations
import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sim.app import App
from sim.loader import load_hmi
from sim.transport import TcpTransport, PtyTransport, StdinTransport


def _build_transport(spec: str):
    if spec == "stdin":
        return StdinTransport()
    if spec == "pty":
        t = PtyTransport()
        print(f"PTY available at: {t.path}", flush=True)
        return t
    if spec.startswith("tcp:"):
        host, _, port = spec[4:].rpartition(":")
        host = host or "127.0.0.1"
        t = TcpTransport(host=host, port=int(port))
        t.start()
        print(f"Listening on tcp://{host}:{t.port}", flush=True)
        return t
    raise SystemExit(f"unknown --bind: {spec}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hmi", default=str(REPO_ROOT / "source" / "nextion.hmi.HMI"))
    ap.add_argument("--bind", default="tcp:127.0.0.1:9999")
    ap.add_argument("--scale", type=int, default=1)
    ap.add_argument("--start-page", default=None)
    ap.add_argument("--log-commands", action="store_true")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    state = load_hmi(args.hmi)
    if args.start_page and args.start_page in state.pages:
        state.active_page = state.pages[args.start_page]
    transport = _build_transport(args.bind)
    App(state, transport, scale=args.scale, log_commands=args.log_commands).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke-run with stdin transport (immediate exit on EOF)**

```
echo "" | python3 scripts/nextion_sim.py --bind stdin --log-level WARNING
```

Expected: Tk window briefly opens, processes empty input, exits when
window is closed. (Requires `python3-tk`. If unavailable, document the
need and skip.)

- [ ] **Step 3: Commit**

```bash
git add scripts/nextion_sim.py
git commit -m "sim: entry point script"
```

---

## Task 10: Firmware replay integration test

**Files:**
- Create: `tests/sim/test_replay_firmware.py`
- Create: `tests/sim/fixtures/firmware_replay.png` (committed reference)

- [ ] **Step 1: Write the test**

```python
# tests/sim/test_replay_firmware.py
from pathlib import Path

import pytest

from sim.loader import load_hmi
from sim.parser import parse
from sim.exec import execute
from sim.renderer import Renderer


# Exact bytes the firmware sends during one update cycle, taken from
# src/main.cpp (the parent miata-dash repo).
FIRMWARE_FRAMES = [
    b"j0.val=42",
    b"x0.val=98",
    b"x1.val=4500",
    b"x2.val=180",
    b"x3.val=33",
    b"x4.val=145",
    b"x5.val=120",
    b"x6.val=132",
    b"x7.val=145",
    b"x8.val=0",
    b"s0.txt=\"\"",
]


def test_firmware_replay_does_not_crash(hmi_path):
    state = load_hmi(hmi_path)
    for frame in FIRMWARE_FRAMES:
        execute(state, parse(frame))
    main = state.pages["main"]
    assert main.by_name("x0").attrs["val"] == 98
    assert main.by_name("x1").attrs["val"] == 4500
    assert main.by_name("j0").attrs["val"] == 42
    assert main.by_name("s0").attrs["txt"] == ""


def test_firmware_replay_renders_to_committed_reference(hmi_path, tmp_path):
    state = load_hmi(hmi_path)
    for frame in FIRMWARE_FRAMES:
        execute(state, parse(frame))
    img = Renderer().render(state)

    fixtures = Path(__file__).parent / "fixtures"
    reference = fixtures / "firmware_replay.png"
    if not reference.exists():
        # First run: write the reference and skip. Commit the resulting PNG.
        fixtures.mkdir(parents=True, exist_ok=True)
        img.save(reference)
        pytest.skip(f"reference written to {reference}; commit and rerun")

    from PIL import Image
    ref_img = Image.open(reference)
    assert img.size == ref_img.size
    # Compare pixel-perfect — same Pillow version + same fonts assumed.
    assert list(img.getdata()) == list(ref_img.getdata())
```

- [ ] **Step 2: First run generates the reference**

```
pytest tests/sim/test_replay_firmware.py::test_firmware_replay_renders_to_committed_reference -v
```

Expected: skipped with "reference written to ...".

- [ ] **Step 3: Visually verify the reference**

Open `tests/sim/fixtures/firmware_replay.png`. It should show the main
dashboard with `98 / 4500 / 180` in the top row, etc., and an empty
warning bar at the bottom.

- [ ] **Step 4: Run the suite**

```
pytest tests/sim/ -v
```

Expected: all tests pass. (The replay test is now active because the
fixture exists.)

- [ ] **Step 5: Commit**

```bash
git add tests/sim/test_replay_firmware.py tests/sim/fixtures/firmware_replay.png
git commit -m "sim: firmware replay test + reference render"
```

---

## Task 11: README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a "Simulator" section**

```markdown
## Simulator (P0)

A live Linux process that renders the dashboard and accepts the same
serial commands the firmware sends.

```bash
python3 scripts/nextion_sim.py
# starts a Tk window, listens on tcp://127.0.0.1:9999

# In another terminal:
printf 'x0.val=12345\xff\xff\xff' | nc -N 127.0.0.1 9999
printf 's0.txt="MAP Error"\xff\xff\xff' | nc -N 127.0.0.1 9999
printf 'page settings\xff\xff\xff' | nc -N 127.0.0.1 9999
```

`--bind pty` creates a /dev/pts/N path you can point real
serial-using code at. `--bind stdin` is for scripted tests.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: simulator usage in README"
```

---

## Self-review (run after writing all tasks)

- **Spec coverage**: Every section of the design doc maps to at least one
  task above. Goals 1–5 → Tasks 4/5 (parsing+exec), 7 (rendering), 6
  (transport), 8 (touch), 10 (validation).
- **Placeholders**: none. All code blocks contain runnable code.
- **Type consistency**: `Operation` types defined in Task 4 are imported
  by Task 5; `DisplayState`, `Page`, `Component` defined in Task 2 are
  used in Tasks 3/5/7/8. `Transport` defined in Task 6 is imported by
  Task 8.
- **Task ordering**: Tasks 4, 5, 6, 7 only depend on Tasks 1–3 and not on
  each other — those four can run in parallel agents. Tasks 8, 9, 10, 11
  depend on the parallel batch and should run sequentially after.

## Execution recommendation

The plan has a natural fan-out / fan-in shape:

- **Sequential prologue:** Tasks 1, 2, 3 (foundation: package, state model, loader).
- **Parallel middle:** Tasks 4, 5, 6, 7 (parser, executor, transport, renderer). Each is independent of the others; all depend on Tasks 2 and 3.
- **Sequential epilogue:** Tasks 8 (Tk app), 9 (entry script), 10 (replay test), 11 (docs). Each depends on the previous.

Three parallel subagents could pick up Tasks 4+5 (parser+executor — naturally paired), 6 (transport), 7 (renderer extraction).

## Commit message convention

All commits use short titles, no body unless something non-obvious about
*why* needs saying. **No `Co-Authored-By: Claude` trailer.** Author as
Evan Severson `208220+eseverson@users.noreply.github.com`. Per-commit pattern:
`git -c user.name="Evan Severson" -c user.email="208220+eseverson@users.noreply.github.com" commit -m "..."`.
