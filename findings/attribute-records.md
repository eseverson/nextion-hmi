# Per-component attribute records (gap #1)

The editor stores per-component attribute values in a uniform-stride
**24-byte record table** that lives at `strdata + page.attdataaddr`. Each
component on a page contributes one record per attribute name. The
component's `PianyiData` block holds the back-references: at each
attribute's `attpos` slot it stores the **u16 record index** of that
attribute in the page-wide table.

The per-component bytecode's `LOAD u16` operands are not "attribute IDs
indexing into some 82-entry table" as previously assumed — they are
direct indexes into this per-page 24-byte-record table. The runtime
resolves a `LOAD k` by reading `attdata[k*24..k*24+24]` and using its
fields to fetch the value.

This document covers the writer's structure, the wire format of one
record, and the per-component attribute layouts for both the well-known
and the previously-exotic component types.

## Writer routines

All in `hmitype.dll` (decoded `/tmp/decoded/plain_hmitype.dll`):

| Method                                              | Role                                                                                          |
|-----------------------------------------------------|-----------------------------------------------------------------------------------------------|
| `hmitype.GuiObj<Kind>.GetAtts_WithNoHead(atts, xilie)`| Declares this component type's attribute list: name, byte position within `PianyiData`, data type. Called once per component instance during compile. |
| `hmitype.UpAttsMake.addatt(atts, name, attpos, attvis, datafenpei, datalei, …)` | Adds one attribute to the per-component list. The `attpos` is the byte position within `PianyiData` (offset from `sizeof(<Kind>_PARAM_Head)`); `datalei` is one of the 25 `hmitype.attshulei::*` types. |
| `hmitype.Attmake.attinfUpToBin(matt, &binatt, …)`   | Converts one `matt` into a 24-byte `binattinf`. Packs the type, flags and length into the bit-packed `attmerrylenth_resbit1_pp_datafrom_changeen_atttype` field. |
| `hmitype.mpage.Allattbytes_set(objid, attname, binatt)` | Looks up the global index of `attname`, copies the `binattinf` into `mpage.allattbytes` at `idx*24`, and back-references the index in the object's `Attstrpianyi`. |
| `hmitype.mobj.attpianyiset(name, idx_u16)`         | Resolves `name` to a slot via `hmitype.GuiCombianyi.Attmake_GetAttindex(name)`, writes the u16 index into `mobj.Attstrpianyi[slot*2 + 4]`. The first 4 bytes of `Attstrpianyi` hold the byte offset of this component's init bytecode within `strdata`. |
| `hmitype.mpage.refallatt()`                         | Sizes the page's `allattbytes = N_total_attrs × 24 bytes` (`AppData.binattinfsize == 24`). |
| `hmitype.appbianyi.BianyiApp` → `OutPutPageFile`    | Top-level compile driver. After per-page compilation, appends each page's `allattbytes` to `strdata`, then sets `appinf1.attdataaddr` to the offset of the **first** page's records. |

The `Attmake_GetAttindex(name)` lookup in `hmitype.GuiCombianyi` walks
`xilie.attstr32`, a u32 array storing every recognized attribute name
packed as 4 ASCII bytes per slot. The returned index is the *global*
attribute slot; multiplied by 2 and offset by 4 it points into the
component's `Attstrpianyi` block. That slot holds the u16 record index
the bytecode will reference.

## Attribute type catalog (`hmitype.attshulei`)

From `hmitype.dll!attshulei::.cctor`:

