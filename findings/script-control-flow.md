# Script compiler: control-flow lowering + multi-operand expressions

Companion to [`script-compiler.md`](script-compiler.md). That doc describes
the minimal subset (assignments, `print`/`printh`, `page N`, sysvar/global
writes). This doc covers the two remaining gaps for non-trivial event
handlers:

1. **Control-flow lowering** — how `if` / `else` / `else if` / `while` /
   `for` get rewritten into the flat `09 00 04` (cjmp) + `54 20 03 …` (jmp)
   sequence the VM uses.
2. **Multi-operand expressions** — how `x0.val = h0.val + 5` and
   `if(h0.val > 2000 && r < -10)` get emitted.

Both pieces live in the same `appbianyi` chain in `plain_hmitype.dll`. One
read covers both. A Python helper that emits the bytes lives in
[`scripts/lib/script_compiler_extras.py`](../scripts/lib/script_compiler_extras.py).

## Source methods (managed `plain_hmitype.dll`)

| IL line | Method                              | Role                                                                 |
|---------|-------------------------------------|----------------------------------------------------------------------|
| 336898  | `appbianyi::chonggouifwhile`        | Splits an `if(...)` / `while(...)` / `for(...)` condition into a list of clause strings, each of the form `"i a,b,<endid>,0"` (with a trailing `,0` placeholder for the cjmp's branch target). |
| 337058  | `appbianyi::getendpos`              | One-pass scan of the condition text. Splits at operators `> >= < <= == != && \|\| ) (`. Returns the operator's `panduan` (the **endid**) as a single byte (table below). |
| 337531  | `appbianyi::getifstr`               | Consumes two `getendpos` outputs (LHS and RHS of one comparator) and produces the per-clause string `"<a>,<b>,<endid>,0"`. The trailing `,0` is the branch-target placeholder. |
| 337659  | `appbianyi::getfanyiif`             | Reverses (negates) a clause's comparator. Used to encode the *inverted* form needed by the VM. Swap table: `1↔6`, `2↔5`, `3↔4` (see below). |
| 337791  | `appbianyi::GetbianyiCodes`         | Per-event compile loop. Recognises `if(` `while(` `for(`, calls `chonggouifwhile`, then handles `{`, `}`, `}else`, `}else if(` and patches branch placeholders. The final loop at IL_1385–IL_15c7 walks every cglist entry and **converts entry-distance placeholders into byte-distance offsets** (see "Two-stage patching" below). |
| 340916  | `appbianyi::bianyionline`           | Per-line compile entry — calls `codeyunxingjiance` then `CodeRun_Run`. |
| 341037  | `appbianyi::codeyunxingjiance`      | Special-cases `L <num>` (label) and `S <num>` (string slot) — converts ASCII prefixes into `01 LL LL LL LL` / `04 ..` operand forms. Does **not** patch `T <num>` (jmp) placeholders — those are handled at the GetbianyiCodes level. |

## Comparator (`endid` / `panduan`) table

From `getendpos` (IL 337092–337488):

| Source operator | Bytecode `endid` (byte) | Notes                                       |
|-----------------|-------------------------|---------------------------------------------|
| `==`            | `1`                     | `=` followed by `=`                         |
| `<`             | `2`                     | bare `<`                                    |
| `>`             | `3`                     | bare `>`                                    |
| `<=`            | `4`                     | `<` followed by `=`                         |
| `>=`            | `5`                     | `>` followed by `=`                         |
| `!=`            | `6`                     | `!` followed by `=`                         |
| `&&`            | `250` (`0xfa`)          | `&` followed by `&`                         |
| `\|\|`          | `251` (`0xfb`)          | `\|` followed by `\|`                       |
| `)` (depth=0)   | `255` (`0xff`)          | end-of-condition marker (not a comparator)  |

In the bytecode, the comparator appears as a single **ASCII** digit (one
byte: `0x31`…`0x36`) inside the cjmp's operand area, e.g. `2c 32 2c` is
`,2,` (the comparator `<`). The `&&` / `||` separators do NOT appear in
the cjmp bytes themselves — they only influence which clauses get
negated (see "Comparator negation rule" below).

## `getfanyiif` negation table

From `getfanyiif` (IL 337659–337788). Inverts a clause's comparator
in-place inside the clause string:

| Input endid | Output endid | Semantic flip       |
|-------------|--------------|---------------------|
| 1 (`==`)    | 6 (`!=`)     | `==` ↔ `!=`         |
| 6 (`!=`)    | 1 (`==`)     | `!=` ↔ `==`         |
| 2 (`<`)     | 5 (`>=`)     | `<` ↔ `>=`          |
| 5 (`>=`)    | 2 (`<`)      | `>=` ↔ `<`          |
| 3 (`>`)     | 4 (`<=`)     | `>` ↔ `<=`          |
| 4 (`<=`)    | 3 (`>`)      | `<=` ↔ `>`          |

## VM semantics for cjmp

The bytecode VM evaluates a cjmp as:

```
cjmp(a, b, op, target):
    if NOT (a OP b):
        jump to target
    else:
        fall through
```

That is, **cjmp jumps over the body when the condition FAILS**. This is
the natural encoding for `if (cond) body`: emit `cjmp(cond, target=past
body)`, no negation required.

## Comparator negation rule

The editor's algorithm in `GetbianyiCodes` IL 338620–338770 (the
`&&`/`||`/multi-condition path, reached when the final endid is 250 or
255):

