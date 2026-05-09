"""Nextion event-script parser + executor.

Parses the plain-text source from `codes*` blobs in the HMI (or `Program.s`)
into a statement AST, and walks that AST against a `ScriptContext`. Supports
the constructs the miata-dash project uses plus the rest of P1 from the spec:

- `int <name>=<expr>(, ...)` declarations
- `<name> = <expr>`, `<obj>.<attr> = <expr>` assignments
- `if (cond) { body } else if (cond) { body } else { body }`
- `while (cond) { body }`
- `for (init; cond; step) { body }`
- procedure calls (`page <n>`, `vis <obj>,<v>`, `cls <c>`, `fill <…>`, etc.)
- `//` line comments

Calls are dispatched to a registry of handlers; unknown calls log at WARN.
The expression evaluator from `sim.expr` is reused for all RHS values.
"""
from __future__ import annotations
from dataclasses import dataclass
import logging
import re
from typing import Optional, Callable

from sim.expr import parse as parse_expr, evaluate as eval_expr

log = logging.getLogger("sim.script")


# ---------- AST ----------

@dataclass(frozen=True)
class IntDecl:
    decls: tuple  # tuple[(name, Expr)]


@dataclass(frozen=True)
class Assign:
    target: str  # "name" or "obj.attr"
    value: object  # Expr


@dataclass(frozen=True)
class If:
    cond: object  # Expr
    then_block: tuple  # tuple[Stmt]
    elifs: tuple  # tuple[(Expr, tuple[Stmt])]
    else_block: tuple  # tuple[Stmt]


@dataclass(frozen=True)
class While:
    cond: object  # Expr
    body: tuple


@dataclass(frozen=True)
class For:
    init: Optional[object]   # Stmt or None
    cond: Optional[object]   # Expr or None (None == always true)
    step: Optional[object]   # Stmt or None
    body: tuple


@dataclass(frozen=True)
class Call:
    name: str
    args: str  # raw arg string (post-name); split by handler


# ---------- Preprocessor / parser ----------

class ParseError(ValueError):
    pass


def _strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        i = line.find("//")
        if i >= 0:
            line = line[:i]
        out.append(line)
    return "\n".join(out)


def _normalise(text: str) -> list[str]:
    """Strip comments, ensure { and } sit on their own lines, split."""
    text = _strip_comments(text)
    # Put braces on their own lines so the line-based parser is straightforward.
    text = text.replace("{", "\n{\n").replace("}", "\n}\n")
    return [l.strip() for l in text.splitlines() if l.strip()]


_ASSIGN_RE = re.compile(r"(?<![=!<>])=(?!=)")


def _split_assign(line: str) -> Optional[tuple[str, str]]:
    """Find the assignment `=` (not ==, !=, <=, >=) and split."""
    m = _ASSIGN_RE.search(line)
    if not m:
        return None
    return line[:m.start()].strip(), line[m.end():].strip()


def _parse_int_decl(line: str) -> IntDecl:
    body = line[len("int "):].strip()
    decls: list[tuple[str, object]] = []
    # Split on commas at depth 0 (parens/strings).
    pieces = _split_top_level(body, ",")
    for piece in pieces:
        parts = _split_assign(piece)
        if parts is None:
            decls.append((piece.strip(), parse_expr("0")))
            continue
        name, rhs = parts
        decls.append((name.strip(), parse_expr(rhs)))
    return IntDecl(tuple(decls))


def _split_top_level(s: str, sep: str) -> list[str]:
    out: list[str] = []
    depth = 0
    in_str = False
    cur = []
    i = 0
    while i < len(s):
        ch = s[i]
        if in_str:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            cur.append(ch)
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and ch == sep:
            out.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    if cur:
        out.append("".join(cur))
    return out


def _take_paren_expr(line: str, head: str) -> tuple[object, str]:
    """For lines like `if(...)`, `if (...)`, `while(...)`. Returns (expr, rest)."""
    rest = line[len(head):].lstrip()
    if not rest.startswith("("):
        raise ParseError(f"expected '(' after {head!r} in {line!r}")
    # Find matching close-paren.
    depth = 0
    in_str = False
    end = -1
    for i, ch in enumerate(rest):
        if in_str:
            if ch == "\\":
                continue
            if ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        raise ParseError(f"unterminated paren in {line!r}")
    cond = rest[1:end].strip()
    tail = rest[end + 1:].strip()
    return parse_expr(cond), tail


def _parse_simple(line: str):
    """Parse a single non-control statement: declaration / assignment / call."""
    if line.startswith("int "):
        return _parse_int_decl(line)
    parts = _split_assign(line)
    if parts is not None:
        lhs, rhs = parts
        return Assign(lhs, parse_expr(rhs))
    # Call: head whitespace tail
    head, _, tail = line.partition(" ")
    return Call(head.strip(), tail.strip())


def _expect_open_brace(lines, i: int) -> int:
    if i >= len(lines) or lines[i] != "{":
        raise ParseError(f"expected '{{' at line {i}: got {lines[i] if i < len(lines) else 'EOF'!r}")
    return i + 1


def _parse_block(lines: list[str], i: int) -> tuple[tuple, int]:
    """Parse statements until a `}` line. Returns (body_tuple, index_after_brace)."""
    body: list = []
    while i < len(lines):
        line = lines[i]
        if line == "}":
            return tuple(body), i + 1
        if line.startswith("if(") or line.startswith("if "):
            stmt, i = _parse_if(lines, i)
            body.append(stmt)
            continue
        if line.startswith("while(") or line.startswith("while "):
            stmt, i = _parse_while(lines, i)
            body.append(stmt)
            continue
        if line.startswith("for(") or line.startswith("for "):
            stmt, i = _parse_for(lines, i)
            body.append(stmt)
            continue
        body.append(_parse_simple(line))
        i += 1
    raise ParseError("unexpected EOF inside block")