| Name         | typevalue (`hi:lo`) | datafenpei (bytes) | Storage         |
|--------------|---------------------|--------------------|-----------------|
| `Color`      | `0x12`              | 2                  | RGB565 u16      |
| `Picid`      | `0x22`              | 2                  | picture index   |
| `Fontid`     | `0x31`              | 1                  | font index      |
| `Strlenth`   | `0x42`              | 2                  | length, paired with Sstr |
| `Select`     | `0x51`              | 1                  | enum (0..N)     |
| `Type`       | `0x61`              | 1                  | type select     |
| `key`        | `0x71`              | 1                  | key select      |
| `Videoid`    | `130 (0x82)`        | 2                  | video index     |
| `Gmovid`     | `146 (0x92)`        | 2                  | gmov index      |
| `Audioid`    | `162 (0xa2)`        | 2                  | audio index     |
| `Pageid`     | `161 (0xa1)`        | 1                  | page index      |
| `Hex16`      | `178 (0xb2)`        | 2                  | hex u16         |
| `UU8`        | `0x01`              | 1                  | u8              |
| `UU16`       | `0x02`              | 2                  | u16             |
| `UU32`       | `0x03`              | 4                  | u32             |
| `UU8_L`      | `0x07`              | 1                  | u8 ("long" enum)|
| `SS16`       | `0x08`              | 2                  | s16             |
| `SS32`       | `0x09`              | 4                  | s32             |
| `binary`     | `0x19`              | 4                  | binary data     |
| `x`          | `0x0b`              | 2                  | coord x         |
| `y`          | `0x0c`              | 2                  | coord y         |
| `w`          | `0x0d`              | 2                  | coord w         |
| `h`          | `0x0e`              | 2                  | coord h         |
| `Sstr`       | `0x0f`              | 4                  | inline string up to 4 bytes; otherwise pointer |
| `BinyiANYTYPE` | `254 (0xfe)`      | 4                  | catch-all       |

The high nibble of `typevalue` is the semantic kind (color, pic-id, …);
the low nibble is the storage form (u8, u16, u32, s16, s32, string).
Only the **low nibble** is preserved in the binary record — the
semantic kind is lost at compile time and must be recovered from the
attribute *name* via `attstr32`/`AppAttNames`.

## 24-byte record (`binattinf`)

Wire layout (all little-endian; struct sequential-layout):

```
offset  size  field
  +0    u32   objdatarampos     # byte offset of owning component's
                                #   objdata_Ram within the page's media
                                #   blob (NOT a TFT file offset)
  +4    s32   attmemorypos      # either the VALUE itself (Sstr / inline
                                #   numeric) OR a pointer to a memory
                                #   region elsewhere
  +8    s32   num_maxval        # upper bound (or auto: count-1 for
                                #   pic/font/video/gmov/audio)
 +12    s32   num_minval        # lower bound (0 for resource-id types)
 +16    u8    frompageid
 +17    u8    fromobjid
 +18    u8    str_encodeh_star  # = mobj.objdataram.memorypos (low byte)
 +19    u8    att_changeid      # comes from Upatt0.attchangeid
 +20    u32   packed            # bit-packed (see below)
 = 24 bytes
```

### Packed u32 at offset +20

Built in `attinfUpToBin` by shifting/adding (LSB → MSB):

| Bits  | Field         | Meaning                                                         |
|-------|---------------|-----------------------------------------------------------------|
| 0..3  | `attlei & 0xF`| Storage type (the low nibble of `typevalue`)                    |
| 4     | `~change`     | 0 if this attr can change at runtime, 1 if read-only            |
| 5     | `datafrom`    | 1 if `attposup > -1` or `attposup == -2` (has a backing position)|
| 6     | `~ispv`       | 0 if this attr is "page-volatile" (PV)                          |
| 7     | `~pp`         | 0 if this attr is "permanent" (PP)                              |
| 8..31 | `merrylenth × 2` | byte length (sometimes ×4 — see `attinfUpToBin` IL)         |

`merrylenth` is `Upattinf.merrylenth × 2` (when `pp==0` and `ispv==0` and
`datafrom` and `change` are all clear, the count becomes
`merrylenth × 16`; in practice for a 1-byte UU8 the stored merrylenth
is 1, and for a 2-byte Color/UU16 it's 2 — see test fixture
`17_more_components/17.tft`).

### Resolving the actual value

The bytecode `LOAD k` reads u16 `k` (the record index in the page's
table), fetches the 24-byte record, then:

- For most numeric types (`UU8`, `UU16`, `SS16`, `SS32`, `x`, `y`, `w`,
  `h`, `Color`, `Picid`, `Fontid`, …), the **value is `attmemorypos`
  itself**, interpreted as the storage form. E.g. for a page's `w=480`
  attribute, `attmemorypos = 480 = 0x1e0` and `attlei=0xd` (storage=2-byte).

