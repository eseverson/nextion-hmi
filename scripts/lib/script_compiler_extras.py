"""script_compiler_extras — control-flow + multi-operand expression encoding.

This is the companion to `script_compiler.py`. It does NOT modify the
existing minimal compiler; instead it provides helpers that can be
integrated when the controller is ready.

What's here:

- `compile_expression`: tokenise a Nextion source expression and emit the
  byte form (infix, single-byte ASCII operators, operands compiled
  via a supplied resolver). Handles arbitrary-arity arithmetic and
  bitwise binary ops.

- `compile_event_with_control_flow`: top-level driver that handles
  `if/else/else if/while/for` + multi-operand expressions, producing the
  per-entry bytecode list (pre-flatten) AND the byte-distance-converted
  final flat block.

- Helper primitives `negate_comparator`, `convert_entry_to_byte_distances`,
  and `is_control_flow_entry` mirror the editor's `getfanyiif` and the
  Stage 2 patching loop.

See `findings/script-control-flow.md` for the algorithm derivation from
the editor's `plain_hmitype.dll` IL. Verified against
`tests/editor outputs/16_loop/16.tft` — see the self-test at the bottom.

NOTE: This module does NOT touch `script_compiler.py` itself. Integration
into the main compiler is intentionally left as a separate step. Per the
hand-off plan, the controller is responsible for wiring this into
`script_compiler._compile_statement` once both this and the
attribute-resolver land.
"""
from __future__ import annotations

import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# --- Comparator tables (mirrors getendpos + getfanyiif) ---

# ASCII byte → endid (the panduan byte set by getendpos)
COMPARATOR_ENDID: dict[str, int] = {
    "==": 1,
    "<":  2,
    ">":  3,
    "<=": 4,
    ">=": 5,
    "!=": 6,
    "&&": 250,
    "||": 251,
}

# Reverse lookup: endid → operator string (only for the comparator IDs 1..6)
ENDID_COMPARATOR: dict[int, str] = {v: k for k, v in COMPARATOR_ENDID.items() if v < 250}

# getfanyiif's swap table — operator ↔ its logical negation
NEGATE_COMPARATOR: dict[int, int] = {
    1: 6, 6: 1,    # == ↔ !=
    2: 5, 5: 2,    # <  ↔ >=
    3: 4, 4: 3,    # >  ↔ <=
}

# Arithmetic/bitwise binary operator ASCII bytes (used in expressions, NOT
# in cjmp comparator slot — those use the digit form via COMPARATOR_ENDID).
ARITH_OP_BYTES: dict[str, int] = {
    "+": 0x2b, "-": 0x2d, "*": 0x2a, "/": 0x2f,
    "&": 0x26, "|": 0x7c, "^": 0x5e, "%": 0x25,
}

# Inline-ASCII threshold for int literals — the editor uses inline ASCII
# decimal digits when the value's textual repr is ≤3 characters (including
# the optional minus sign), and the 5-byte `03 LL LL LL LL` long form
# otherwise. So 0..999 and -1..-99 are inline; 1000+ and -100 and below
# are long-form.
def _emit_int_literal(value: int) -> bytes:
    """Mirror the editor's `Strmake_StrToS32`-driven literal encoding."""
    s = str(value)
    if len(s) <= 3:
        return s.encode("ascii")
    return bytes([0x03]) + struct.pack("<i", value)


# --- Expression compiler --------------------------------------------------

# A resolver function: takes an atom string (an identifier like 'h0.val',
# 'sys0', 'dim') and returns its operand bytes. The default resolver
# delegates to `script_compiler.CompileContext`. Callers may pass a more
# capable resolver that also handles component-attribute refs (task 1a).
Resolver = Callable[[str], bytes]


# Tokeniser regex: matches an integer (incl. negative), a dotted
# identifier (e.g. `h0.val`), a bare identifier, a parenthesised
# sub-expression, or a single-char operator.
_TOKEN_RE = re.compile(
    r"""
    \s*
    (
        -? \d+                                   # int literal
      | [A-Za-z_][A-Za-z0-9_]* (?:\.[A-Za-z_][A-Za-z0-9_]*)?   # dotted ident
      | [+\-*/&|^%]                              # binary op (NOT comparison)
      | =                                        # assignment
    )
    \s*
    """,
    re.VERBOSE,
)


