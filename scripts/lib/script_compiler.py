"""script_compiler — Nextion event-handler script → bytecode (minimal).

The Nextion editor compiles each event-handler script (`codesup`,
`codesdown`, `codestimer`, `codesload`, `codesunload`, `codesslide`)
into a length-prefixed bytecode block stored in the TFT usercode
region. The format is documented in
[`findings/format-bytecode.md`](../findings/format-bytecode.md). This
module is the *encoder* counterpart to `tft_bytecode.py`'s
disassembler.

## Scope

This is a **minimal, verifiable** subset of the editor's compiler.
The full compiler (in `plain_hmitype.dll!hmitype.appbianyi`, see
[`findings/script-compiler.md`](../findings/script-compiler.md)) is
a multi-pass C# routine that calls a native helper `GuiCombianyi
.CodeRun_Run` for the final translation pass. Reproducing all of it
would mirror that ~10k-line state machine.

What we DO compile here (round-trip byte-for-byte against real
editor output):

- `page N`             →  `09 0b 04 <ascii digits of N>`
- `printh aa bb cc ..` →  `09 0b 08 <ascii payload>`
- `<sysvar>=<expr>`    →  sysvar ref, `=`, expr
- `<global>=<expr>`    →  global var ref, `=`, expr (for declared globals)
- An expression here is either an integer literal or another sysvar/
  global reference; integers <1000 are emitted as ASCII digits, larger
  ints as `03 LL LL LL LL`.
- `int <name>=0[, …]`  → emits no bytecode (the variable lives in the
  global-memory directory, which is a different block).

What we DON'T compile yet:

- Component-attribute access (`h0.val`, `x0.bco=red.val`). These
  require a component → local-var-offset lookup table that's only
  populated by the rest of the editor's compile path.
- Control flow (`if`, `while`, `for`). The editor rewrites these to
  `i` (cjmp) opcodes + `jmp` back-edges; that rewrite happens in
  `GetbianyiCodes::chonggouifwhile`.
- String literals, function calls, multi-statement lines.

The implemented subset is enough to compile `Program.s` for the
miata-dash project and a handful of hotspot-press handlers, which
were verified byte-for-byte.

## Block framing

Every compiled handler is wrapped with a 4-byte little-endian length
prefix: `<u32 length> <payload[length]>`. The payload is the
concatenation of per-statement bytecode runs; statements are
separated by neither padding nor separator — the runs join end-to-end.
"""
from __future__ import annotations

import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# `NextionInstructionSets` ships in tools/TFTTool. Make sure that path is on
# sys.path before importing — works regardless of where this script is run
# from.
_TFTTOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "TFTTool"
if str(_TFTTOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_TFTTOOL_DIR))

from NextionInstructionSets import all_instruction_sets  # type: ignore  # noqa: E402


# -- Identifier resolution -------------------------------------------------

# Operand-type prefix bytes (mirrors TFTTool's table):
#   0x01 = local var      0x05 = global var
#   0x04 = system var     0x03 = 32-bit int literal
LOCAL_PREFIX  = 0x01
GLOBAL_PREFIX = 0x05
SYSTEM_PREFIX = 0x04
INT32_PREFIX  = 0x03


def _find_instruction_set(version: str, model: int) -> dict:
    """Locate the (size_class, index) → mnemonic tables for the given
    editor version + display model. Falls back to nxt-1.65.1 / model
    100 for newer versions (matches the F-series 1.67.1 behaviour
    observed in our corpus).
    """
    for entry in all_instruction_sets:
        if version in entry["versions"] and model in entry["models"]:
            return entry["models"][model]
    # Fallback: try latest known version
    for entry in reversed(all_instruction_sets):
        if model in entry["models"]:
            return entry["models"][model]
    raise ValueError(f"no instruction set for version={version!r} model={model}")