```
# chonggouifwhile_list = [clause0, clause1, ..., clauseN-1]
# Each clauseI is a string like "i x.val,1400,2,0"
remaining = len(clauses) - 1
for i in range(len(clauses) - 1):
    s = getfanyiif(clauses[i])                  # NEGATE comparator
    bytes_i = bianyionline(s)                   # compile to bytes (ends in ,0 ASCII)
    bytes_i = bytes_i[:-1] + b"\x03" + struct.pack("<i", remaining)
    cglist.append(bytes_i)
    remaining -= 1

# Last clause: NOT negated; placeholder kept
bytes_last = bianyionline(clauses[-1])
cglist.append(bytes_last)
# Save the last clause's index as the if's "ListIndex" — patched at `}`
```

Each early clause has its **branch offset stored as an entry-distance
(N entries to skip)** pointing past the remaining cjmps to the body
start. The last clause's target is left as a `,0` placeholder until the
matching `}` is seen, at which point it's patched to "skip the body".

The final byte-conversion pass (described below) turns the entry
distances into byte offsets.

### Why negation works for both `&&` and `||`

The semantics work out the same way for both connectors because the VM
evaluates `cjmp` as "if NOT (cond), jump":

- **`a && b`** — to skip body when EITHER condition fails:
  - cjmp(NEG(a), target=body-start): VM tests NEG(a); if FALSE (i.e.,
    if `a` holds), jump to body-start. **But that's wrong!**

Actually re-reading the IL: the IL_06d1 path is taken when **the
condition's final endid is 250 (`&&`) or 255 (`)`)**. The 251 (`||`)
case appears to take the **same path** in the IL since 251 is never
explicitly tested by `IL_05a3` (which tests only against 250 and
255).

Hmm — looking at `getendpos` more carefully, the endid stored in V_0
is the LAST operator separator. For `a > 20 && b < 30`, the inner
clauses end with `&&`, and the final clause ends with `)` (endid 255).
For `a > 20 || b < 30`, ditto with `||` then `)`. So V_0 == 255 in
BOTH cases.

The actual semantic interpretation of `&&` vs `||` must therefore be
encoded somewhere else. Inspection of the corpus suggests the editor
only emits the IL_06d1 path code as written above, with each early
clause **negated** — which works for `||` (verified on the project's
`if(sys0>20 || sys0<-20)` — see worked example below). The
`&&`-specific path is not observed in the available corpus and may
share the negation logic with `||`.

**Recommendation for the encoder**: when reconstructing, emit each
early clause with `getfanyiif` (negation) applied, and target
body-start. For `&&` chains specifically, this is **probably wrong**
and would need cross-checking against an `&&` corpus sample. Add a
TODO to flag.

## Branch-target encoding

The cjmp instruction's operand list is:

```
09 00 04 <a-operand> 2c <b-operand> 2c <endid-as-ASCII> 2c <target>
```