def _tokenise_expr(text: str) -> list[str]:
    """Split an arithmetic expression into atoms and single-char
    operators. Does NOT handle comparison operators or `&&`/`||` — those
    are split earlier in the cjmp pipeline."""
    out: list[str] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m or m.start(1) == m.end(1):
            raise ValueError(f"can't tokenise at {pos}: {text[pos:pos+20]!r}")
        tok = m.group(1)
        out.append(tok)
        pos = m.end()
    return out


def compile_expression(text: str, resolve: Resolver) -> bytes:
    """Compile an infix arithmetic/bitwise expression to bytecode.

    The Nextion editor emits expressions LEFT-TO-RIGHT INFIX. Operators
    are single ASCII bytes between operands. There is no precedence
    reordering — the source string's order is preserved.

    Atoms are integer literals or identifiers (resolved via `resolve`).
    """
    tokens = _tokenise_expr(text)
    if not tokens:
        raise ValueError(f"empty expression: {text!r}")
    out = bytearray()
    expect_op = False
    for tok in tokens:
        if expect_op:
            if tok in ARITH_OP_BYTES:
                out.append(ARITH_OP_BYTES[tok])
                expect_op = False
            elif tok == "=":
                out.append(0x3d)
                expect_op = False
            else:
                raise ValueError(f"expected operator, got {tok!r}")
        else:
            # An atom: int literal or identifier
            if re.fullmatch(r"-?\d+", tok):
                out += _emit_int_literal(int(tok))
            else:
                out += resolve(tok)
            expect_op = True
    if not expect_op:
        raise ValueError(f"expression ends on an operator: {text!r}")
    return bytes(out)


# --- Comparator helpers ---------------------------------------------------

def negate_comparator(endid: int) -> int:
    """Return the negated comparator id (getfanyiif). Only valid for
    1..6; other endids (250/251/255) pass through unchanged."""
    return NEGATE_COMPARATOR.get(endid, endid)


# Split a condition like `a<b && c==d` or `sys0>20 || sys0<-20` into
# clauses + the separator endids between them. Mirrors chonggouifwhile.
_CONDITION_SPLIT_RE = re.compile(r"(==|!=|<=|>=|<|>|&&|\|\|)")


def split_condition(cond: str) -> list[tuple[str, str, int]]:
    """Parse a condition into a list of `(lhs, rhs, comparator_endid)`
    triples, one per comparison clause.

    Example:
        split_condition("sys0>20 || sys0<-20")
        → [("sys0", "20", 3), ("sys0", "-20", 2)]
        split_condition("h0.val<2000")
        → [("h0.val", "2000", 2)]
    """
    # First split on && / ||, then split each chunk on comparator
    parts = re.split(r"(&&|\|\|)", cond)
    # parts will be [clause_text, separator, clause_text, separator, ...]
    result: list[tuple[str, str, int]] = []
    for i in range(0, len(parts), 2):
        clause_text = parts[i].strip()
        # Find the comparator inside the clause
        m = re.search(r"(==|!=|<=|>=|<|>)", clause_text)
        if not m:
            raise ValueError(f"no comparator in clause: {clause_text!r}")
        op = m.group(1)
        lhs = clause_text[:m.start()].strip()
        rhs = clause_text[m.end():].strip()
        result.append((lhs, rhs, COMPARATOR_ENDID[op]))
    return result


# --- Control-flow code emitter --------------------------------------------

@dataclass
class IfFrame:
    Lei: str = "if"            # "if", "else", "while", "for"
    ListIndex: int = -1        # cglist slot index of the cjmp to patch at `}`
    endstr: list[str] = field(default_factory=list)  # pending lines emitted before back-jmp
    chain_jmps: list[int] = field(default_factory=list)
    # ↑ for if-elseif-else chains: jmp-over slots that need patching at
    # the chain's final `}`. The innermost (current) frame holds them.