- For `Sstr` strings that fit in 4 bytes, the value is the 4 raw bytes
  of `attmemorypos`. Longer strings are addressed indirectly: a paired
  `<name>_maxl` (`Strlenth` attribute) gives the allocated length, and
  the actual text lives in a separately-allocated memory region whose
  offset is stored in `attmemorypos`.

- For dynamically-allocated arrays (curve buffers, file-stream buffers,
  data-record blocks), `attmemorypos` is the offset into a global
  memory area; see `databianyi.attaddrhandlist` in
  `hmitype.dll!appbianyi`.

## Per-page attribute table

Per page, `mpage.attdataaddr` is set during compile to the byte offset
within `strdata` where this page's records begin. Records are written
contiguously in the order produced by `mpage.refallatt()`, which
enumerates every object on the page and lists its attributes in a fixed
order (id, type, x, y, endx, endy, w, h, then each `GetAtts_WithNoHead`
attribute). Total record count per page = sum over each object of
`(visible_attrs.Count + 8 head fields)`.

In `appinf1`, the global field `attdataaddr` (now correctly placed at
**+0x0c**, see the `appinf1` layout note below) points to the *first*
page's records. To find page `p`'s records, read the page directory
entry's `pagexinxi.attdataaddr` field, which is relative to `strdataaddr`.

## Per-component attribute layouts

For each component class, the table below lists every attribute the
editor declares in `GetAtts_WithNoHead` for the F-series (T1) path:
name, byte offset from `sizeof(Kind_PARAM_Head)` within `PianyiData`,
storage type. Extracted programmatically from
`hmitype.dll`'s IL — see `attrs-raw.txt` for the full dump (every class,
both compile paths).

The data is from the **F-series (T1)** code path; non-F-series models
re-emit identical declarations with the same offsets but the second
block in `attrs-raw.txt` shows alternate positions for K0/T0/X3/X5 etc.

### Exotic component types

These are the types the previous findings explicitly flagged as
unmapped.

#### CheckBox (type 56) — `GuiObjCheckBox`

| attpos | name    | type   |
|--------|---------|--------|
| +1     | style   | Select |
| +1     | borderw | UU8    |
| +2     | borderc | Color  |
| +4     | bco     | Color  |
| +6     | pco     | Color  |
| +8     | val     | UU8    |

#### Radio (type 57) — `GuiObjRadio`

| attpos | name    | type   |
|--------|---------|--------|
| +1     | bco     | Color  |
| +2     | pco     | Color  |
| +4     | val     | UU8    |

#### QR Code (type 58) — `GuiObjQrcode`

| attpos | name      | type     |
|--------|-----------|----------|
| +1     | sta       | Select   |
| +1     | dis       | UU8      |
| +2     | bco       | Color    |
| +4     | pco       | Color    |
| +6     | pic       | Picid    |
| +8     | txt_maxl  | Strlenth |
| +12    | txt       | Sstr     |

#### Gauge / Pointer (type 122) — `GuiObjZhizhen`

Has its own GetAtts_WithNoHead — see `attrs-raw.txt` section
`GuiObjZhizhen`. Key attrs in order: `bco`, `bpic`, `pco`, `wid`, `val`,
`maxval`, `minval`, plus needle geometry. Layout matches Slider's
pattern (16-byte record at `pageattdata + pianyislot×24`).

#### DualStateButton (type 53) — `GuiObjSwitchbutton`

| attpos | name       | type     |
|--------|------------|----------|
| +1     | dez        | Select   |
| +1     | val        | UU8      |
| +2     | bco        | Color    |
| +4     | pco        | Color    |
| +6     | bco2       | Color    |
| +8     | pco2       | Color    |
| +10    | pco1       | Color    |
| +12    | font       | Fontid   |
| +13    | dis        | UU8      |
| +14    | txt_maxl   | Strlenth |
| +16    | txt        | Sstr     |

#### ScrollingText (type 55) — `GuiObjSLText`

