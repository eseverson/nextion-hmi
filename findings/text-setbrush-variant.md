# Text-family `setbrush` spax/spay inline-vs-LOAD dispatch (Lookup 1)

Closes [`init-bytecode-encoder.md`](init-bytecode-encoder.md) gap "Getatt_Codes
minor dispatch branches" for the Text/Number/XFloat/Button family: when
does the editor emit `spax`/`spay` as a 5-byte `LOAD` and when does it
inline the value as ASCII bytes?

## Headline rule

**The inline-vs-LOAD choice is per-type, not per-value.** Whether
`'&spax&'`/`'&spay&'` inlines as ASCII or resolves to a `LOAD u16` is
determined entirely by the attribute's `Upatt0.attposup` slot at the
point `canshutihuan` runs over the template — that slot is set per-type
during `<GuiObj…>.GetAtts_WithNoHead`, and for the F-series
(`xilie.function_objdataraminmemory != 1`) the values are:

| Component (objType)       | `spax`/`spay` `attposup` | Effect in bytecode    |
|---------------------------|--------------------------|-----------------------|
| `text` (116)              | **-1**                   | Inlined as ASCII      |
| `button_t` / DSB (53)     | **-1**                   | Inlined as ASCII      |
| `button` (98)             | **-1**                   | Inlined as ASCII      |
| `number` (54)             | `num+21` / `num+22`      | Emitted as `LOAD`     |
| `xfloat` (59)             | `num+21` / `num+22`      | Emitted as `LOAD`     |
| `gtext` / ScrollingText (55) | `num+25` / `num+26`   | Emitted as `LOAD`     |

So the value that gets inlined is whatever the user set for that
attribute (default `"0"` for both, max `255`). The "spax/spay=0 ⇒ inline"
hypothesis in the original task description is the special case observed
because all Text/Button/Button_T components in real projects happen to
leave spax/spay at the default `0`. A Text with spax=5 would emit
ASCII `'5'` at the same byte slot — still inlined, not LOAD.

## Where the dispatch lives in the editor

All in `/tmp/decoded/plain_hmitype.dll`.

### 1. Per-type attribute schema (`<GuiObj…>.GetAtts_WithNoHead`)

Each component type publishes its attribute table via
`hmitype.GuiObj<Kind>.GetAtts_WithNoHead(atts, xilie)`. The table
returns *position-in-PianyiData* for each attribute, or `-1` to mark the
attribute as a compile-time constant (no RAM slot, value is baked into
the bytecode).

Cross-checked attposup for the spax/spay attribute in the
`function_objdataraminmemory != 1` branch (the F-series path):

```csharp
// hmitype.GuiObjText.GetAtts_WithNoHead  (IL line 5670-5690)
atts.addatt("isbr", 1,  …, objchangetype.yes, …);   // LOAD
atts.addatt("spax", -1, …, objchangetype.no,  …);   // INLINE
atts.addatt("spay", -1, …, objchangetype.no,  …);   // INLINE
atts.addatt("pw",   8,  …, objchangetype.yes, …);   // LOAD

// hmitype.GuiObjButton.GetAtts_WithNoHead  (IL ~38680)
// hmitype.GuiObjButton_T.GetAtts_WithNoHead  (IL ~2119-2121)
atts.addatt("spax", -1, …); atts.addatt("spay", -1, …);

// hmitype.GuiObjXfloat.GetAtts_WithNoHead  (IL line 33659-33660)
// hmitype.GuiObjNum.GetAtts_WithNoHead  (IL line 19513-19514)
// hmitype.GuiObjGText.GetAtts_WithNoHead  (IL line 29119-29120)
atts.addatt("spax", num + 21, …, objchangetype.yes, …);
atts.addatt("spay", num + 22, …, objchangetype.yes, …);
```

`objchangetype.no` correlates with `-1`: a compile-time-only attribute
can't be mutated at runtime (no RAM backing) and therefore doesn't need
a LOAD address.

### 2. Placeholder substitution (`mobj.canshutihuan`)

`Getatt_Codes` always emits the same template string (with
`'&spax&'`/`'&spay&'` placeholders). The per-attribute decision happens
later in `mobj.canshutihuan(ref List<string> bt, byte state)` at IL line
10017. For each `'&attr&'` placeholder:

```csharp
// hmitype.mobj.canshutihuan  (IL 10017-10058)
if (state == 1 || atts[j].Upatt0.attposup == -1) {
    // INLINE: substitute with the attribute's current value as decimal
    string newValue = atts[j].attval.BytesToNum(atts[j].Upatt0.attlei).ToString();
    text = text.Replace("'&" + atts[j].attname + "&'", newValue);
} else {
    // LOAD: substitute with "objname.attname"; the script compiler
    // resolves this to a 5-byte LOAD operand later.
    text = text.Replace("'&" + atts[j].attname + "&'", objname + "." + atts[j].attname);
}
```