def emit_cjmp(lhs_bytes: bytes, rhs_bytes: bytes, endid: int,
              placeholder_int: int) -> bytes:
    """Build a cjmp entry. The trailing branch target is a 4-byte int —
    initially an *entry distance* (will be byte-converted in Stage 2)."""
    op_ascii = str(endid).encode("ascii")
    body = (b"\x09\x00\x04" + lhs_bytes + b"\x2c" + rhs_bytes
            + b"\x2c" + op_ascii + b"\x2c"
            + b"\x03" + struct.pack("<i", placeholder_int))
    return body


def emit_jmp(placeholder_int: int) -> bytes:
    """Build a jmp entry. Same encoding as cjmp's target."""
    return b"\x54\x20\x03" + struct.pack("<i", placeholder_int)


def is_control_flow_entry(entry: bytes, cjmp_template: bytes | None) -> bool:
    """Detect whether a cglist entry's trailing 4 bytes are a branch
    offset to be rewritten in Stage 2. Mirrors the IL test at
    `GetbianyiCodes` IL_13a1–IL_1418."""
    if len(entry) < 7:
        return False
    if entry[0] == 0x54 and entry[1] == 0x20:
        return True
    if entry[0] == 0x49 and entry[1] == 0x20:
        return True
    if cjmp_template is not None and entry[:3] == cjmp_template:
        return True
    return False


def convert_entry_to_byte_distances(cglist: list[bytes],
                                    cjmp_template: bytes | None) -> None:
    """The Stage 2 byte-distance conversion pass. Mirrors the IL loop at
    `GetbianyiCodes` IL_1385–IL_15c7. Mutates cglist in place.

    For each control-flow entry at slot i, reads the trailing 4 bytes as
    a signed int32 entry-distance, and rewrites them to the equivalent
    byte-distance (accounting for each skipped entry's 4-byte length
    prefix that will be prepended at flatten time).
    """
    n = len(cglist)
    for i in range(n):
        entry = cglist[i]
        if not is_control_flow_entry(entry, cjmp_template):
            continue
        entry_distance = struct.unpack("<i", entry[-4:])[0]
        if entry_distance > 0:
            # Forward: sum the next `entry_distance` entries' lengths
            # (each with its 4-byte length prefix accounted for).
            byte_distance = sum(
                len(cglist[i + 1 + k]) + 4
                for k in range(entry_distance)
                if i + 1 + k < n
            )
        elif entry_distance < 0:
            # Backward (back-jmp): sum the entries from (i + 1 +
            # entry_distance) up to AND INCLUDING the back-jmp's own slot
            # (i). Mirrors the IL_1559 loop which iterates V_5 from
            # entry_distance up to (but not including) 0, indexing
            # cglist[i + 1 + V_5].
            byte_distance = -sum(
                len(cglist[i + 1 + k]) + 4
                for k in range(entry_distance, 0)
                if 0 <= i + 1 + k < n
            )
        else:
            byte_distance = 0
        cglist[i] = entry[:-4] + struct.pack("<i", byte_distance)


def flatten_cglist(cglist: list[bytes]) -> bytes:
    """Concatenate cglist entries with 4-byte length prefixes — the final
    on-disk form of an event handler block."""
    out = bytearray()
    for entry in cglist:
        out += struct.pack("<I", len(entry)) + entry
    return bytes(out)


# --- Top-level driver -----------------------------------------------------

