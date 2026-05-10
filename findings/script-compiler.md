# Event-handler script compiler

The Nextion editor turns each event-handler script (`codesup`,
`codesdown`, `codestimer`, `codesload`, `codesunload`, `codesslide`,
and the global `Program.s`) from plain-text Nextion script into a
length-prefixed bytecode block in the TFT usercode region.
[`format-bytecode.md`](format-bytecode.md) documents the bytecode
encoding; this file documents the **compiler** that produces it.

A minimal but byte-for-byte verified Python re-implementation lives
in [`scripts/script_compiler.py`](../scripts/script_compiler.py).

## Where the editor's compiler lives

Confirmed by disassembling `plain_hmitype.dll` (decompiled via
`MONO_PATH=/tmp/all_dlls/ monodis` after the unpacking pipeline from
[`achmi-internals.md`](achmi-internals.md) leaves it on disk).

| Component                | Location                                          | Role                                                  |
|--------------------------|---------------------------------------------------|-------------------------------------------------------|
| `hmitype.appbianyi`      | static helper class (327k+ in IL dump)            | Outer compile path: split lines, route control-flow   |
| `appbianyi::FileBianyi`  | line 327706 of IL                                 | Entry point — called by `HMIFORM.output` UI           |
| `appbianyi::BianyiApp`   | line 329028                                       | Per-app compile driver                                |
| `appbianyi::GetbianyiCodes` | line 337791                                    | Per-event compiler: scan lines, handle `int`/`if`/`while`/`for` |
| `appbianyi::chonggouifwhile` | line 336897                                  | Rewrite control-flow constructs to `i` (cjmp) + jmp   |
| `appbianyi::bianyionline`   | line 340915                                    | Per-line entry: stateful setup + delegate to compiler |
| `appbianyi::codeyunxingjiance` | line 341037                                 | Buffer prep: copy source, dispatch, handle `L/S/T/add` prefixes |
| `GuiCombianyi::CodeRun_Run` | line 308174 of IL (class at 302847)           | **The bytecode emitter** — single-statement compiler  |
| `Tcode.dll`              | `ICSharpCode.TextEditor` syntax highlighter (red herring) | NOT the compiler. Confused naming.            |

Despite the file name, `Tcode.dll` (decoded from
`plain_Tcode.dll`) is **not** the compiler — it's the embedded
ICSharpCode SyntaxEditor used by the editor's code panes for
syntax highlighting only. Sources in `ICSharpCode.TextEditor.*`
namespaces. The compiler is entirely in `plain_hmitype.dll`.

### `appbianyi::FileBianyi` (entry point)

Signature:

```
bool FileBianyi(Myapp_inf Myapp_, string binpath,
                RichTextBox text, CompileMode CompileMode1)
```

`CompileMode` is a 2-value enum: `RunFile = 0`, `OutPutTFT = 1`.
This is the routine `HMIFORM.output` calls when the user clicks
**Compile** in the UI.

### `appbianyi::GetbianyiCodes` (per-event compiler)

```
bool GetbianyiCodes(mobj myobj, List<string> bts,
                    out List<byte[]> cglist, _eventtype ev,
                    int dx, bool makestring,
                    Encoding en, Myapp_inf Myappinf0)
```

`bts` is the list of *source lines* for one event handler; `cglist`
is the output list of per-line bytecode arrays (each later joined
to form the full block).

Inside this method the IL shows direct string compares against:
- `"timerset "` — passed through directly
- `"int "` — variable declaration
- `"if("`, `"while("`, `"for("` — control-flow start
- `"w"` / `"f"` — first-character dispatch into while/for paths

Control flow is rewritten in `chonggouifwhile`: the editor expands
`if (...) {...} else {...}` into an `i` (`cjmp`) opcode with a
branch offset, followed by the `then` body, an unconditional `jmp`,
and the `else` body. Branch targets are patched after both arms
have been compiled (so their byte lengths are known).

For ordinary single-line statements, `GetbianyiCodes` calls
`bianyionline`, which sets up state and calls `codeyunxingjiance`.

### `codeyunxingjiance` (line-to-buffer dispatcher)

```
byte codeyunxingjiance(mobj obj, byte[] surcode,
                        out byte[] descode, bool makestring,
                        Myapp_inf Myappinf0)
```

This wraps the actual native compiler. It:

1. Copies `surcode` (raw ASCII bytes of one source statement) into
   `descode`.