| attpos | name      | type     |
|--------|-----------|----------|
| +1     | sta       | Select   |
| +1     | style     | Select   |
| +1     | key       | key      |
| +2     | borderc   | Color    |
| +4     | borderw   | UU8      |
| +5     | font      | Fontid   |
| +6     | bco       | Color    |
| +6     | picc      | Picid    |
| +6     | pic       | Picid    |
| +8     | pco       | Color    |
| +10    | xcen      | Select   |
| +11    | leftshow  | UU8      |
| +12    | left      | Select   |
| +13    | ch        | UU8      |
| +14    | txt_maxl  | Strlenth |
| +16    | txt       | Sstr     |
| +20    | isbr      | Select   |
| +21    | spax      | UU8      |
| +22    | spay      | UU8      |
| +26    | path_m    | Strlenth |
| +28    | path      | Sstr     |
| +36    | maxval_y  | SS32     |
| +40    | val_y     | SS32     |

#### Waveform (type 0) — `GuiObjCurve`

| attpos | name       | type   |
|--------|------------|--------|
| +1     | sta        | Select |
| +1     | dir        | Select |
| +2     | ch         | Select |
| +4     | bco        | Color  |
| +4     | picc       | Picid  |
| +4     | pic        | Picid  |
| +6     | gdc        | Color  |
| +8     | gdw        | UU8    |
| +9     | gdh        | UU8    |
| +10    | objWid     | UU16   |
| +12    | objHig     | UU16   |
| +14    | pco0       | Color  |
| +16    | pco1       | Color  |
| +18    | pco2       | Color  |
| +20    | pco3       | Color  |
| +22    | inittrue   | UU8    |
| +24    | dis        | UU16   |
| +28    | molloc_s   | SS32   |
| +32    | molloc     | SS32   |

The `molloc`/`molloc_s` pair is the per-channel data buffer pointer
(into the global memory area; `attmemorypos` is the offset).

#### CropPicture (type 5) — `GuiObjPicc`

| attpos | name | type   |
|--------|------|--------|
| +1     | picc | Picid  |
| +2     | vvs0 | SS16   |
| +4     | vvs1 | SS16   |

#### Gmov (type 168 etc.) — `GuiObjGmov`

| attpos | name      | type     |
|--------|-----------|----------|
| +1     | vid       | Gmovid   |
| +2     | en        | UU8      |
| +3     | loop      | Select   |
| +6     | dis       | UU16     |
| +8     | tim       | SS32     |
| +12    | stim      | SS32     |
| +20    | qty       | SS32     |
| +24    | from      | Select   |
| +26    | path_m    | Strlenth |
| +28    | path      | Sstr     |
| +32    | molloc_s  | SS32     |
| +36    | molloc    | SS32     |

#### ExPic (extended picture, type ?) — `GuiObjExPic`

| attpos | name   | type     |
|--------|--------|----------|
| +2     | path_m | Strlenth |
| +4     | path   | Sstr     |

#### type 113 — `GuiObjPic` or `GuiObjQrcodeVP`

Three candidate classes have a small attr list compatible with type
113's bytecode (`Q0_INIT`, 9 0a 04). Most likely `GuiObjQrcodeVP`:

| attpos | name     | type     |
|--------|----------|----------|
| +1     | RAM1     | UU16     |
| +1     | VP       | UU16     |
| +2     | sta      | Select   |
| +3     | dis      | UU8      |
| +4     | bco      | Color    |
| +6     | pco      | Color    |
| +8     | pic      | Picid    |
| +14    | txt_maxl | Strlenth |
| +16    | txt      | Sstr     |

If the project's `q0` is actually a `Pic` (type 112), the layout is the
single-attr `GuiObjPic { pic: Picid }`.

### Already-known types (cross-check)

#### XFloat (type 59) — `GuiObjXfloat`

Confirms the existing 24-byte record decoder:

| attpos | name    | type     |
|--------|---------|----------|
| +1     | sta     | Select   |
| +1     | style   | Select   |
| +1     | key     | key      |
| +2     | borderc | Color    |
| +4     | borderw | UU8      |
| +5     | font    | Fontid   |
| +6     | bco     | Color    |
| +6     | picc    | Picid    |
| +6     | pic     | Picid    |
| +8     | pco     | Color    |
| +10    | xcen    | Select   |
| +11    | ycen    | Select   |
| +12    | val     | SS32     |
| +16    | vvs0    | UU8      |
| +17    | vvs1    | UU8      |
| +20    | isbr    | Select   |
| +21    | spax    | UU8      |
| +22    | spay    | UU8      |