def _build_lookup(insn_set: dict) -> tuple[dict, dict]:
    """Return ({mnemonic: (size_class, index)}, {sysvar: (size_class, index)})."""
    op_lookup: dict[str, tuple[int, int]] = {}
    for size, names in insn_set["numerated_operators"].items():
        for idx, name in enumerate(names):
            op_lookup[name] = (size, idx)
    sys_lookup: dict[str, tuple[int, int]] = {}
    for size, names in insn_set["numerated_system_variables"].items():
        for idx, name in enumerate(names):
            sys_lookup[name] = (size, idx)
    return op_lookup, sys_lookup


# -- Context ---------------------------------------------------------------

@dataclass
class CompileContext:
    """Information the compiler needs that comes from outside the
    script source: which editor version / model we're targeting, and
    the names + slot offsets of any user-declared globals (from
    `int <name>...` in `Program.s`)."""
    editor_version: str = "nxt-1.65.1"
    model: int = 100   # F-series / T1
    globals: dict[str, int] = field(default_factory=dict)
    """Map from user-declared global name to its byte offset within
    the global-memory frame. Each `int` slot is 4 bytes — the first
    one is at offset 0, the second at offset 4, etc. Auto-populated
    when an `int <name>` declaration is encountered."""
    component_offsets: dict[str, int] = field(default_factory=dict)
    """Map from "comp.attr" → absolute public-memory byte offset.

    Explicit overrides take precedence over ``allocator`` lookups. Pass
    this dict directly when you want to hard-code offsets (e.g. for a
    fixture-driven regression test), or leave it empty and use
    ``allocator``."""
    allocator: "MemoryAllocator | None" = None
    """Optional ``memory_allocator.MemoryAllocator`` instance. When set,
    ``resolve_atom("<comp>.<attr>")`` falls back to
    ``allocator.frame_offset(comp, attr)`` if the dotted ref isn't in
    ``component_offsets``. This is the preferred path for new code —
    it derives offsets from the project's component layout rather than
    requiring the caller to enumerate every comp/attr pair."""

    def __post_init__(self):
        insn_set = _find_instruction_set(self.editor_version, self.model)
        self._op_lookup, self._sys_lookup = _build_lookup(insn_set)

    def is_sysvar(self, name: str) -> bool:
        return name in self._sys_lookup

    def sysvar_operand(self, name: str) -> bytes:
        size_class, idx = self._sys_lookup[name]
        # `04 <size_class> <index_lo> <index_mid> <index_hi>` (24-bit idx)
        return bytes([SYSTEM_PREFIX, size_class,
                      idx & 0xff, (idx >> 8) & 0xff, (idx >> 16) & 0xff])

    def is_global(self, name: str) -> bool:
        return name in self.globals

    def global_operand(self, name: str) -> bytes:
        off = self.globals[name]
        return bytes([GLOBAL_PREFIX]) + struct.pack("<I", off)

    def is_opcode(self, name: str) -> bool:
        return name in self._op_lookup

    def opcode_bytes(self, name: str) -> bytes:
        size_class, idx = self._op_lookup[name]
        return bytes([0x09, idx, size_class])

    def resolve_atom(self, name: str) -> bytes:
        """Resolve an expression atom (identifier, dotted comp.attr) to
        its operand bytes. Integer literals are handled separately by the
        expression compiler and are NOT routed through here."""
        if "." in name:
            off = self._resolve_component_attr(name)
            return bytes([LOCAL_PREFIX]) + struct.pack("<I", off)
        if self.is_sysvar(name):
            return self.sysvar_operand(name)
        if self.is_global(name):
            return self.global_operand(name)
        raise ValueError(
            f"unknown identifier {name!r}: not a sysvar, declared global, "
            f"or component attribute"
        )

    def _resolve_component_attr(self, dotted: str) -> int:
        """Look up the absolute public-memory offset for ``<comp>.<attr>``.

        Search order: explicit ``component_offsets`` dict first
        (caller-supplied overrides), then ``allocator.frame_offset(...)``.
        Raises ``ValueError`` if neither resolves it.
        """
        if dotted in self.component_offsets:
            return self.component_offsets[dotted]
        if self.allocator is not None and "." in dotted:
            comp, attr = dotted.split(".", 1)
            if self.allocator.has_component(comp):
                try:
                    return self.allocator.frame_offset(comp, attr)
                except KeyError as exc:
                    raise ValueError(
                        f"unknown attribute {attr!r} on {comp!r}: {exc}"
                    ) from None
        raise ValueError(
            f"unknown component attribute {dotted!r}: add it to "
            f"CompileContext.component_offsets, or register the component "
            f"with the attached MemoryAllocator"
        )