def emit_if_chain(clauses: list[tuple[str, str, int]],
                  cglist: list[bytes],
                  ifstack: list[IfFrame],
                  resolve_atom_expr: Callable[[str], bytes],
                  cjmp_template_ref: list[bytes | None],
                  frame_in: IfFrame | None = None) -> None:
    """Emit cjmps for a multi-clause condition. All but the last clause
    are negated (per `getfanyiif`) and given an entry-distance target
    pointing to body-start. The last clause keeps its natural comparator
    and a placeholder target (patched at `}`).
    """
    n = len(clauses)
    for i, (lhs, rhs, endid) in enumerate(clauses):
        is_last = (i == n - 1)
        # LHS and RHS may be either bare atoms or full sub-expressions
        # (e.g., `h0.val + 5`). Run them through `compile_expression` so
        # int literals and infix arithmetic both work.
        lhs_b = compile_expression(lhs, resolve_atom_expr)
        rhs_b = compile_expression(rhs, resolve_atom_expr)
        if is_last:
            entry = emit_cjmp(lhs_b, rhs_b, endid, placeholder_int=0)
        else:
            # Early clause: negate, target body-start (n - 1 - i entries ahead).
            neg_endid = negate_comparator(endid)
            entry = emit_cjmp(lhs_b, rhs_b, neg_endid, placeholder_int=n - 1 - i)
        cglist.append(entry)
        if cjmp_template_ref[0] is None:
            cjmp_template_ref[0] = entry[:3]

    frame = frame_in or IfFrame()
    frame.Lei = "if"
    frame.ListIndex = len(cglist) - 1   # the LAST clause's cjmp
    ifstack.append(frame)


def patch_close(cglist: list[bytes], ifstack: list[IfFrame],
                resolve_atom_expr: Callable[[str], bytes]) -> None:
    """Handle `}` — emit any pending endstr (loop back-jmps + for-loop
    increment), patch the cjmp's branch target, AND patch any
    accumulated chain jmps from `else if`/`else` siblings."""
    frame = ifstack.pop()

    # Emit endstr items (used for while/for: increment + back-jmp)
    for s in frame.endstr:
        s = s.strip()
        if s.startswith("T "):
            # Back-jmp placeholder. `T <N>` where N = cjmp's slot index
            # (saved BEFORE the cjmp was added, so N is the cjmp's slot).
            n = int(s[2:].strip())
            # Entry distance = N - current_count - 1
            # (negative, points back to the cjmp's slot prefix at flatten time)
            entry_distance = n - len(cglist) - 1
            cglist.append(emit_jmp(entry_distance))
        else:
            # An assignment-style line (e.g., for-loop increment)
            # Compile as an expression statement.
            cglist.append(compile_expression(s, resolve_atom_expr))

    # Patch the cjmp's own target (which has placeholder_int=0)
    cjmp_slot = frame.ListIndex
    if 0 <= cjmp_slot < len(cglist):
        entry_distance = (len(cglist) - 1) - cjmp_slot
        cglist[cjmp_slot] = cglist[cjmp_slot][:-4] + struct.pack("<i", entry_distance)

    # Patch any accumulated chain jmps (from sibling `else if` / `else`
    # branches). They should all jump to the same point: just past the
    # current end of cglist (= after the last body's bytes).
    final_slot = len(cglist) - 1
    for jmp_slot in frame.chain_jmps:
        entry_distance = final_slot - jmp_slot
        cglist[jmp_slot] = cglist[jmp_slot][:-4] + struct.pack("<i", entry_distance)