Total attrs after head (8 head + 18 declared) = 26 records.

#### Slider (type 1) — `GuiObjSlider`

| attpos | name   | type   |
|--------|--------|--------|
| +1     | mode   | Select |
| +1     | sta    | Select |
| +2     | psta   | Select |
| +3     | wid    | UU8    |
| +4     | hig    | UU8    |
| +5     | dis    | UU8    |
| +6     | pic    | Picid  |
| +6     | picc   | Picid  |
| +6     | bco    | Color  |
| +8     | pic2   | Picid  |
| +8     | pco    | Color  |
| +10    | val    | UU16   |
| +12    | maxval | UU16   |
| +14    | minval | UU16   |
| +16    | ch     | UU8    |
| +22    | pic1   | Picid  |
| +22    | picc1  | Picid  |
| +22    | bco1   | Color  |

#### Button (type 98) — `GuiObjButton` and `GuiObjButton_T`

See `attrs-raw.txt` — 42 attrs covering both Button styles. Notable:
`val` at +16, `txt` (Sstr) at +20, `txt_maxl` at +18.

#### Variable (type 52) — `GuiObjVari`

| attpos | name     | type     |
|--------|----------|----------|
| +1     | sta      | Select   |
| +2     | txt_maxl | Strlenth |
| +4     | txt      | Sstr     |
| +8     | val      | SS32     |

The 4-byte marker `90 01 01 00` previously documented in `tft_format.py`
is actually the **packed flag word** at offset +20 of one of these
records: low nibble = type code, etc. The u32 array following the
marker is a sequence of `binattinf.attmemorypos` values being read as
contiguous "Variable.val" fields — coincidentally aligned because every
Variable component happens to produce a `val=SS32` attribute.

## objxinxi entry layout (per-component, 232 bytes)

Cracked by decoding `tests/editor outputs/23_minimal_project/23.tft` (one
empty page, one object — the Page itself) and cross-checking against
`17_more_components/17.tft`.

```
offset  size  field
  +0    u8    lei            # component type (e.g. 121 = GuiObjPage,
                             #   59 = GuiObjXfloat)
  +1    u8    id             # instance id
  +2    u16   ??             # constant 0x3700 across every entry seen so
                             #   far; meaning unknown
  +4    u32   init_off       # offset (rel strdata) of this component's
                             #   init bytecode; mirrored at +52
  +8    20×0xff              # padding
  +28   u32   objdatarampos  # byte offset of this component's
                             #   `objdata_Ram` inside the page media blob
                             #   (matches `binattinf.objdatarampos`)
  +32   12 bytes ??          # mostly zeros; byte +34 is 0x7f (= 127)
                             #   for every entry; meaning unknown
  +44   u16   w
  +46   u16   h
  +48   u16   endx           # = x + w − 1
  +50   u16   endy           # = y + h − 1
  +52..+231   Attstrpianyi   # 180 bytes:
                             #   +0..+3:  u32 bytecode_offset (= init_off)
                             #   +4..+179: 88 × u16 slots, indexed by
                             #             AppAttNames[N]; each slot holds
                             #             the record_index into the
                             #             page-wide allattbytes table, or
                             #             0xffff for "no record"
= 232 bytes per object
```

Page-aligned trailing padding (0xff) follows the last object in the
`objxinxiadd` region. The page directory's `objstar` indexes the first
object on a page; `objqyt` counts how many consecutive entries belong to
that page.

### Confirmed slot → record_index examples

Minimal project's Page (`23.tft`, obj 0):

| AppAttNames slot | name   | record_index | record contents              |
|------------------|--------|--------------|------------------------------|
| 62               | lei    | 0            | attlei=0x1, val=121          |
| 49               | id     | 1            | attlei=0x1, val=0            |
| 1                | vscope | 3            | attlei=0x1, val=0            |
| 42               | x      | 9            | attlei=0xb, val=0            |
| 43               | y      | 10           | attlei=0xc, val=0            |
| 46               | w      | 11           | attlei=0xd, val=480          |
| 47               | h      | 12           | attlei=0xe, val=320          |
| 44               | endx   | 13           | attlei=0x8 (SS16), val=479   |
| 45               | endy   | 14           | attlei=0x8 (SS16), val=319   |
| 2                | sta    | 23           | attlei=0x1, val=0            |
| 4                | bco    | 24           | attlei=0x2 (UU16/Color)      |

