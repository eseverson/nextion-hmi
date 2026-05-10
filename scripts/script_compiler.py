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
_TFTTOOL_DIR = Path(__file__).resolve().parent.parent / "tools" / "TFTTool"
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