def _parse_if(lines: list[str], i: int) -> tuple[If, int]:
    cond, tail = _take_paren_expr(lines[i], "if")
    if tail and tail != "{":
        raise ParseError(f"junk after if(): {tail!r}")
    i += 1
    if tail != "{":
        i = _expect_open_brace(lines, i)
    then_block, i = _parse_block(lines, i)
    elifs: list[tuple[object, tuple]] = []
    else_block: tuple = ()
    while i < len(lines):
        line = lines[i]
        if line.startswith("else if(") or line.startswith("else if "):
            ec, et = _take_paren_expr(line, "else if")
            i += 1
            if et != "{":
                i = _expect_open_brace(lines, i)
            eb, i = _parse_block(lines, i)
            elifs.append((ec, eb))
            continue
        if line == "else" or line.startswith("else "):
            i += 1
            i = _expect_open_brace(lines, i)
            else_block, i = _parse_block(lines, i)
            break
        break
    return If(cond, then_block, tuple(elifs), else_block), i


def _parse_while(lines: list[str], i: int) -> tuple[While, int]:
    cond, tail = _take_paren_expr(lines[i], "while")
    i += 1
    if tail != "{":
        i = _expect_open_brace(lines, i)
    body, i = _parse_block(lines, i)
    return While(cond, body), i


def _parse_for(lines: list[str], i: int) -> tuple[For, int]:
    line = lines[i]
    # extract `for( init; cond; step )`
    open_ = line.find("(")
    close = line.rfind(")")
    if open_ == -1 or close == -1:
        raise ParseError(f"bad for: {line!r}")
    inner = line[open_ + 1:close]
    parts = _split_top_level(inner, ";")
    if len(parts) != 3:
        raise ParseError(f"for() needs 3 parts, got {len(parts)} in {line!r}")
    init = _parse_simple(parts[0].strip()) if parts[0].strip() else None
    cond = parse_expr(parts[1].strip()) if parts[1].strip() else None
    step = _parse_simple(parts[2].strip()) if parts[2].strip() else None
    i += 1
    i = _expect_open_brace(lines, i)
    body, i = _parse_block(lines, i)
    return For(init, cond, step, body), i


def parse_script(text: str) -> tuple:
    """Parse a script source string into a tuple of top-level statements."""
    lines = _normalise(text)
    body: list = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("if(") or line.startswith("if "):
            stmt, i = _parse_if(lines, i)
            body.append(stmt)
            continue
        if line.startswith("while(") or line.startswith("while "):
            stmt, i = _parse_while(lines, i)
            body.append(stmt)
            continue
        if line.startswith("for(") or line.startswith("for "):
            stmt, i = _parse_for(lines, i)
            body.append(stmt)
            continue
        if line == "{" or line == "}":
            raise ParseError(f"stray brace at top level (line {i}: {line!r})")
        body.append(_parse_simple(line))
        i += 1
    return tuple(body)


# ---------- Executor ----------

# Procedure registry. Handlers receive (ctx, args:str) and return None.
# Set externally (sim.app fills these) to keep this module dependency-free.
ProcHandler = Callable[[object, str], None]
_PROCS: dict[str, ProcHandler] = {}


def register_proc(name: str, fn: ProcHandler) -> None:
    _PROCS[name] = fn


def execute_block(stmts: tuple, ctx) -> None:
    for s in stmts:
        execute_stmt(s, ctx)


def execute_stmt(s, ctx) -> None:
    if isinstance(s, IntDecl):
        for name, expr in s.decls:
            ctx.declare_local(name, int(eval_expr(expr, ctx)))
        return
    if isinstance(s, Assign):
        v = eval_expr(s.value, ctx)
        if "." in s.target:
            obj, attr = s.target.split(".", 1)
            ctx.write_attr(obj, attr, v)
        else:
            ctx.write_name(s.target, v)
        return
    if isinstance(s, If):
        if _truthy(eval_expr(s.cond, ctx)):
            execute_block(s.then_block, ctx)
            return
        for ec, eb in s.elifs:
            if _truthy(eval_expr(ec, ctx)):
                execute_block(eb, ctx)
                return
        if s.else_block:
            execute_block(s.else_block, ctx)
        return
    if isinstance(s, While):
        # Bound the loop to keep a runaway script from hanging the sim.
        guard = 100_000
        while _truthy(eval_expr(s.cond, ctx)) and guard > 0:
            execute_block(s.body, ctx)
            guard -= 1
        if guard == 0:
            log.warning("while loop hit 100000-iter guard, aborting")
        return
    if isinstance(s, For):
        if s.init is not None:
            execute_stmt(s.init, ctx)
        guard = 100_000
        while True:
            if s.cond is not None and not _truthy(eval_expr(s.cond, ctx)):
                break
            execute_block(s.body, ctx)
            if s.step is not None:
                execute_stmt(s.step, ctx)
            guard -= 1
            if guard == 0:
                log.warning("for loop hit 100000-iter guard, aborting")
                break
        return
    if isinstance(s, Call):
        h = _PROCS.get(s.name)
        if h is None:
            log.debug("unknown proc %r args=%r", s.name, s.args)
            return
        try:
            h(ctx, s.args)
        except Exception:
            log.exception("proc %r failed (args=%r)", s.name, s.args)
        return
    raise TypeError(f"unknown stmt {s!r}")


def _truthy(v) -> bool:
    if isinstance(v, str):
        return len(v) > 0
    return bool(v)


def run(text: str, ctx) -> None:
    """Parse and execute a script source string."""
    if not text or not text.strip():
        return
    stmts = parse_script(text)
    execute_block(stmts, ctx)