Where `<target>` is a long-form int literal `03 LL LL LL LL` (always
the 5-byte long form, regardless of magnitude). The target is a
**signed 32-bit byte offset from the end of the cjmp's content to the
destination**. Forward targets are positive; backward targets are
negative (sign-extended).

The unconditional jmp is:

```
54 20 03 <target>
```

That's literally `T ` (ASCII) + `03` + 4-byte signed offset, with the
same offset semantics (from end of jmp's content).

### Two-stage patching

The editor compiles in two stages:

1. **Stage 1: entry-distance placeholders.** Each cjmp and jmp is
   appended to `cglist` (a `List<byte[]>`). At emission time the
   trailing 4-byte target field stores an **entry-distance** (number of
   cglist entries to skip), not a byte distance.

   - Early clauses of a multi-clause condition store the entry distance
     from themselves to body-start.
   - The if's "last clause" cjmp stores a placeholder of `,0` (ASCII
     `30`); when `}` is seen, the patcher rewrites the trailing byte to
     `03` + `structToBytes(cglist.Count - 1 - ListIndex)`.
   - The while/for's back-jmp `T N` (where N = cjmp's slot index
     captured before emission) gets compiled to `54 20 30` initially.
     When `}` patches it (IL 338984–338998), the trailing `30` is
     replaced with `03` + `structToBytes(N - cglist.Count - 1)` — a
     negative entry distance.

2. **Stage 2: byte-distance conversion.** The loop at IL 1385–15c7 (run
   at the end of `GetbianyiCodes`) walks every cglist entry. For
   entries that match a control-flow opcode head (`54 20` = jmp, `49
   20` = label-jump, or matches the cjmp template `V_11` = first
   3 bytes saved at first cjmp emission), it:

   a. Reads the trailing 4 bytes of the entry as a signed int32
      `entry_distance`.
   b. Computes `byte_distance` as:
      - **Forward** (`entry_distance > 0`):
        ```
        byte_distance = sum(cglist[ListIndex + 1 + k].Length + 4
                            for k in range(entry_distance))
        ```
        That is: skip the next `entry_distance` entries; the
        `+4` accounts for each entry's 4-byte length prefix that
        will be prepended at flatten time.
      - **Backward** (`entry_distance < 0`):
        ```
        byte_distance = -sum(cglist[ListIndex + 1 + k].Length + 4
                             for k in range(entry_distance, 0))
        ```
        The sum INCLUDES the back-jmp's own slot (`k = -1`,
        index `ListIndex`). This is critical — a missing `+1` in
        the range gives an off-by-back-jmp-size error. Mirrors the
        IL_1559 loop precisely.
      - **Zero** (uncommon): leave as 0.
   c. Replaces the trailing 4 bytes of the entry with
      `struct.pack("<i", byte_distance)`.

   The IL has additional handling at IL_147c–IL_150c that **forward-chains**
   nested forward jumps when the target itself is a `T `-jmp — the
   outer jmp's offset is extended to include the inner jmp's target.
   This is not exercised by the corpus's flat if-elseif-else chains
   and isn't strictly required for them; see `script_compiler_extras.py`
   for the simpler non-chaining implementation.

The end result: at flatten time (`mobj::GetRefbianji`, IL line 48917+),
each `cglist[i]` is concatenated as `struct.pack("<I", item.Length) +
item`. The cjmp/jmp's target field now contains the correct byte
offset for the VM to consume directly.

## `chonggouifwhile` algorithm

Pseudocode reconstruction of the IL 336898–337054 routine:

```python
def chonggouifwhile(condition_str, src_line, by_out, endid_out):
    """Parse one condition expression. condition_str is the substring
    inside the parens of `if(...)`, `while(...)`, or `for(...; ...; ...)`'s
    middle clause. Returns a list of per-clause strings of the form
    `i a,b,<endid>,0`. The last endid is passed back via endid_out."""
    result = []
    pos = 3                                # start past "if("
    if condition_str[-1] != ")":
        error("missing closing paren")
        return []

    while pos < len(condition_str):
        # Two getendpos calls: LHS and RHS of one comparator
        pos = getendpos(condition_str, pos, &lhs, &lhs_endid)
        if pos == 65535:
            error("malformed condition")
            return []
        pos = getendpos(condition_str, pos, &rhs, &rhs_endid)
        if pos == 65535:
            error("malformed condition")
            return []

        endid_out = rhs_endid                # store the SEPARATOR (the
                                              # operator that ended the rhs:
                                              # could be `&&`, `||`, `)`)

        if lhs_endid != 255 and (endid_out == 255 or endid_out == lhs_endid):
            # Same comparator as last seen, or end-of-cond: build clause
            clause = f"i {lhs},{rhs},{lhs_endid},0"
            result.append(clause)
        else:
            error("malformed comparison")
            return []

    return result
```