Per-page table size: each component reserves a contiguous record range
sized to its component class. Page = 33 records (records 0..32 in the
minimal table); XFloat = 41 records (records 33..73 in fixture 17 page 0
between obj0=Page and obj2=second Xfloat). Inside that range, most slots
have `attlei=0` (the encoder leaves them empty); only AppAttNames slots
that the type's `GetAtts_WithNoHead` actually uses get filled records.

### What this resolves

- **Per-page record indexing**: no longer "an open puzzle". Each
  component's Attstrpianyi is the per-component lookup table; the record
  it points to is the live attribute value. The records themselves are
  sparse-by-design.
- **Queued experiment 23** ("H2 trailing-region 4×32-byte rows"): the
  hypothesised row structure does not exist. Bytes `H2[+0x4c..+0xc4]`
  are 120 bytes of `0xff` padding (already confirmed by
  [`h2-trailing.md`](h2-trailing.md)); the per-page metadata that the
  rows would have held lives in the `pagexinxi` directory at `pageadd`
  and the `objxinxi` directory at `objxinxiadd`, both decoded above.

### Still open

1. **Per-component stride** in the page-wide record table: Page = 33,
   XFloat = 41. The function `class_name → stride` is not yet pinned
   down; might be `(max_appatt_slot_used + 1)` rounded up, or a per-class
   constant from a `hmitype.dll` table. Decoding obj1..obj49 of fixture
   17 systematically and tabulating stride per type would close this in
   one pass.
2. **`hdr[+2..+3] = 0x3700`** constant: present on every entry; unknown
   purpose.
3. **`hdr[+32..+43]` 12-byte block**: mostly zeros; byte +34 = 0x7f
   constant; remaining bytes vary subtly between entries. May hold flags
   or a secondary memory pointer.

## Cross-references

- **Test fixtures** in `nextion/tests/editor outputs/`:
  - `17_more_components/17.tft` — has Waveform, CropPicture,
    DualStateButton, ScrollingText, Checkbox, Radio, QRCode, Gauge,
    type 113 all in one project. Page 0 record table starts at file
    offset `0x82808`; 960 records.
  - `15_picture/15.tft` — Picture (type 112).
  - Their HMI inputs in the same folder hold the authored values.

- **Raw IL extraction**: `nextion/findings/attrs-raw.txt` lists every
  `GuiObj*` class's full attribute set in the F-series code path
  (first occurrence) plus the alternate-model path (second occurrence).
  Generated by parsing `/tmp/hmitype_all.il` from the decompiled
  `plain_hmitype.dll`.

- **Disassembly source**: To regenerate, copy `plain_*.dll` from
  `/tmp/decoded/` into a single directory then run
  `monodis hmitype.dll > /tmp/hmitype_all.il`. (Requires every
  referenced DLL — `achmiface`, `Tcode`, `HMIFORM`, `ControlInterFace`,
  `DevComponents.DotNetBar2`, `AxInterop.WMPLib`, `Interop.WMPLib` —
  to be in the same working directory.)

## What's not yet figured out

1. **Mapping bytecode `LOAD u16` to attribute name.** The bytecode
   encodes a per-page record index, not the attribute's global ID. To
   resolve a `LOAD k` back to a meaningful name, we still need the
   per-page attribute *enumeration order* (the `mpage.allattnames` list
   that `refallatt()` builds). That order depends on (1) the order of
   objects on the page and (2) the per-component attribute order from
   `GetAtts_WithNoHead`. Both are deterministic and recoverable from
   the test fixtures; cross-checking a known fixture against the
   enumerated record table will let us tabulate the order definitively.

2. **String values longer than 4 bytes.** For `txt` attributes whose
   value is too long to fit in `attmemorypos`, the wire stores an
   offset into a memory region. The offset's coordinate system (global
   vs. per-page vs. relative to `staticstrBeg`) wasn't pinned down
   here. Inspecting the `databianyi.attaddrhandlist` allocation in
   `appbianyi.mollocmemory_add` would close this — it's a ~50-line
   managed method, just not in this pass.

