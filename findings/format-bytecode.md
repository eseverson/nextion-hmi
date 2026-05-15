# TFT bytecode

The Nextion VM's instruction stream lives in size-prefixed blocks
inside the TFT body. Two block families are observed:

1. **Per-component init blocks** — one per visible component, addressed
   via the component's `PianyiData[+0x34]` u32 (an offset into the
   `strdataaddr` region). Sets the component's runtime attributes.
2. **Per-event script blocks** — `codesload` / `codesloadend` /
   `codesunload` / `codesup` / `codesdown` / `codestimer` /
   `codesslide` handlers, plus the global `Program.s`. Free-form
   Nextion bytecode.

Reference disassembler: [`scripts/lib/tft_bytecode.py`](../scripts/lib/tft_bytecode.py)
handles the per-component init format. Per-event bytecode is decoded
through the existing instruction-set tables in
[`tools/TFTTool/NextionInstructionSets.py`](../tools/TFTTool/NextionInstructionSets.py).

## Block framing

Every block starts with a 4-byte little-endian length prefix giving
the size of the rest of the block (excluding the prefix itself):

```
+0x00   u32   length
+0x04   bytes payload[length]
```

For the global memory directory (the first block in the usercode
region) the prefix doubles as the directory size in bytes.

## Per-event bytecode encoding

| Byte             | Form               | Meaning                          |
|------------------|--------------------|----------------------------------|
| `09 NN SS`       | opcode             | `NN` = index, `SS` = operand size class (4 or 8) |
| `01 LL LL LL LL` | local var ref      | 32-bit local-frame address       |
| `05 LL LL LL LL` | global var ref     | 32-bit global address            |
| `04 VV VV VV VV` | system var ref     | low byte = size class, high 24 bits = index |
| `03 LL LL LL LL` | int literal (long) | 32-bit signed                    |
| `2c`             | `,`                | argument separator               |
| `+ - * / = …`    | operator           | ASCII byte                       |
| `"…"`            | string literal     | `\` escapes                      |
| `54 20 …`        | `jmp`              | unconditional jump (op = 0x2054) |

**Short literals** appear as raw ASCII in compact contexts (loop
conditions, `page <n>` operands): the digit `5` is byte `0x35`, not a
4-byte int literal. Decimal digits and short flags use this form;
longer values use the `03 ..` long-int form.

The `(size_class, index)` → mnemonic mapping is editor-version and
model-series dependent; `nxt-1.67.1` shares the model-100 (T1)
instruction set with `nxt-1.65.1`.

## Confirmed opcodes (size 4)

| index | mnemonic   | notes                                        |
|-------|------------|----------------------------------------------|
| `0x00`| `i` (cjmp) | conditional jump                             |
| `0x03`| `ref`      | reference                                    |
| `0x0b`| `page`     | page switch                                  |
| `0x0d`| `fill`     | rectangle fill (`fill x,y,w,h,colour`)       |
| `0x14`| `xstr`     | render a string                              |
| `0x17`| `addt`     | XFloat / draw3d helper                       |
| `0x1d`| `draw`     | draw                                         |

## Confirmed opcodes (size 8)

| index | mnemonic   | notes                                        |
|-------|------------|----------------------------------------------|
| `0x04`| `print`    | print to serial                              |
| `0x07`| `draw3d`   | 3D-bevel rectangle                           |
| `0x0b`| `printh`   | hex-bytes print                              |
| `0x1c`| `setbrush` | set fill brush                               |
| `0x22`| `doevents` | yield to event loop                          |
| `0x23`| `timerset` | rearm a timer                                |

## Confirmed system variables

| size | index | name    | notes                                  |
|------|-------|---------|----------------------------------------|
| 4    | `0x03`| `dim`   | brightness                             |
| 4    | `0x05`| `sya0`  | editor-injected scratch                |
| 4    | `0x0e`| `baud`  | UART baud                              |
| 4    | `0x14`| `dims`  | brightness scale (mirror of `dim`)     |
| 8    | `0x0d`| `recmod`| record mode                            |

## Control flow

Conditional jump (`if` / loop condition):

```
09 00 04     i / cjmp opcode (size 4, num 0)
<expr>       operand expression
2c           ','
<target>     branch target (offset)
```

Unconditional jump (`while` / `for` loop back-edges, `goto`):

```
54 20 03     jmp opcode (0x2054, size 8 alternative encoding)
<target>     branch target
```

Loops compile as a contiguous block with the cond/body/step inlined and
a back-jump at the end. Each declared local int adds a slot to the
global memory directory; adding `int qq` shifts every subsequent
internal directory pointer by +4.

## Per-component init bytecode

Component init blocks use a 3-byte opcode header followed by a body
made of ASCII arguments, `LOAD` operands, and commas.

```
<u32 length>            block size (excluding the prefix)
<opcode_byte_3>         3-byte opcode header (see table below)
<ASCII args>            comma-separated arguments, e.g. "0,20,160,50,"
<5-byte LOAD ops>       01 XX YY 00 00  = LOAD u32(0xYYXX), where the
                        loaded value is an *attribute ID* (an index into
                        the per-page attribute record table)
<2c=','>                separators between args
```

Observed 3-byte opcode headers:

| Bytes        | Component type(s)                       | Mnemonic                   |
|--------------|------------------------------------------|----------------------------|
| `09 0d 04`   | Page (type 121)                          | `PAGE_INIT`                |
| `09 1d 08`   | XFloat (59), Text (116), ScrollingText (55) | `WIDGET_INIT_9`          |
| `09 1c 08`   | XFloat variant (alternate firmware)      | `WIDGET_INIT_8`            |
| `09 00 04`   | Button (98), DualStateButton (53)        | `BUTTON_INIT`              |
| `09 01 04`   | Picture (112)                            | `PICTURE_INIT`             |
| `09 08 08`   | QR Code (58)                             | `QRCODE_INIT`              |
| `09 0a 04`   | type 113 (q0)                            | `Q0_INIT`                  |
| `04 04 05`   | Checkbox (56), Radio (57)                | `CHECKBOX_INIT`            |

Component types that emit **empty** bytecode blocks (length=0):
Hotspot (109), Timer (51), Variable (52), Slider (1), Waveform (0),
CropPicture (5). Their runtime values come from elsewhere (Slider
record, Variable val array, picxinxiadd, etc.).

## Open questions

- **Attribute-value table**: the LOAD operands inside per-component
  init blocks reference attribute IDs, not values. Resolving them
  requires the value table that those IDs index into, which lives in
  the flat region after the bytecode but isn't yet fully decoded for
  every component type.
- **Bytecode generation**: every opcode in observed projects decodes
  cleanly; no encoder exists yet. Authoring a TFT from scratch needs
  one (and a way to emit the global memory directory entries).
- **Opcode coverage**: unused opcodes in current corpus include `pic`
  (`09 01 04` size 4), `xpic` (`09 0a 04`), `picq` (`09 0f 04`), `xstr`
  variants, `crcputh`, `qrcode`, `tswS`, `lcd_dev` etc. Each needs a
  fixture that exercises the opcode — see
  [`experiments.md`](experiments.md).