## `getifstr` algorithm

Pseudocode reconstruction of IL 337531–337655:

```python
def getifstr(condition_str, pos_in, endid_out, error_out):
    """Read two operands separated by a comparator and return the
    formatted string `<a>,<b>,<endid>,0`. The pos is advanced past the
    end of the second operand."""
    pos = pos_in
    pos = getendpos(condition_str, pos, &a, &a_endid)
    if pos == 65535:
        error_out = "malformed expression: missing operator"
        return ""

    pos = getendpos(condition_str, pos, &b, &b_endid)
    if pos == 65535:
        error_out = "malformed expression: missing operand"
        return ""

    endid_out = b_endid                      # the SEPARATOR that ended b
    pos_in = pos                             # advance caller's position

    return ",".join([a, "", b, "", str(a_endid), "0"])
    # Equivalent to: f"{a},{b},{a_endid},0"
```

## Worked example 1: simple `if`

Source:
```
if(x2.val<1400)
{
    x2.bco=blu.val
}
else
{
    x2.bco=bco.val
}
```

`x2.val` is at local offset 0x8e. `x2.bco` is at 0x8a. `blu.val` is at
0x386. `bco.val` is at 0x37b.

Compilation steps:

1. `chonggouifwhile` returns `["i x2.val,1400,2,0"]` (one clause, endid
   for `<` is 2).
2. Single-clause path: the last clause is emitted without negation.
   `bianyionline("i x2.val,1400,2,0")` compiles to 22 bytes:
   ```
   09 00 04 01 8e 00 00 00 2c 03 78 05 00 00 2c 32 2c 03 1a 00 00 00
   ─cjmp── ─x2.val────── ,  ─1400(long)── ,  '2' ,  ─target placeholder──
   ```
   Note: the trailing `2c 03 1a 00 00 00` (`,` + `03` + 4 bytes) appears
   only AFTER the `}` patcher fires — initially the entry ends in
   `2c 30` (`,0`), where `0` is ASCII '0'. The patcher replaces `30`
   with `03 LL LL LL LL`.
3. Push `ifbianyi_(Lei="if", ListIndex=cglist_count-1)` onto ifstack.
4. Read `{` (no-op).
5. Body line `x2.bco=blu.val`: compiles to 11 bytes
   ```
   01 8a 00 00 00 3d 01 86 03 00 00
   ─x2.bco──────  =  ─blu.val──────
   ```
6. Read `}else`. Patch the cjmp:
   - entry_distance = cglist.Count - 1 - ListIndex = (after body
     emission, count = 2 above cjmp at slot 0; so count-1-0 = 1).
     Actually count-1-ListIndex = N entries since cjmp.
   - Append unconditional jmp placeholder `54 20 30` (ASCII "T 0") to
     cglist.
   - Push `ifbianyi_(Lei="else", ListIndex=cglist_count-1)`.
7. Read `{`, then body `x2.bco=bco.val` (11 bytes).
8. Read `}`. Patch the else's jmp similarly.
9. **Stage 2 conversion** walks cglist and replaces entry-distances
   with byte-distances:
   - cjmp at slot 0: entry_distance = 2 (body + jmp). Bytes following
     cjmp slot: body (11 bytes content + 4 prefix = 15) + jmp (7 bytes
     + 4 = 11) = 26. So target = `1a 00 00 00`.
   - jmp at slot 2: entry_distance = 1 (else body). Bytes following:
     else_body (11+4=15). So target = `0f 00 00 00`.