3. **High nibble of `typevalue`.** Only the low nibble survives into
   the binary record. The runtime presumably uses the attribute name
   (via `Attmake_GetAttindex`) to recover the semantic kind. For a
   reader, that's fine — for an *encoder*, the name → high-nibble
   mapping needs to be tabulated. The `attrs-raw.txt` extraction is
   the input table; producing the name→typevalue lookup is mechanical.

4. **`Strlenth` companion records.** Every `Sstr` attribute is paired
   with a `Strlenth` named `<sstrname>_maxl` (or `_m`). The pairing is
   implicit in the order they're emitted in `GetAtts_WithNoHead`, but
   their binding mechanism at runtime (does the Strlenth record's
   `attmemorypos` mirror the Sstr's allocation size? Or does the Sstr
   record's `attmemorypos` point to a header that includes the
   length?) isn't fully clear from static analysis.

## Where the next person should look

1. **For the bytecode→attribute-name mapping (item 1 above)**: Round-trip
   a single-page TFT with known attributes and walk
   `mpage.refallatt()` IL to confirm the enumeration order.

2. **For string-value indirection (item 2)**: Read the IL of
   `appbianyi.mollocmemory_add` at line ~328600 of `/tmp/hmitype_all.il`
   and trace how `attaddrhandlist[molloclabel]` is populated. The
   pointer's coordinate system is in there.

3. **For an encoder**: With the layouts in this doc plus the bytecode
   format already in `format-bytecode.md`, the encoder is a small
   serializer:
   1. For each component, build a list of `matt` instances (name,
      attval, type from the table here).
   2. Run `attinfUpToBin` (Python port) to produce 24-byte records.
   3. Concatenate per-page; set `pagexinxi.attdataaddr` and
      `appinf1.attdataaddr`.
   4. Build each `mobj.Attstrpianyi` (180 bytes T1) with the indexes.

## Corrections to other findings

While reading the IL of `hmitype.appinf1`, the layout in
`format-tft.md`'s Header 2 section turned out to be partly
mis-labelled. The real layout (from
`hmitype.dll!appinf1`):

```
+0x00  staticstrBeg    (u32)
+0x04  AppAllvasAddr   (u32)
+0x08  AppAllvasQty    (u32)
+0x0c  attdataaddr     (u32)    ← was "usercode_address" in format-tft.md
+0x10  resourcesfileddr (u32, =0x10000)
+0x14  strdataaddr     (u32, =0x80000)
+0x18  pageadd         (u32)
+0x1c  objxinxiadd     (u32)
+0x20  picxinxiadd     (u32)
+0x24  gmovxinxiadd    (u32)
+0x28  videoxinxiadd   (u32)
+0x2c  wavxinxiadd     (u32)
+0x30  zimoxinxiadd    (u32)
+0x34  MainCodeHex     (u32)
+0x38  pageqyt         (u16)    ← was "pageqyt at +0x30"
+0x3a  objqyt          (u16)
+0x3c  picqyt          (u16)
+0x3e  gmovqyt         (u16)
+0x40  videoqyt        (u16)
+0x42  wavqyt          (u16)
+0x44  zimoqyt         (u16)
+0x46  res1            (u16)
+0x48  encode          (u8)
+0x49  res2            (u8)
+0x4a  res3            (u16)
= 0x4c bytes
```

This is not the agent's territory to update; flag it for the spec
owner. The h2_cipher.py struct decoder appears to already match the
real layout in practice (its caller decodes pageqyt from offset 0x38
correctly), so this is a documentation issue, not a code bug.

The `pagexinxi` per-page directory entry (at `appinf1.pageadd`) is:

```
+0x0  objstar      (u16)   first object index on this page
+0x2  objqyt       (u8)    object count
+0x3  res0         (u8)
+0x4  HexPos       (u32)   offset to this page's init bytecode
+0x8  attdataaddr  (u32)   offset (rel strdata) to this page's att records
+0xc  medatapos    (u32)   offset to this page's runtime media blob
= 16 bytes per page entry
```