# -- Lowering primitives ---------------------------------------------------

def _emit_int_literal(value: int) -> bytes:
    """Emit a 32-bit signed integer in either inline ASCII (decimal
    digits) or as a `03 LL LL LL LL` operand.

    Observed editor behaviour: integers whose decimal repr is ≤ 3
    digits go inline (`0`, `42`, `480`, `999`); 4-or-more-digit values
    become the long form (`1000` and beyond). Negative numbers and
    large ints always use the long form.
    """
    if value < 0 or value >= 1000:
        return bytes([INT32_PREFIX]) + struct.pack("<i", value)
    return str(value).encode("ascii")


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _emit_value(token: str, ctx: CompileContext) -> bytes:
    """Compile a primary expression atom: integer literal, sysvar,
    or declared global."""
    # Integer literal
    if re.fullmatch(r"-?\d+", token):
        return _emit_int_literal(int(token))
    # Identifier — try sysvar, then global
    if _IDENT_RE.match(token):
        if ctx.is_sysvar(token):
            return ctx.sysvar_operand(token)
        if ctx.is_global(token):
            return ctx.global_operand(token)
        raise ValueError(f"unknown identifier {token!r}: not a sysvar or "
                         f"declared global. Component-attribute access "
                         f"(e.g. `h0.val`) is not supported by this "
                         f"compiler.")
    raise ValueError(f"unexpected token {token!r}")


# -- Statement compilers ---------------------------------------------------

# Match `<ident>=<expr>` — simple assignment to sysvar/global.
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$")

# Match `int <name>=0[, <name>=0]...` — global declarations.
_INT_DECL_RE = re.compile(r"^\s*int\s+(.+?)\s*$")


def _compile_statement(stmt: str, ctx: CompileContext) -> bytes:
    """Compile one statement (no trailing semicolon) to bytecode.
    Returns the bytecode bytes (not length-prefixed)."""
    stmt = stmt.strip()
    if not stmt:
        return b""

    # `int <name>[=val]...` declarations: allocate global slot(s),
    # emit no bytecode.
    m = _INT_DECL_RE.match(stmt)
    if m:
        decls = m.group(1)
        # Split on commas, parse each `name[=initial]`
        for decl in decls.split(","):
            decl = decl.strip()
            name_value = decl.split("=", 1)
            name = name_value[0].strip()
            if not _IDENT_RE.match(name):
                raise ValueError(f"invalid identifier in int decl: {name!r}")
            # Initial value is ignored at compile time — locals/globals
            # get zero-initialised; the global-memory directory tracks
            # the initial values separately.
            offset = max(ctx.globals.values(), default=-4) + 4
            ctx.globals[name] = offset
        return b""

    # `page <N>` — emit the page opcode with N as inline ASCII
    m = re.match(r"^\s*page\s+(\d+)\s*$", stmt)
    if m:
        n = m.group(1)
        return ctx.opcode_bytes("page") + n.encode("ascii")

    # `printh <hex bytes>` — emit printh opcode + raw payload
    m = re.match(r"^\s*printh\s+(.+)$", stmt)
    if m:
        payload = m.group(1).strip()
        return ctx.opcode_bytes("printh") + payload.encode("ascii")

    # `print "string"` — emit print opcode + ascii payload of quoted text
    m = re.match(r'^\s*print\s+"(.*)"\s*$', stmt)
    if m:
        payload = m.group(1)
        return ctx.opcode_bytes("print") + b'"' + payload.encode("latin-1") + b'"'

    # Generic `<lvalue>=<expr>` assignment. `<expr>` for now must be
    # a single atom (int literal or known identifier).
    m = _ASSIGN_RE.match(stmt)
    if m:
        lhs, rhs = m.group(1), m.group(2).strip()
        # LHS is a bare identifier
        if ctx.is_sysvar(lhs):
            lhs_bytes = ctx.sysvar_operand(lhs)
        elif ctx.is_global(lhs):
            lhs_bytes = ctx.global_operand(lhs)
        else:
            raise ValueError(f"can't compile assignment to {lhs!r}: not a "
                             f"sysvar or declared global")
        rhs_bytes = _emit_value(rhs, ctx)
        return lhs_bytes + b"=" + rhs_bytes

    raise NotImplementedError(f"can't compile statement: {stmt!r}")