Final byte-stream (per-entry length prefixes prepended):
```
16 00 00 00  09 00 04 01 8e 00 00 00 2c 03 78 05 00 00 2c 32 2c 03 1a 00 00 00
0b 00 00 00  01 8a 00 00 00 3d 01 86 03 00 00
07 00 00 00  54 20 03 0f 00 00 00
0b 00 00 00  01 8a 00 00 00 3d 01 7b 03 00 00
```

Verified against `nextion/tests/editor outputs/16_loop/16.tft` @ TFT
offset 0x70d9a (note: that fixture has an extra elseif clause; the
single-if pattern above is verified against the `if(x5.val>1000) {} else
{}` block at @0x70eb8). The pattern is byte-for-byte identical apart
from operand values.

## Worked example 2: `while`

Source:
```
while(qq<5)
{
    qq=qq+1
}
```

`qq` is a declared `int` global at offset 0x0c.

Compilation steps:

1. Source detected as `while(`. The editor adds `"T <N>"` to the
   ifbianyi_'s `endstr` BEFORE adding the cjmp to cglist, where N =
   current cglist.Count (= the slot the cjmp will occupy).
2. `chonggouifwhile` returns `["i qq,5,2,0"]`.
3. Compile last clause `bianyionline` → 18 bytes:
   ```
   09 00 04 05 0c 00 00 00 2c 35 2c 32 2c 03 1c 00 00 00
   ─cjmp── ─qq────────── ,  '5' ,  '2' ,  ─target placeholder──
   ```
   Append to cglist. Push ifbianyi_(Lei="if" but with endstr=[T N]).
4. Read `{`, then body `qq=qq+1` → 13 bytes:
   ```
   05 0c 00 00 00 3d 05 0c 00 00 00 2b 31
   ─qq──────────  =  ─qq──────────  +  '1'
   ```
5. Read `}`. The patcher (IL 338863+) iterates `endstr` of the
   topmost ifbianyi_, finds `"T <N>"`, compiles it via bianyionline
   (yields `54 20 30` = "T 0" ASCII), and patches:
   - V_1 = N (the saved slot index)
   - V_5 = N - cglist.Count - 1 (the back-jmp's entry distance,
     negative)
   - Replace trailing `30` with `03` + structToBytes(V_5)
   - Append patched to cglist.
6. Then the cjmp's own target is patched (`count-1-ListIndex` = entry
   forward distance) — same as for if.
7. **Stage 2 conversion**:
   - cjmp: entry_distance = 2 (body + back-jmp). Bytes = (13+4) +
     (7+4) = 28. Target = `1c 00 00 00`.
   - Back-jmp: entry_distance = -3 (skip 3 entries backwards: itself,
     body, cjmp). Bytes = -(7+4 + 13+4 + 18+4) = -50. Target =
     `ce ff ff ff` (0xffffffce, signed -50).