2. Calls `GuiCombianyi::CodeRun_Run(buf, pos, mobj)` to compile
   the buffer in place. The native helper modifies `descode` to
   contain the bytecode for that statement.
3. **Special-cases three textual prefixes** that arise from
   compiler-internal lowering (not user source):
   - `T <num>` — branch target placeholder (resolved at link time)
   - `L <num>` — `goto` label literal
   - `S <num>` — string-literal slot reference
   - `add <args>` — runtime add-component call

   Each is converted into the corresponding `01 LL LL LL LL`
   (local) or `09 NN SS` (opcode) form.

### `GuiCombianyi::CodeRun_Run` (the emitter)

```
byte CodeRun_Run(byte* buf, PosLaction* poscode, mobj mobj)
```

This is the actual statement compiler. It:

1. Reads the first byte at `buf[poscode.star]`:
   - If `0x09`, the buffer already contains a partial opcode — keep
     going past the opcode header.
   - Otherwise, treat the buffer as ASCII source.
2. Calls helper parsers for various sub-forms:
   - `Getpageid(buf, pos)` — `page N` operand
   - `Getobjid(buf, pos, page)` — component name (`h0`, `x0`,
     `t0`, etc.)
   - `Getatt(buf, pos, runattinf*)` — attribute reference
     (`.val`, `.bco`, `.txt`, etc.)
   - `Getsysname(buf, pos, runattinf*)` — system var (`dim`,
     `baud`, …)
   - `GetAllvasIntname(buf, pos, runattinf*)` — user-declared
     global int
   - `strgetS32(buf, pos, back)` — integer literal
   - `strgetu32(...)` — unsigned 32-bit literal
3. Emits the result into the buffer in place. Errors set
   `GuiCombianyi::errcode` + `errmessage`.

This native-style entry point (its arguments are raw pointers and
it has 4026 bytes of code) is the closest thing to a single
"emit one statement" function.

## Algorithm (pseudo-code reconstruction)

```python
def compile_event(source_lines, page_ctx, app_ctx) -> bytes:
    out = bytearray()
    for line in source_lines:
        line = strip_comment(line)
        if line.startswith("if(")    : out += compile_if_else_chain(...)
        elif line.startswith("while("): out += compile_while_loop(...)
        elif line.startswith("for(") : out += compile_for_loop(...)
        elif line.startswith("int ") : declare_global_int(line, app_ctx)
        else:
            for stmt in line.split(";"):
                out += compile_one_stmt(stmt, page_ctx, app_ctx)
    return out


def compile_one_stmt(stmt, page_ctx, app_ctx) -> bytes:
    # Recognise known opcode mnemonics
    head, _, rest = stmt.partition(" ")
    if head in OPCODE_TABLE:
        size_cls, idx = OPCODE_TABLE[head]
        return bytes([0x09, idx, size_cls]) + emit_args(rest, ...)

    # Otherwise it's an assignment / expression
    lhs, op, rhs = split_assignment(stmt)
    lhs_bytes = resolve_lvalue(lhs, page_ctx, app_ctx)
    rhs_bytes = compile_expr(rhs, page_ctx, app_ctx)
    return lhs_bytes + op.encode("ascii") + rhs_bytes


def resolve_lvalue(name, page_ctx, app_ctx):
    if "." in name:                          # h0.val, t0.txt
        comp, attr = name.split(".", 1)
        offset = page_ctx.component_attr_offset(comp, attr)
        return bytes([0x01]) + u32_le(offset)
    if name in SYSTEM_VARS:
        sz, idx = SYSTEM_VARS[name]
        return bytes([0x04, sz, idx & 0xff, (idx >> 8) & 0xff, (idx >> 16) & 0xff])
    if name in app_ctx.globals:
        return bytes([0x05]) + u32_le(app_ctx.globals[name])
    raise CompileError(name)


def compile_expr(s, page_ctx, app_ctx):
    # Tokenise into operators (+, -, *, /, &, |, ^, <<, >>) and atoms
    # Atoms: int literals, lvalues, parenthesised sub-exprs
    # Editor heuristic: integers fitting in 3 decimal digits are emitted as
    # ASCII bytes; integers >= 1000 use the `03 LL LL LL LL` operand form.
    ...
```

## Identifier resolution tables

### System variables (size_class, index)

Pulled from `nxt-1.65.1 / model 100` in
[`tools/TFTTool/NextionInstructionSets.py`](../tools/TFTTool/NextionInstructionSets.py)
(F-series uses the same table on 1.67.1). The size_class
determines the inline encoding (size=4 sysvars take 4 bytes
internally; size=8 sysvars take 8 bytes).