def patch_close_with_else(cglist: list[bytes], ifstack: list[IfFrame],
                          kind: str,
                          new_clauses: list[tuple[str, str, int]] | None = None,
                          resolve_atom_expr: Callable[[str], bytes] | None = None,
                          cjmp_template_ref: list[bytes | None] | None = None
                          ) -> None:
    """Handle `}else` and `}else if(...)`.

    1. Emit a forward jmp at the end of the current if's body (the
       "jmp-over-else" placeholder). Patched at the chain's final `}`.
    2. Patch the just-popped if's cjmp to land at the new body start.
    3. For `}else if(...)`: push a NEW cjmp frame inheriting the
       accumulated chain_jmps PLUS the new jmp-over slot.
    4. For `}else`: push an else frame that carries the chain_jmps
       (no new cjmp; the `}` after the else body closes everything).
    """
    # 1. Emit jmp-over-else placeholder (entry_distance = 0 placeholder
    #    — patched at chain's final `}`).
    cglist.append(emit_jmp(0))
    jmp_slot = len(cglist) - 1

    # 2. Pop the previous frame; patch its cjmp's target so it jumps
    #    past the body AND past the jmp-over to the new body start.
    frame = ifstack.pop()
    cjmp_slot = frame.ListIndex
    entry_distance = (len(cglist) - 1) - cjmp_slot
    cglist[cjmp_slot] = cglist[cjmp_slot][:-4] + struct.pack("<i", entry_distance)

    # Carry forward the chain_jmps list, appending the new jmp-over.
    chain = list(frame.chain_jmps)
    chain.append(jmp_slot)

    # 3. Push the new frame.
    if kind == "else":
        else_frame = IfFrame(Lei="else", ListIndex=-1, chain_jmps=chain)
        ifstack.append(else_frame)
    elif kind == "else if":
        assert new_clauses is not None and resolve_atom_expr is not None
        assert cjmp_template_ref is not None
        # Emit the new condition's cjmps. They form a new frame on top
        # of the stack; we transplant the carried chain_jmps onto that
        # new frame so they all get patched together at the chain's
        # final `}`.
        emit_if_chain(new_clauses, cglist, ifstack, resolve_atom_expr,
                      cjmp_template_ref)
        # The new frame is now on top of ifstack — adopt the chain.
        ifstack[-1].chain_jmps = chain
    else:
        raise ValueError(f"unknown else kind {kind!r}")


def emit_label_marker(addr: int = 0) -> bytes:
    """Emit the `L <addr>` opcode that precedes every while/for loop.

    Wire format: ``"L " (0x4c 0x20) + 0x03 (long-int marker) + u32 LE
    addr`` — 7 bytes. The on-disk form has a 4-byte length prefix
    (``07 00 00 00``) prepended by the flatten layer.

    Function (resolved 2026-05-17 from IL inspection): pair-marker for
    suspend/resume of running event handlers. Each event handler emits
    a leading `S` marker; every `L` references its `S` via an
    arbitrary `biaoji` key set at compile time. At TFT-output time
    `appbianyi::Makestrsbytes` patches each L's `addr` field to point
    at the byte immediately following its paired S
    (``S.strdatapos + 4 + 7``).

    At runtime, `CodeRun_Run` (IL ~119010) reads L's `addr` ONLY when
    ``myappinf::RunHexPos != -1`` (i.e. resuming a suspended handler).
    Cold-start execution skips L entirely. So for any project that
    doesn't suspend/resume mid-handler (which is most of them), an
    ``addr = 0`` dummy is functionally correct.

    For byte-for-byte editor parity, callers would need to:
      1. Emit an ``S`` marker at the start of each event handler with
         a unique ``biaoji``.
      2. Track each ``L``'s biaoji and patch its address after the
         final byte layout is known.
    """
    return b"\x4c\x20\x03" + struct.pack("<I", addr)


# --- Self-test ------------------------------------------------------------