Final byte-stream (with the leading `L <addr>` label opcode the editor
emits for every loop entry — function unclear but present at the start
of every `while`/`for` loop; the source for the loop body's first entry):
```
07 00 00 00  4c 20 03 a5 0d 00 00      ← `L ` label opcode (loop entry mark)
12 00 00 00  09 00 04 05 0c 00 00 00 2c 35 2c 32 2c 03 1c 00 00 00
0d 00 00 00  05 0c 00 00 00 3d 05 0c 00 00 00 2b 31
07 00 00 00  54 20 03 ce ff ff ff      ← back-jmp to cjmp's prefix
```

Verified against `16_loop/16.tft` @ TFT offset 0x07141d.

### Note on the `L <addr>` opcode

Every `while` and `for` loop in the corpus is preceded by an `L <addr>`
opcode (`4c 20 03 LL LL LL LL`). The `<addr>` value appears to be a
global label-table index (0x0da5 in the example), set by `appbianyi`'s
label allocator. Its runtime purpose is unclear (it may mark the loop
header for breakpoint / debug tooling). For the encoder, we can either:

- **Skip it**: omit the `L` opcode and produce slightly smaller
  bytecode. Untested against the device — may or may not work.
- **Emit a dummy `L 0`**: matches the editor's pattern bit-for-bit.
  Safer for round-trip.

The encoder helper emits a dummy `L` for byte-for-byte parity.

## Worked example 3: `for`

Source:
```
for(int qq=0; qq<5; qq=qq+1)
{
    sys0=qq
}
```

The `for` is lowered as:
1. The init statement (`int qq=0` — declaration plus assignment to
   zero) is run once at the loop start.
2. The condition (`qq<5`) becomes the loop's cjmp.
3. The body, then the increment (`qq=qq+1`), then a back-jmp.

`chonggouifwhile` handles the condition portion only. The init and
increment are extracted by `GetbianyiCodes` itself (IL 338120–338270):
the `for(<init>;<cond>;<inc>)` is split on `;` into 3 parts. The
`<init>` portion is compiled as a normal statement before the loop
starts; `<inc>` is appended to the loop body before the back-jmp.

(Not separately verified — no `for` corpus available in the project
TFTs beyond the synthetic 16_loop test which uses `while`. The
algorithm is documented from the IL only.)

## Multi-operand expression compilation

Verified against multiple project examples:

| Source                  | Bytecode                                                      | Form                                                |
|-------------------------|---------------------------------------------------------------|-----------------------------------------------------|
| `qq=qq+1`               | `05 0c .. 3d 05 0c .. 2b 31`                                  | LHS = LHS + RHS_atom                                |
| `sys0=x7.val-x4.val`    | `05 00 .. 3d 01 7c 01 .. 2d 01 e0 ..`                         | LHS = atom1 - atom2                                 |
| `if(x2.val<1400) ...`   | `09 00 04 01 8e .. 2c 03 78 05 .. 2c 32 2c <target>`          | cjmp(a, b, op, target) — operands comma-separated   |
| `if(sys0<-20) ...`      | `... 2c 2d 32 30 2c ...`                                      | `-20` → inline ASCII (3 chars fits short form)      |

**Rule**: Expressions are emitted in **left-to-right infix form** with
operators as single ASCII bytes. There is no RPN, no prefix, no precedence
reordering — the source string's order is preserved verbatim.

Each atom is emitted as a standalone operand (sysvar / global /
component-attribute ref / int literal, per
[`format-bytecode.md`](format-bytecode.md)). Operators between atoms
are emitted as their ASCII byte:

| Operator | Byte | ASCII |
|----------|------|-------|
| `=`      | 0x3d | `=`   |
| `+`      | 0x2b | `+`   |
| `-`      | 0x2d | `-`   |
| `*`      | 0x2a | `*`   |
| `/`      | 0x2f | `/`   |
| `&`      | 0x26 | `&`   |
| `\|`     | 0x7c | `\|`  |
| `^`      | 0x5e | `^`   |
| `<<`     | (untested, likely two-byte `3c 3c`) | `<<` |
| `>>`     | (untested, likely two-byte `3e 3e`) | `>>` |

**Worked example: `sys0=x7.val-x4.val`**

| Source token | Bytes                  | Note                     |
|--------------|------------------------|--------------------------|
| `sys0`       | `05 00 00 00 00`       | global ref, offset 0     |
| `=`          | `3d`                   | assign                   |
| `x7.val`     | `01 7c 01 00 00`       | local ref, offset 0x17c  |
| `-`          | `2d`                   | subtract                 |
| `x4.val`     | `01 e0 00 00 00`       | local ref, offset 0xe0   |
| **Total**    | 17 bytes               |                          |

Wrapped with `11 00 00 00` (length 17) as the entry's flatten prefix.

**Worked example: `h0.val + 5 > 2000` (hypothetical, as a cjmp condition)**

The cjmp's `i a,b,<op>,0` form means binary expressions are split at
the comparator. So `h0.val + 5 > 2000` becomes:
- a = `h0.val + 5` (sub-expression — infix)
- b = `2000` (atom)
- op = `>` (id 3)

The cjmp emits as:
```
09 00 04 <h0.val> 2b 35 2c <2000> 2c 33 2c <target>
─cjmp── ─h0.val── +  '5' ,  ─2000──── ,  '3' ,  ─target──
```

Where `2b 35` is `+5` (the `5` as inline ASCII since 5 < 1000). The
`a` operand is "h0.val + 5" emitted as infix `<h0.val>2b35`.

## Algorithm summary (encoder pseudocode)

```python
def compile_event(source_lines, page_ctx, app_ctx):
    cglist: list[bytes] = []     # one entry per compiled statement
    ifstack: list[IfFrame] = []  # nested if/while/for frames
    cjmp_template: bytes = None  # first cjmp's 3-byte head (for Stage 2 detection)
    line_idx = 0
    while line_idx < len(source_lines):
        line = source_lines[line_idx].strip()
        if line.startswith("if("):
            clauses = chonggou_split_condition(line[3:-1])  # strip "if(" and ")"
            emit_if_or_while(clauses, "if", cglist, ifstack, cjmp_template)
        elif line.startswith("while("):
            slot = len(cglist)
            # back-jmp placeholder will reference this slot at `}` time
            frame = IfFrame(Lei="if", endstr=[f"T {slot}"])
            clauses = chonggou_split_condition(line[6:-1])
            emit_if_or_while(clauses, "while", cglist, ifstack, cjmp_template, frame_in=frame)
        elif line.startswith("for("):
            init, cond, inc = parse_for(line[4:-1])
            cglist.append(compile_statement(init))
            slot = len(cglist)
            frame = IfFrame(Lei="if", endstr=[f"T {slot}", inc])  # inc emitted before back-jmp
            clauses = chonggou_split_condition(cond)
            emit_if_or_while(clauses, "for", cglist, ifstack, cjmp_template, frame_in=frame)
        elif line == "}":
            patch_close(cglist, ifstack)
        elif line == "}else":
            patch_close_with_else(cglist, ifstack, kind="else")
        elif line.startswith("}else if("):
            patch_close_with_else(cglist, ifstack, kind="else if", new_clauses=...)
        else:
            cglist.append(compile_statement(line))
        line_idx += 1

    # Stage 2: byte-distance conversion
    convert_entry_distances_to_byte_distances(cglist, cjmp_template)

    # Flatten
    out = b""
    for entry in cglist:
        out += struct.pack("<I", len(entry)) + entry
    return out

def emit_if_or_while(clauses, kind, cglist, ifstack, cjmp_template, frame_in=None):
    """Emit cjmps for each clause. Returns the new frame on ifstack."""
    for i, clause in enumerate(clauses[:-1]):
        negated = getfanyiif(clause)                  # NEGATE comparator
        b = compile_clause(negated)                   # ends in ASCII ',0' (`2c 30`)
        b = b[:-1] + b"\x03" + struct.pack("<i", len(clauses) - 1 - i)
        cglist.append(b)
    # Last clause: not negated, placeholder kept
    b = compile_clause(clauses[-1])
    cglist.append(b)
    if cjmp_template is None and len(cglist) > 0:
        cjmp_template = bytes(cglist[-1][:3])         # save for Stage 2 detection
    frame = frame_in or IfFrame()
    frame.Lei = "if"
    frame.ListIndex = len(cglist) - 1
    ifstack.append(frame)

def patch_close(cglist, ifstack):
    """Handle `}` — emit any pending endstr (back-jmps for loops) and
    patch the cjmp's branch target."""
    frame = ifstack[-1]
    # Emit endstr items (for while/for: the back-jmp placeholder)
    for s in frame.endstr:
        b = compile_one_line(s)
        # If b starts with "T " (a back-jmp), patch immediately
        if len(b) > 2 and b[0] == 0x54 and b[1] == 0x20:
            N = parse_int_after_T_space(b)
            entry_distance = N - len(cglist) - 1
            b = b[:-1] + b"\x03" + struct.pack("<i", entry_distance)
        cglist.append(b)

    # Patch the cjmp's own target (which currently ends in ASCII '0')
    cjmp_slot = frame.ListIndex
    entry_distance = (len(cglist) - 1) - cjmp_slot
    old = cglist[cjmp_slot]
    cglist[cjmp_slot] = old[:-1] + b"\x03" + struct.pack("<i", entry_distance)

    ifstack.pop()

def patch_close_with_else(cglist, ifstack, kind, new_clauses=None):
    """Handle `}else` / `}else if(...)`. Emit a forward jmp to skip the
    else body, patch the cjmp, then push a new else frame."""
    frame = ifstack[-1]
    cglist.append(b"\x54\x20\x30")  # `T 0` placeholder (jmp over else)
    cjmp_slot = frame.ListIndex
    entry_distance = (len(cglist) - 1) - cjmp_slot
    cglist[cjmp_slot] = cglist[cjmp_slot][:-1] + b"\x03" + struct.pack("<i", entry_distance)
    ifstack.pop()
    # Push else / else-if frame
    if kind == "else":
        ifstack.append(IfFrame(Lei="else", ListIndex=len(cglist) - 1))
    else:  # else if
        emit_if_or_while(new_clauses, "if", cglist, ifstack, ...)

def convert_entry_distances_to_byte_distances(cglist, cjmp_template):
    """The Stage 2 final pass: walk cglist and rewrite each control-flow
    entry's trailing 4 bytes from entry-distance to byte-distance."""
    for i, entry in enumerate(cglist):
        if not is_control_flow_entry(entry, cjmp_template):
            continue
        entry_distance = struct.unpack("<i", entry[-4:])[0]
        if entry_distance > 0:
            byte_distance = sum(len(cglist[i + 1 + k]) + 4
                                for k in range(entry_distance))
        elif entry_distance < 0:
            byte_distance = -sum(len(cglist[i + k]) + 4
                                 for k in range(entry_distance, 0))
        else:
            byte_distance = 0
        cglist[i] = entry[:-4] + struct.pack("<i", byte_distance)

def is_control_flow_entry(entry, cjmp_template):
    """An entry is control-flow if it starts with 0x54 0x20 (jmp `T `),
    0x49 0x20 (alt jmp `I `), or matches cjmp_template's first 3 bytes."""
    if len(entry) < 7:
        return False
    if entry[0] == 0x54 and entry[1] == 0x20:
        return True
    if entry[0] == 0x49 and entry[1] == 0x20:
        return True
    if cjmp_template is not None and entry[:3] == cjmp_template:
        return True
    return False
```