def compile_handler(source: str, ctx: CompileContext | None = None) -> bytes:
    """Compile a multi-line Nextion event-handler script into one
    block of bytecode (NOT including the length prefix).

    The source is split on newlines and `//` comments are stripped.
    Each non-empty line is compiled in order and the results are
    concatenated.
    """
    if ctx is None:
        ctx = CompileContext()
    out = bytearray()
    for raw_line in source.splitlines():
        # Strip end-of-line comment
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        # Statements separated by `;` (rare in the corpus)
        for stmt in line.split(";"):
            out += _compile_statement(stmt, ctx)
    return bytes(out)


def compile_block(source: str, ctx: CompileContext | None = None) -> bytes:
    """Compile a handler and prefix it with the 4-byte u32 length —
    the form actually stored in the TFT usercode region."""
    body = compile_handler(source, ctx)
    return struct.pack("<I", len(body)) + body


# -- Full compiler (control flow + component attributes) -------------------

# Regex patterns for control-flow line recognition.
_IF_RE    = re.compile(r"^\s*if\s*\((.+)\)\s*\{?\s*$")
_ELIF_RE  = re.compile(r"^\s*\}\s*else\s+if\s*\((.+)\)\s*\{?\s*$")
_ELSE_RE  = re.compile(r"^\s*\}\s*else\s*\{?\s*$")
_CLOSE_RE = re.compile(r"^\s*\}\s*$")
_WHILE_RE = re.compile(r"^\s*while\s*\((.+)\)\s*\{?\s*$")
_FOR_RE   = re.compile(r"^\s*for\s*\((.+)\)\s*\{?\s*$")