def _self_test() -> None:
    """Verify byte-for-byte against the 16_loop corpus."""
    # Simple resolver matching the 16.tft fixture's local-frame layout.
    # Component-attribute → local-frame offset map (extracted from the
    # observed bytecode in `tests/editor outputs/16_loop/16.tft`):
    # Extracted from 16.tft by reading the local-var-ref bytes off the
    # disassembled Timer event. NB: these offsets are PAGE-SPECIFIC and
    # only valid for the main page of 16_loop. Color globals here are
    # the page-Variable refs, which the source-text uses as `red.val`,
    # `blu.val`, etc.
    OFFSETS = {
        "x2.val":  0x8e, "x2.bco":  0x8a,
        "x5.val":  0xe9, "x5.bco":  0xe5,
        "x1.val":  0x109, "x1.bco": 0x105,
        "x6.val":  0x165, "x6.bco": 0x161,
        "x4.val":  0xe0,
        "x7.val":  0x17c, "x7.bco": 0x178,
        "x8.val":  0x1a5, "x8.bco": 0x1a1,
        "x0.val":  0x3c,  "x0.bco": 0x38,
        "x4.bco":  0xdc,
        "bco.val": 0x37b, "blu.val": 0x386, "red.val": 0x391,
        "wht.val": 0x39c, "yel.val": 0x3a7, "org.val": 0x3b2,
        "grn.val": 0x3bd,
    }
    GLOBALS = {"sys0": 0x00, "qq": 0x0c}

    def resolve(name: str) -> bytes:
        if name in OFFSETS:
            return b"\x01" + struct.pack("<I", OFFSETS[name])
        if name in GLOBALS:
            return b"\x05" + struct.pack("<I", GLOBALS[name])
        raise ValueError(f"unknown atom: {name}")

    # ---- Test 1: simple expression `sys0=x7.val-x4.val` ----
    expected = bytes.fromhex("05000000003d017c0100002d01e0000000")
    got = compile_expression("sys0=x7.val-x4.val", resolve)
    assert got == expected, f"expr1: got {got.hex()} expected {expected.hex()}"

    # ---- Test 2: simple expression `qq=qq+1` ----
    expected = bytes.fromhex("050c0000003d050c0000002b31")
    got = compile_expression("qq=qq+1", resolve)
    assert got == expected, f"expr2: got {got.hex()} expected {expected.hex()}"

    # ---- Test 3a: simple if/else, `if(x8.val>0) x8.bco=red.val else x8.bco=bco.val` ----
    # Expected bytes from 16.tft @ 0x70fad:
    #   12 00 00 00  09 00 04 01 a5 01 00 00 2c 30 2c 33 2c 03 1a 00 00 00
    #   0b 00 00 00  01 a1 01 00 00 3d 01 91 03 00 00
    #   07 00 00 00  54 20 03 0f 00 00 00
    #   0b 00 00 00  01 a1 01 00 00 3d 01 7b 03 00 00
    expected_flat = bytes.fromhex(
        "12000000" "09000401a50100002c302c332c031a000000"
        "0b000000" "01a10100003d0191030000"
        "07000000" "5420030f000000"
        "0b000000" "01a10100003d017b030000"
    )

    cglist: list[bytes] = []
    ifstack: list[IfFrame] = []
    cjmp_template_ref: list[bytes | None] = [None]

    # Simulate the GetbianyiCodes line-by-line dispatch for this snippet.
    # Line: "if(x8.val>0)"
    clauses = split_condition("x8.val>0")
    emit_if_chain(clauses, cglist, ifstack, resolve, cjmp_template_ref)
    # Line: "x8.bco=red.val"  (body)
    cglist.append(compile_expression("x8.bco=red.val", resolve))
    # Line: "}else"
    patch_close_with_else(cglist, ifstack, "else")
    # Line: "x8.bco=bco.val"  (else body)
    cglist.append(compile_expression("x8.bco=bco.val", resolve))
    # Line: "}"
    patch_close(cglist, ifstack, resolve)
    # Stage 2
    convert_entry_to_byte_distances(cglist, cjmp_template_ref[0])
    got_flat = flatten_cglist(cglist)
    assert got_flat == expected_flat, (
        f"if-else flatten mismatch:\n"
        f"  got      {got_flat.hex()}\n"
        f"  expected {expected_flat.hex()}"
    )

    # ---- Test 3b: if-elseif-elseif-elseif-else (4 elseif clauses) ----
    # The 16.tft fixture's `if(x0.val>2000)..else if..else if..else if..else`
    # chain @ TFT offset 0x70fec (the inner Timer event's x0 colour
    # selector). Verifies chain_jmps propagation through `else if`.
    OFFSETS["x0.val"] = 0x3c
    OFFSETS["x0.bco"] = 0x38
    OFFSETS["x8.bco"] = 0x1a1
    OFFSETS["bco.val"] = 0x37b
    OFFSETS["blu.val"] = 0x386
    OFFSETS["red.val"] = 0x391
    OFFSETS["yel.val"] = 0x3a7
    OFFSETS["org.val"] = 0x3b2
    OFFSETS["grn.val"] = 0x3bd
    with open(Path(__file__).resolve().parents[2] /
              "tests" / "editor outputs" / "16_loop" / "16.tft", "rb") as f:
        tft = f.read()
    expected_flat = tft[0x70fec:0x710cb]

    cglist = []
    ifstack = []
    cjmp_template_ref = [None]
    for cond_text, body_text in [
        ("x0.val>2000", "x0.bco=red.val"),
        ("x0.val>1700", "x0.bco=org.val"),
        ("x0.val>1550", "x0.bco=yel.val"),
        ("x0.val>1000", "x0.bco=grn.val"),
    ]:
        clauses = split_condition(cond_text)
        if not ifstack:
            emit_if_chain(clauses, cglist, ifstack, resolve, cjmp_template_ref)
        else:
            patch_close_with_else(cglist, ifstack, "else if",
                                  new_clauses=clauses,
                                  resolve_atom_expr=resolve,
                                  cjmp_template_ref=cjmp_template_ref)
        cglist.append(compile_expression(body_text, resolve))
    patch_close_with_else(cglist, ifstack, "else")
    cglist.append(compile_expression("x8.bco=bco.val", resolve))
    patch_close(cglist, ifstack, resolve)
    convert_entry_to_byte_distances(cglist, cjmp_template_ref[0])
    got_flat = flatten_cglist(cglist)
    assert got_flat == expected_flat, (
        f"if-elseif×4-else mismatch:\n"
        f"  got      {got_flat.hex()}\n"
        f"  expected {expected_flat.hex()}"
    )

    # ---- Test 4: || chain — `if(sys0>20||sys0<-20) { x4.bco=red.val x7.bco=red.val }` ----
    expected_flat = bytes.fromhex(
        "13000000" "09000405000000002c32302c342c03180000"  "00"
        "14000000" "09000405000000002c2d32302c322c031e0000" "00"
        "0b000000" "01dc0000003d01910300" "00"
        "0b000000" "0178010000" "3d01910300" "00"
    )
    # Need to also resolve x4.bco — let me add it
    OFFSETS["x4.bco"] = 0xdc
    OFFSETS["x7.bco"] = 0x178

    cglist = []
    ifstack = []
    cjmp_template_ref = [None]
    clauses = split_condition("sys0>20||sys0<-20")
    emit_if_chain(clauses, cglist, ifstack, resolve, cjmp_template_ref)
    cglist.append(compile_expression("x4.bco=red.val", resolve))
    cglist.append(compile_expression("x7.bco=red.val", resolve))
    patch_close(cglist, ifstack, resolve)
    convert_entry_to_byte_distances(cglist, cjmp_template_ref[0])
    got_flat = flatten_cglist(cglist)
    assert got_flat == expected_flat, (
        f"|| chain mismatch:\n"
        f"  got      {got_flat.hex()}\n"
        f"  expected {expected_flat.hex()}"
    )

    # ---- Test 5: while loop `while(qq<5){ qq=qq+1 }` ----
    expected_flat = bytes.fromhex(
        "12000000" "090004050c0000002c352c322c031c000000"
        "0d000000" "050c0000003d050c0000002b31"
        "07000000" "542003ceffffff"
    )
    cglist = []
    ifstack = []
    cjmp_template_ref = [None]
    # while-loop: record T <slot> for back-jmp; slot = current count (where cjmp will go)
    slot = len(cglist)
    frame = IfFrame(Lei="if", endstr=[f"T {slot}"])
    clauses = split_condition("qq<5")
    emit_if_chain(clauses, cglist, ifstack, resolve, cjmp_template_ref, frame_in=frame)
    # body
    cglist.append(compile_expression("qq=qq+1", resolve))
    patch_close(cglist, ifstack, resolve)
    convert_entry_to_byte_distances(cglist, cjmp_template_ref[0])
    got_flat = flatten_cglist(cglist)
    assert got_flat == expected_flat, (
        f"while mismatch:\n"
        f"  got      {got_flat.hex()}\n"
        f"  expected {expected_flat.hex()}"
    )

    print("script_compiler_extras self-test OK")


if __name__ == "__main__":
    _self_test()