`state == 1` is the **edit-preview** path (`GetRefbianji`, IL 9869-9895);
it inlines everything regardless of `attposup`. `state == 0` is the
**runtime compile** path (`Getbianyi`, IL 9797-9867) — the one that
produces the bytecode actually stored in the `.tft` — and the
`attposup == -1` test is the only inline trigger.

After substitution, the resulting source line goes through
`appbianyi.bianyionline → GuiCombianyi.CodeRun_Run`. A reference of the
form `objname.attname` becomes an `01 LL HH 00 00` LOAD operand; a
literal decimal token becomes ASCII bytes.

## Verified shapes in `nextion/source/nextion.hmi.tft`

Disassembled `setbrush` blocks (opcode `09 1c 08`, the 1.67.1 setbrush
variant) on the project's main page. Template positions
14-15-16-17-18 are `(isbr, spax, spay, pw_or_0, borderw_or_0)`:

| Block @     | Type       | Bytes at positions 14-18 (after `,1,`)                  | Interpretation               |
|-------------|------------|---------------------------------------------------------|------------------------------|
| `0x700e2`   | XFloat     | `LOAD(63), LOAD(64), LOAD(65), '0', '0'`                | All LOADs (xfloat keeps RAM) |
| `0x70178`   | XFloat     | `LOAD(104), LOAD(105), LOAD(106), '0', '0'`             | All LOADs                    |
| `0x706d4`   | Text       | `LOAD(465), '0', '0', LOAD(462), '0'`                   | spax/spay inlined            |
| `0x70772`   | Text       | `LOAD(506), '0', '0', LOAD(503), '0'`                   | spax/spay inlined            |
| `0x716ad`   | Button_T   | `LOAD(1192), '0', '0', '0', '1'`                        | spax/spay inlined + sta=1/style=4 |

Block-length deltas (62 vs 64 vs 68 bytes) match the per-pattern byte
count: each inlined `0` costs 1 ASCII byte plus a comma, vs a LOAD costs
5 bytes plus a comma.

## Implications for the encoder

`scripts/tft_init_encoder.py` already substitutes attribute names with
LOAD operands via the `attr_addr` callable. The needed change is to make
spax/spay (and `pw` for Button_T/Button — `pw` is `objchangetype.no` in
the same branch, default value `0`) emit a **literal** rather than a
LOAD when the component type is `text`/`button`/`button_t`. Concretely:

```python
INLINE_ALWAYS_BY_TYPE = {
    53:  {"spax", "spay"},               # button_t
    98:  {"spax", "spay"},               # button
    116: {"spax", "spay"},               # text
    # number(54), xfloat(59), gtext(55) → spax/spay are LOAD
}

def emit_attr(comp_type, attname, value, attr_addr):
    if attname in INLINE_ALWAYS_BY_TYPE.get(comp_type, ()):
        return str(value).encode("ascii")      # ASCII decimal literal
    return b"\x01" + struct.pack("<I", attr_addr(attname))  # LOAD u32
```

The same -1 dispatch applies to a few other attributes too — auditing
each type's `GetAtts_WithNoHead` else-branch for `attposup == -1` reveals
the full list (e.g. `borderc`, `borderw`, `style`, `sta`, `key`,
`txt_maxl`). Those are already handled by the encoder because they fall
into the `'&sta&'`-driven template dispatch in `Getatt_Codes`; spax/spay
are the only ones reached by the template that don't naturally branch.

## Edit-preview vs runtime variant

Note that `GetRefbianji` (the edit-preview path, `state=1`) ignores
`attposup` entirely and inlines all attributes. This means the
`.tft` file's **edit-preview bytecode** (used by the editor's render
pane, not the firmware) has an entirely different shape for XFloat/
Number/GText — every LOAD becomes an inline literal. We're producing
the runtime bytecode (the `Ref` event compiled at `state=0`), so we
only care about the rule above.

## References

- `hmitype.GuiObjText.GetAtts_WithNoHead` IL ~5646-5691
- `hmitype.GuiObjButton.GetAtts_WithNoHead` IL ~38580-38720
- `hmitype.GuiObjButton_T.GetAtts_WithNoHead` IL ~2072-2123
- `hmitype.GuiObjXfloat.GetAtts_WithNoHead` IL ~33600-33662
- `hmitype.GuiObjNum.GetAtts_WithNoHead` IL ~19470-19516
- `hmitype.GuiObjGText.GetAtts_WithNoHead` IL ~29062-29126
- `hmitype.UpAttsMake.addatt` (sets `attposup = attpos`) IL ~47552
- `hmitype.mobj.canshutihuan` IL ~10017-10058 — placeholder substitution
- `hmitype.mobj.Getbianyi`/`GetRefbianji` IL ~9797-9895 — state=0 vs state=1 dispatch
- `hmitype.mobj.Getatt_Codes` (templates) IL ~8834-9700 (per-type
  template body, all emit `'&spax&','&spay&'` regardless of value)