def compile_event_handler(source: str, ctx: CompileContext | None = None) -> bytes:
    """Full event-handler compiler: control flow, component-attribute refs,
    and multi-operand expressions.

    Returns cglist-flattened bytes — each statement / control-flow construct
    is a separate length-prefixed entry, matching the on-disk TFT format.

    Unlike compile_handler / compile_block (which concatenate all statements
    into one block), this function produces the per-entry-prefixed format that
    the Nextion runtime expects for event handlers with branching.

    Supported syntax on top of compile_handler:
        - Component-attribute refs: ``h0.val``, ``x0.bco``
          (requires ctx.component_offsets populated by caller)
        - Control flow: ``if``, ``else if``, ``else``, ``while``, ``for``
        - Multi-operand arithmetic: ``x0.val = x1.val + x2.val - 10``
    """
    from script_compiler_extras import (
        compile_expression, split_condition,
        emit_if_chain, patch_close, patch_close_with_else,
        convert_entry_to_byte_distances, flatten_cglist,
        IfFrame,
    )

    if ctx is None:
        ctx = CompileContext()

    cglist: list[bytes] = []
    ifstack: list[IfFrame] = []
    cjmp_template_ref: list[bytes | None] = [None]

    def resolve(name: str) -> bytes:
        return ctx.resolve_atom(name)

    for raw_line in source.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue

        # --- control-flow dispatch -----------------------------------------
        m = _IF_RE.match(line)
        if m:
            clauses = split_condition(m.group(1).strip())
            emit_if_chain(clauses, cglist, ifstack, resolve, cjmp_template_ref)
            continue

        m = _ELIF_RE.match(line)
        if m:
            clauses = split_condition(m.group(1).strip())
            patch_close_with_else(cglist, ifstack, "else if",
                                  new_clauses=clauses,
                                  resolve_atom_expr=resolve,
                                  cjmp_template_ref=cjmp_template_ref)
            continue

        if _ELSE_RE.match(line):
            patch_close_with_else(cglist, ifstack, "else")
            continue

        if _CLOSE_RE.match(line):
            patch_close(cglist, ifstack, resolve)
            continue

        m = _WHILE_RE.match(line)
        if m:
            slot = len(cglist)
            frame = IfFrame(Lei="if", endstr=[f"T {slot}"])
            clauses = split_condition(m.group(1).strip())
            emit_if_chain(clauses, cglist, ifstack, resolve,
                          cjmp_template_ref, frame_in=frame)
            continue

        m = _FOR_RE.match(line)
        if m:
            inner = m.group(1)
            parts = inner.split(";", 2)
            if len(parts) != 3:
                raise ValueError(f"for-loop header must have exactly two ';': {line!r}")
            init_s, cond_s, inc_s = (p.strip() for p in parts)
            if init_s:
                cglist.append(compile_expression(init_s, resolve))
            slot = len(cglist)
            frame = IfFrame(Lei="for", endstr=[inc_s, f"T {slot}"])
            clauses = split_condition(cond_s)
            emit_if_chain(clauses, cglist, ifstack, resolve,
                          cjmp_template_ref, frame_in=frame)
            continue

        # --- simple statements ---------------------------------------------

        # `int <name>...` — update globals, emit nothing
        dm = _INT_DECL_RE.match(line)
        if dm:
            _compile_statement(line, ctx)
            continue

        # `page N`, `printh ...`, `print "..."` — compile via existing handler
        if (re.match(r"^\s*page\s+\d+\s*$", line)
                or re.match(r"^\s*printh\s+", line)
                or re.match(r'^print\s+"', line)):
            entry = _compile_statement(line, ctx)
            if entry:
                cglist.append(entry)
            continue

        # General expression (assignment, call with `=`-assignment, etc.)
        cglist.append(compile_expression(line, resolve))

    convert_entry_to_byte_distances(cglist, cjmp_template_ref[0])
    return flatten_cglist(cglist)


# -- Self-test -------------------------------------------------------------