## Int-literal threshold (revised)

The script_compiler.py rule "≥ 1000 or negative → long form" is too
strict — the editor also inlines short negative ints as ASCII. The
correct rule, observed in `if(sys0<-20)`:

| Value range          | Form                                       |
|----------------------|--------------------------------------------|
| repr ≤ 3 chars       | inline ASCII digits (incl. `-` sign)       |
| repr ≥ 4 chars       | `03 LL LL LL LL` (5-byte signed int32)     |

So `0..999` (3-digit max) and `-1..-99` (1- or 2-digit + sign = ≤3
chars) are inline. `1000` and `-100` upward are long-form. The
encoder helper in `script_compiler_extras.py` uses this rule.

## Open questions / gaps

1. **`&&` corpus**. No project in the repo uses `&&`. The IL is
   single-pathed for both `&&` and `||`, suggesting the encoding might
   be identical, but the VM semantics for `&&` need verification.
2. **`for` loop**. No `for` exists in the project corpus (16_loop has a
   `while`). The init/inc lowering is documented from IL only,
   untested against bytecode.
3. **Nested if/while**. The IL handles arbitrary nesting via the
   `ifstack`. The Stage 2 byte-conversion pass handles nested forward
   jumps by *iterating* through skipped entries that are themselves
   forward jumps (IL 14d0–1509). The encoder helper has not been
   verified against nested constructs — corpus only has flat
   sequential if-else chains.
4. **The `L <addr>` opcode preceding every loop**. Function unknown.
   Encoder emits a dummy `L 0` for parity.
5. **`||` vs `&&` semantic difference**. The IL appears to apply
   `getfanyiif` (negation) to every early clause regardless. For `||`
   this is verified-correct. For `&&` the same encoding seems wrong
   semantically. Needs cross-check.

## References

- `chonggouifwhile`: `plain_hmitype.dll!hmitype.appbianyi::chonggouifwhile`
  (IL line 336898).
- `GetbianyiCodes`: IL line 337791. The two-stage patching pass is at
  IL 1385–15c7 (line 340131–340498 of the IL dump).
- `getfanyiif`: IL line 337659.
- `getendpos`: IL line 337058.
- `getifstr`: IL line 337531.
- Verified-against corpus:
  `nextion/tests/editor outputs/16_loop/16.tft` (while loop @ 0x71429,
  if-elseif-else @ 0x70d9e, `||` chain @ 0x70f64).