| Size | Index | Name      | Notes                              |
|------|-------|-----------|------------------------------------|
| 4    | 0x00  | `dp`      | display page                       |
| 4    | 0x01  | `RED`     | colour constant                    |
| 4    | 0x02  | `thc`     |                                    |
| 4    | 0x03  | `dim`     | brightness (0–100)                 |
| 4    | 0x04  | `wup`     | wake-up page                       |
| 4    | 0x05  | `sya0`    | editor-internal scratch            |
| 4    | 0x06  | `tch0`    |                                    |
| 4    | 0x07  | `sya1`    |                                    |
| 4    | 0x08  | `tch1`    |                                    |
| 4    | 0x09  | `tch2`    |                                    |
| 4    | 0x0a  | `tch3`    |                                    |
| 4    | 0x0b  | `BLUE`    |                                    |
| 4    | 0x0c  | `GRAY`    |                                    |
| 4    | 0x0d  | `rand`    |                                    |
| 4    | 0x0e  | `baud`    | UART baud                          |
| 4    | 0x0f  | `thsp`    |                                    |
| 4    | 0x10  | `ussp`    |                                    |
| 4    | 0x11  | `thup`    |                                    |
| 4    | 0x12  | `usup`    |                                    |
| 4    | 0x13  | `addr`    |                                    |
| 4    | 0x14  | `dims`    | brightness scale mirror            |
| 4    | 0x15  | `bcpu`    | CPU load %                         |
| 8    | 0x05  | `appid`   |                                    |
| 8    | 0x06  | `bkcmd`   |                                    |
| 8    | 0x0d  | `recmod`  | UART recv mode                     |

### Opcode mnemonics (size_class, index)

The per-event opcodes use `09 NN SS` where `SS ∈ {4, 8}`. Full
table for nxt-1.67.1 / model 100 in
[`NextionInstructionSets.py`](../tools/TFTTool/NextionInstructionSets.py).
Most-used in practice (size 4): `page = (4, 0x0b)`,
`print` would be size-8.

For full coverage of every entry, the table in `NextionInstructionSets.py`
is what `script_compiler.py` consumes directly via `_build_lookup`.

## Integer-literal encoding rule

Observed in real TFT output:

| Source value     | Bytecode                       | Bytes |
|------------------|--------------------------------|-------|
| `0`              | `30` (ASCII `0`)               | 1     |
| `42`             | `34 32`                        | 2     |
| `100`, `480`     | ASCII digits                   | 3     |
| `999`            | ASCII digits *(boundary)*      | 3     |
| `1000` and up    | `03 e8 03 00 00`               | 5     |
| `115200`         | `03 00 c2 01 00`               | 5     |
| negative numbers | `03 LL LL LL LL` (signed)      | 5     |

`script_compiler.py` uses `value < 0 or value >= 1000 → long form`,
which round-trips every observed case in the miata-dash corpus.

## What the minimal compiler implements

In [`scripts/script_compiler.py`](../scripts/script_compiler.py),
the following round-trip byte-for-byte against the project's actual
TFT blocks:

| Source                                       | Verified bytecode                                                                  |
|----------------------------------------------|------------------------------------------------------------------------------------|
| `int sys0=0,sys1=0,sys2=0`                   | (empty — allocates global slots only)                                              |
| `baud=115200`                                | `04 04 0e 00 00 3d 03 00 c2 01 00`                                                  |
| `recmod=0`                                   | `04 08 0d 00 00 3d 30`                                                              |
| `printh 00 00 00 ff ff ff 88 ff ff ff`       | `09 0b 08 30 30 20 30 30 20 30 30 20 66 66 20 66 66 20 66 66 20 38 38 20 66 66 20 66 66 20 66 66` |
| `page 0`                                     | `09 0b 04 30`                                                                       |
| `page 1` / `page 2`                          | `09 0b 04 31` / `09 0b 04 32`                                                       |
| `print "update"`                             | `09 04 08 22 75 70 64 61 74 65 22`                                                  |
| `sys2=42` (when `sys2` is a declared int)    | `05 08 00 00 00 3d 34 32`                                                           |

All eight cases compile exactly, including the test-10 fixture
`sys2=42` that was specifically created as a bytecode probe.

## What the minimal compiler does NOT implement

Each gap is a known, scoped piece of additional work:

1. **Component-attribute access** (`h0.val`, `x0.bco=red.val`,
   `t0.txt="hello"`). The compiler would need a
   `(component_name, attribute_name) → local_var_offset` table for
   every component on the current page. The editor builds this
   table in `GuiCombianyi.Getobjid` + `Getatt` by walking the page's
   `objdata_Ram` and `PianyiData` records and looking up the
   attribute index in the 82-entry `xilie.AppAttNames` table
   (`hmitype.dll`).

   Confirmed example from the miata-dash TFT: `dim=h0.val`
   compiles to `04 04 03 00 00 3d 01 54 04 00 00` — the `01 54 04
   00 00` is a local-var ref at offset `0x454`, the page-frame
   address for slider `h0`'s `val` attribute. A complete compiler
   would compute that offset from the page layout.

2. **Control flow** (`if (cond) {...} else {...}`, `while (...)`,
   `for (...; ...; ...)`). The IL of
   `appbianyi::chonggouifwhile` shows the rewrite: each construct
   is expanded into a string list of "intermediate" lines like
   `T 5` (branch target placeholder) and `i op_a,op_b,comp,T 5`
   (`cjmp` with target ref). After every body is compiled, the
   placeholders are resolved to byte offsets.

3. **Multi-operand expressions** beyond `lvalue = atom`. The
   editor compiles arithmetic and bit-ops left-to-right with
   ASCII operator bytes between operands. Adding a real expression
   parser is the next obvious incremental step.

4. **String literals as `txt`-assignment values** — `t0.txt="..."`
   needs both component-attribute resolution (gap 1) and a
   small string-table writer.

5. **Opcodes with structured arguments** — `timerset`, `xstr`,
   `qrcode`, etc. each take a specific argument schema. Most are
   simple comma-separated ASCII forms; some emit `01 LL ..`
   operands for attribute refs.

## Verification

Run the compiler's self-test:

```
cd nextion/scripts
PYTHONPATH=../tools/TFTTool python3 script_compiler.py
```

Expected output: `script_compiler self-test OK`.

To verify against the project TFT directly:

```
cd nextion
PYTHONPATH=tools/TFTTool:scripts python3 -c "
from script_compiler import CompileContext, compile_handler
ctx = CompileContext()
compile_handler('int sys0=0,sys1=0,sys2=0', ctx)
for src, expected in [
    ('baud=115200', '04040e00003d0300c20100'),
    ('recmod=0',     '04080d00003d30'),
    ('page 0',       '090b0430'),
]:
    got = compile_handler(src, ctx).hex()
    print(('OK' if got == expected else 'FAIL'), src, got)
"
```

Eight of eight known source/bytecode pairs round-trip exactly.

## Concrete next leads (for the next iteration)

- **Per-page component-attribute table.** Build it from the
  HMI-side records or from the TFT's `objdata_Ram` + `PianyiData`
  per page. Once available, swap `_emit_value`'s "unknown
  identifier" error path for a component-attribute resolver.
  The 82-entry `xilie.AppAttNames` in `hmitype.dll` is the
  attribute-name → attribute-index table; what's missing is the
  per-component PianyiData offset.

- **Control-flow lowering.** Implement
  `chonggouifwhile`-equivalent logic in Python:
  1. Parse `if (cond) { stmts } [ else if (...) {} ... ] [ else
     {} ]` into a chain.
  2. For each clause, emit `09 00 04` (`i`/cjmp), then the
     condition operand-by-operand, then `2c`, then a 4-byte
     placeholder, then the body.
  3. Insert `54 20 03` (`jmp`) at the end of each `then` block
     pointing past the `else`.
  4. Patch placeholders with the resolved byte offsets after
     compilation.

  The `i` opcode's operand schema is documented in
  [`format-bytecode.md`](format-bytecode.md#control-flow); the
  branch target is a signed byte offset from the end of the
  current `i`/`jmp` opcode.

- **Expression parser.** Replace `_emit_value`'s "single atom"
  restriction with a Pratt parser handling the operator
  precedence table from `numerated_operators`. Each token in the
  output is concatenated; binary operators emit a single ASCII
  byte between operands.

- **Native `CodeRun_Run` cross-check.** A more thorough validation
  would dump every event-handler bytecode from a known-good TFT,
  pair it with its source from `Nextion2Text`, and confirm
  round-tripping. The matching script in
  [`scripts/script_compiler.py`](../scripts/script_compiler.py)'s
  self-test does this for `Program.s`; expanding the corpus is
  cheap.