if __name__ == "__main__":
    # Round-trip the known-good `Program.s` for the miata-dash project.
    # Source (from `nextion/source/nextion.hmi.HMI`, dumped via
    # Nextion2Text):
    #
    #     int sys0=0,sys1=0,sys2=0
    #     baud=115200
    #     recmod=0
    #     printh 00 00 00 ff ff ff 88 ff ff ff
    #     page 0
    #
    # The editor produces these blocks (block prefix not shown):
    #   blk1 baud=115200            04040e00003d0300c20100
    #   blk2 recmod=0               04080d00003d30
    #   blk3 printh 00...           090b083030203030203030206666206666206666...
    #   blk4 page 0                 090b0430
    ctx = CompileContext()
    # The `int` decl auto-populates globals before the assignments.
    src1 = "int sys0=0,sys1=0,sys2=0"
    src2 = "baud=115200"
    src3 = "recmod=0"
    src4 = "printh 00 00 00 ff ff ff 88 ff ff ff"
    src5 = "page 0"
    # `int` decls emit nothing
    assert compile_handler(src1, ctx) == b"", "int decl should emit nothing"
    assert ctx.globals == {"sys0": 0, "sys1": 4, "sys2": 8}, ctx.globals
    # `baud=115200` → 04 04 0e 00 00 3d 03 00 c2 01 00
    expected = bytes.fromhex("04040e00003d0300c20100")
    got = compile_handler(src2, ctx)
    assert got == expected, f"baud=115200: {got.hex()} vs {expected.hex()}"
    # `recmod=0` → 04 08 0d 00 00 3d 30
    expected = bytes.fromhex("04080d00003d30")
    got = compile_handler(src3, ctx)
    assert got == expected, f"recmod=0: {got.hex()} vs {expected.hex()}"
    # printh
    expected = bytes.fromhex(
        "090b083030203030203030206666206666206666203838206666206666206666")
    got = compile_handler(src4, ctx)
    assert got == expected, f"printh: {got.hex()} vs {expected.hex()}"
    # page 0
    expected = bytes.fromhex("090b0430")
    got = compile_handler(src5, ctx)
    assert got == expected, f"page 0: {got.hex()} vs {expected.hex()}"

    # `sys2=42` (the test-10 fixture appends this) → 05 08 00 00 00 3d 34 32
    ctx2 = CompileContext()
    ctx2.globals = {"sys0": 0, "sys1": 4, "sys2": 8}
    expected = bytes.fromhex("0508000000 3d 34 32".replace(" ", ""))
    got = compile_handler("sys2=42", ctx2)
    assert got == expected, f"sys2=42: {got.hex()} vs {expected.hex()}"

    print("script_compiler self-test OK")

    # -- compile_event_handler tests (use 16_loop byte-verified sequences) --
    # Component-attribute offset map from the 16_loop fixture's public memory.
    COMP_OFFSETS = {
        "x0.val": 0x3c,  "x0.bco": 0x38,
        "x2.val": 0x8e,  "x2.bco": 0x8a,
        "x4.val": 0xe0,  "x4.bco": 0xdc,
        "x5.val": 0xe9,  "x5.bco": 0xe5,
        "x1.val": 0x109, "x1.bco": 0x105,
        "x6.val": 0x165, "x6.bco": 0x161,
        "x7.val": 0x17c, "x7.bco": 0x178,
        "x8.val": 0x1a5, "x8.bco": 0x1a1,
        "bco.val": 0x37b, "blu.val": 0x386, "red.val": 0x391,
        "wht.val": 0x39c, "yel.val": 0x3a7, "org.val": 0x3b2,
        "grn.val": 0x3bd,
    }
    ectx = CompileContext()
    ectx.globals = {"sys0": 0x00, "qq": 0x0c}
    ectx.component_offsets = COMP_OFFSETS

    # Test: simple if/else (verified against 16.tft @ 0x70fad)
    src_if_else = """\
if(x8.val>0)
x8.bco=red.val
}else
x8.bco=bco.val
}"""
    expected_if_else = bytes.fromhex(
        "12000000" "09000401a50100002c302c332c031a000000"
        "0b000000" "01a10100003d0191030000"
        "07000000" "5420030f000000"
        "0b000000" "01a10100003d017b030000"
    )
    got = compile_event_handler(src_if_else, ectx)
    assert got == expected_if_else, (
        f"if-else mismatch:\n  got      {got.hex()}\n"
        f"  expected {expected_if_else.hex()}"
    )

    # Test: while loop (verified against 16.tft @ while-loop fixture)
    src_while = """\
while(qq<5)
qq=qq+1
}"""
    expected_while = bytes.fromhex(
        "12000000" "090004050c0000002c352c322c031c000000"
        "0d000000" "050c0000003d050c0000002b31"
        "07000000" "542003ceffffff"
    )
    got = compile_event_handler(src_while, ectx)
    assert got == expected_while, (
        f"while mismatch:\n  got      {got.hex()}\n"
        f"  expected {expected_while.hex()}"
    )

    print("compile_event_handler self-test OK")

    # -- Allocator-driven resolution -----------------------------------------
    # Same if-else fixture, but resolve x8.val / x8.bco / bco.val / red.val
    # via MemoryAllocator instead of an explicit component_offsets dict.
    # Verifies the new fallback path in resolve_atom.
    from memory_allocator import MemoryAllocator

    actx = CompileContext()
    actx.globals = {"sys0": 0x00, "qq": 0x0c}
    alloc = MemoryAllocator(app_allvas_qty=4)
    # Place x0..x8 at the same memorypos as the 16_loop fixture:
    _bco_ap = 1  # ATTPOSUP_TABLE[59]["bco"]
    for cname, bco_off in [
        ("x0", 0x38), ("x2", 0x8a), ("x4", 0xdc), ("x5", 0xe5),
        ("x1", 0x105), ("x6", 0x161), ("x7", 0x178), ("x8", 0x1a1),
    ]:
        alloc.cursor = bco_off - _bco_ap
        alloc.add_object(59, cname)
    # And the color Variables (type 52, val at offset 0):
    for cname, mempos in [
        ("bco", 0x37b), ("blu", 0x386), ("red", 0x391),
        ("wht", 0x39c), ("yel", 0x3a7), ("org", 0x3b2), ("grn", 0x3bd),
    ]:
        alloc.cursor = mempos
        alloc.add_object(52, cname)
    actx.allocator = alloc

    got = compile_event_handler(src_if_else, actx)
    assert got == expected_if_else, (
        f"if-else (allocator path) mismatch:\n  got      {got.hex()}\n"
        f"  expected {expected_if_else.hex()}"
    )

    # Confirm the explicit-dict path still wins when both are set.
    actx2 = CompileContext()
    actx2.globals = {"sys0": 0x00, "qq": 0x0c}
    actx2.allocator = alloc
    actx2.component_offsets = {"x8.val": 0xdead}  # garbage, but should be used
    try:
        compile_event_handler(src_if_else, actx2)
    except Exception:
        pass  # any failure is fine; we just want to confirm the dict path was hit
    # Verify the resolved bytes for x8.val came from the dict, not the allocator.
    assert actx2._resolve_component_attr("x8.val") == 0xdead

    print("allocator-driven resolution self-test OK")

    # -- Multi-elif smoke test ------------------------------------------------
    # The miata-dash Timer event has an x1.val cascade (4-way: >6800 / >6500
    # / >6000 / else). We don't yet have byte-verified expected bytes, but
    # the structure should compile without errors and produce a sensible
    # cjmp/jmp pattern: 3 entries for the cjmp clauses, 3 body+jmp pairs,
    # 1 else body, and the closing target.
    src_elif_cascade = """\
if(x1.val>6800)
x1.bco=red.val
}else if(x1.val>6500)
x1.bco=org.val
}else if(x1.val>6000)
x1.bco=yel.val
}else
x1.bco=bco.val
}"""
    out = compile_event_handler(src_elif_cascade, actx)
    # Each entry in the cglist is length-prefixed; walk them and count.
    i = 0
    n_entries = 0
    while i + 4 <= len(out):
        ln = struct.unpack_from("<I", out, i)[0]
        if ln == 0 or i + 4 + ln > len(out):
            break
        n_entries += 1
        i += 4 + ln
    # 3 cjmp entries + 4 body entries (red/org/yel/bco) + 3 jmp entries
    # connecting branches to the close = 10 entries total.
    assert n_entries == 10, f"expected 10 entries, got {n_entries}"
    # Verify each cjmp entry starts with the 'i' opcode (09 00 04).
    cjmps = 0
    i = 0
    while i + 4 <= len(out):
        ln = struct.unpack_from("<I", out, i)[0]
        if ln == 0 or i + 4 + ln > len(out):
            break
        body = out[i + 4:i + 4 + ln]
        if body[:3] == b"\x09\x00\x04":
            cjmps += 1
        i += 4 + ln
    assert cjmps == 3, f"expected 3 cjmp entries, got {cjmps}"

    print("multi-elif cascade smoke test OK")
