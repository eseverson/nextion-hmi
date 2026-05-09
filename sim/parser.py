from __future__ import annotations
from dataclasses import dataclass
from typing import Union
import re

from sim import expr as _expr


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


@dataclass(frozen=True)
class ExprValue:
    """RHS expression that needs evaluation against a ScriptContext."""
    node: object  # sim.expr.Expr


Value = Union[IntLiteral, StrLiteral, AttrRef, ExprValue]


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
    value: object  # int or ExprValue


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


@dataclass(frozen=True)
class TouchInject:
    """Sim-only extension: scripted touch input from outside the process.

    `target` is a component name (str) or component id (int) on the active
    page. `action` is "press", "release", or "click" (= press immediately
    followed by release).
    """
    target: object  # str or int
    action: str     # "press" | "release" | "click"


Operation = Union[
    Mutation, PageSwitch, GlobalSet, Refresh,
    ClearScreen, Print, PrintH, TouchInject, Unsupported,
]


_GLOBAL_NAMES = {"dim", "dims", "baud", "recmod", "thup", "usup",
                 "sys0", "sys1", "sys2"}
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

    # `touch <target> [press|release|click]` — sim-only injection.
    # Default action is `click` (press + release).
    if text.startswith("touch "):
        rest = text[len("touch "):].strip().split()
        if not rest:
            return Unsupported(text, "touch: missing target")
        target_s = rest[0]
        action = rest[1].lower() if len(rest) > 1 else "click"
        if action not in ("press", "release", "click"):
            return Unsupported(text, f"touch: bad action {action!r}")
        target: object
        if _INT_RE.fullmatch(target_s):
            target = int(target_s)
        elif _IDENT_RE.fullmatch(target_s):
            target = target_s
        else:
            return Unsupported(text, f"touch: bad target {target_s!r}")
        return TouchInject(target, action)

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
        # Try simple literal/attr-ref first, else fall back to a parsed
        # expression AST. Empty RHS is still invalid.
        v = _parse_value(rhs)
        if v is None and rhs:
            try:
                v = ExprValue(_expr.parse(rhs))
            except Exception:
                return Unsupported(text, "parse: bad value")
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
            if isinstance(v, (AttrRef, ExprValue)):
                return GlobalSet(lhs, v)
            return Unsupported(text, "global: expected int")
        return Unsupported(text, "parse: bare identifier lhs")

    return Unsupported(text, "parse: unrecognised form")
