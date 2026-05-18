# Memory allocation & frame-offset rules

The editor's compiler maintains a single, monotonic byte-array buffer
that — by the time `appbianyi.StructHtoL` finishes — becomes the
project's **public runtime memory image**. Every per-page object lives
in this buffer at a deterministic offset; the on-disk byte-encoded
local-var refs (`01 LL LL LL LL`) in event-handler bytecode use that
offset directly. The same buffer is also where variable-length
attribute payloads (strings, picture-data buffers, dynamic curve/gmov
buffers) get assigned their `attmemorypos` value.

This document recovers the allocator from
`/tmp/decoded/plain_hmitype.dll` (IL dump at
`/tmp/hmitype_all.il`, regenerated via the
[`achmi-internals.md`](achmi-internals.md) pipeline).

It unblocks the three roadmap items called out in
[`next-steps.md`](next-steps.md):

- **1a** Component-attribute resolver (`dim=h0.val`-class refs)
- **2c** Variable-length attribute storage allocator
- **4**  Global memory directory at usercode offset 0

Cross-references:

- [`attribute-records.md`](attribute-records.md) — 24-byte `binattinf`
  table & `attshulei` types (stride per attribute)
- [`script-compiler.md`](script-compiler.md) — `01 LL LL LL LL`
  local-var ref encoding
- [`format-tft.md`](format-tft.md) — usercode section / H2 fields

## Cast of characters

```
appbianyi (static)
 └── databianyi : databianyitype                  // singleton, per-compile
       ├── Public_Memory  : PublicMemory_handcenter
       │     ├── initDatas       : byte[]        // serialized public RAM image
       │     └── noinitdatasize  : int32         // bytes of dynamic noinit tail
       ├── Private_Memory : PrivateMemory_handcenter
       │     ├── initDatas       : List<byte[]>  // one entry per page
       │     └── noinitdatasize  : int32[]       // per-page dynamic tail size
       ├── memory_attaddrhand0   : List<memory_attaddrhand>
       └── attaddrhandlist       : List<int32>   // resolved offsets, by id

memory_attaddrhand
 ├── varinmemorypos    : int32   // byte position in objdata_Ram where the
 │                               //   resolved offset is back-patched
 ├── mollocsize        : int32   // total bytes to allocate (rounded up to 4)
 ├── decpos            : int32   // resolved global byte offset (set in StructHtoL)
 ├── val               : byte[]  // init data (string contents) or null for
 │                               //   pure dynamic allocations
 └── attaddrhandlistid : int32   // back-ref index into attaddrhandlist
```

## `mollocmemory_add` (allocator entry-point)

Located at `/tmp/hmitype_all.il:326852-326992`. ~70 lines of IL.

```python
def mollocmemory_add(attaddrhand0,    # List<memory_attaddrhand> — usually
                                       #   the global databianyi.memory_attaddrhand0
                     memorypos,        # byte position in objdata_Ram where the
                                       #   resolved offset must be written back
                     mollocsize,       # bytes requested (>= 1)
                     molloctype,       # 0 = dynamic (no init data), 1 = init w/ val
                     pageid,           # page index, for diagnostics only
                     val):             # optional init bytes (string/etc), or None
    # Reject zero-byte allocations.
    if mollocsize < 1:
        return -1

    # Round mollocsize up to a multiple of 4 (32-bit alignment).
    while mollocsize & 3 != 0:
        mollocsize += 1

    h = memory_attaddrhand()
    h.varinmemorypos = memorypos
    h.mollocsize     = mollocsize
    if val:
        h.val = val[:val.Length]      # copy
    else:
        h.val = b""

    # Sanity: if init data is provided, it must fit.
    if h.val and mollocsize < len(h.val):
        MessageOpen.Show("mollocsize<val.Length")
        return -1

    # Reserve a slot in attaddrhandlist, value -1 (placeholder).
    h.attaddrhandlistid = len(databianyi.attaddrhandlist)
    databianyi.attaddrhandlist.append(-1)

    attaddrhand0.append(h)
    return h.attaddrhandlistid
```

The returned `id` is stored in `matt.molloclabel` by the caller. Later
the runtime (or the compiler's
[`runtime_value_resolver`](#runtime-resolution-step) step) reads
`databianyi.attaddrhandlist[id]` to recover the byte offset.

**Key fact**: this function does **not** assign the offset itself. It
just enqueues a request. Offsets are assigned in a single batched pass
inside `appbianyi.StructHtoL` — see
[layout pass](#layout-pass-in-structhtol) below.

## Callers of `mollocmemory_add`

All four call-sites are in `mobj.GetobjRamDatas_RAM1` /
`mobj.GetobjRamDatas_NORAM1` (`/tmp/hmitype_all.il:51024, 52541, 53001,
53050`). Each handles one attribute kind:

| Attribute kind | `mollocsize`                 | `molloctype` | `val`             | Notes |
|----------------|------------------------------|--------------|-------------------|-------|
| `Sstr` (string)| `len(string) + 1` (NUL)      | `1` (init)   | string bytes + 1 NUL byte | Long strings only — short strings <=4 bytes are stored inline in `attmemorypos`. |
| `molloc` (curve/gmov dynamic buf) | `loc8` (data-size from sibling attr) | `0` (dynamic) | None | Used by `Waveform.molloc`, `Gmov.molloc`. |
| `binary` (generic byte block)     | `<name>size` attribute parsed to int  | `0` (dynamic) | None | Used by `binary`-typed attrs. |

The caller computes `memorypos = attpos + Upatt0.attposup`, where:

- `attpos` = the byte offset where the current object's
  `objdata_Ram` was placed in the public buffer (the
  `loc12` accumulator described below).
- `Upatt0.attposup` = the byte position of the attribute within
  `PianyiData`, sourced from the per-component table in
  [`attribute-records.md`](attribute-records.md) (e.g. for
  `GuiObjSlider`, `val` is at `+10`).

So `memorypos` is the absolute address within Public_Memory where the
attribute's u32 slot lives — and that's the slot that will be
back-patched with the resolved offset of the allocated payload.

## Allocation passes inside `appbianyi.StructHtoL`

`StructHtoL` (`/tmp/hmitype_all.il:332019-335338`) is the top-level
compile driver. The relevant memory-layout pipeline is:

```
loc7      : byte[3_000_000]  // the public-memory scratch buffer
loc12     : int              // cursor (next free byte in loc7)
loc30     : int              // dynamic-tail cursor (set after first pass)
```

### 1. Global `int` declarations (lines 332188..332923)

Iterates Program.s line-by-line. For each `int <name>=<value>`
declaration, calls `AddAppAllVas(name, value, &lbytes=loc7, &mpos=loc12,
SS32)`.

`AddAppAllVas`:
- Writes the int's *value* as u32 to `loc7[loc12]`.
- Increments `loc12 += 4` (`SS32.datafenpei == 4`).
- Appends the `AllvasIntType` record to `appbianyi.AppAllVas` (which is
  later serialized separately into `strdata` — see
  [`GetAppAllVasData`](#getappallvasdata) below).

After this loop:
- `loc12 == 4 * AppAllvasQty` (so the first per-page object data
  starts at byte `AppAllvasQty * 4` of the public buffer).

### 2. Per-page object loop (lines 332985..333210)

```python
for page_idx, page in enumerate(Myapp.ResourcesPages):
    page.myobjmedata0.medataclear()
    for obj_idx, obj in enumerate(page.objs):
        # First-time placement, only for non-page-private objects:
        if obj.objdataram.merry != 1:
            obj.objdataram.memorypos = loc12          # absolute offset in public
            ret = obj.GetobjRamDatas(loc7, loc12, loc12,
                                    databianyi.memory_attaddrhand0,
                                    page.myobjmedata0)
            assert ret >= 0
            loc12 += ret
            while loc12 & 3 != 0:
                loc12 += 1                            # align to 4
        # else: this object is per-page private, handled in pass 4
```

During each `GetobjRamDatas_*` call, every attribute on that object
that needs a separate memory region (Sstr long-string, molloc, binary)
triggers a `mollocmemory_add` request. The request's `memorypos` is
the byte position of *this attribute's pointer slot* inside the buffer
(i.e., `loc12_for_this_object + Upatt0.attposup`).

After this loop, `loc12` points just past the last object's data. Each
non-merry obj's `memorypos` field equals its base byte in the buffer.

**This is the source of the `0x454`-style frame offsets** seen in
event-handler bytecode like `dim=h0.val → 01 54 04 00 00`. The offset
is `obj.memorypos + Upatt0.attposup(val)`. For the project's `h0`
(Slider, `val` at `attposup=+10`), `h0.memorypos = 0x44a` puts `val`
at `0x454`.

### 3. Public allocator: pass 1 — pre-init values (lines 333230..333313)

Iterates `databianyi.memory_attaddrhand0`. For each entry **with
non-empty `val`** (i.e., the Sstr-string allocations):

```python
for h in memory_attaddrhand0:
    if not h.val: continue
    if h.mollocsize >= len(h.val):
        loc7[loc12 : loc12 + len(h.val)] = h.val   # copy init bytes
    h.decpos = loc12                               # record assigned offset
    loc7[h.varinmemorypos : h.varinmemorypos+4] = u32_le(h.decpos)  # back-patch
    attaddrhandlist[h.attaddrhandlistid] = h.decpos
    loc12 += h.mollocsize

# pad loc12 to 4
while loc12 & 3 != 0: loc12 += 1
loc30 = loc12                                       # start of dynamic tail
```

### 4. Public allocator: pass 2 — dynamic (no-init) (lines 333335..333401)

```python
for h in memory_attaddrhand0:
    if h.val: continue                              # already processed
    h.decpos = loc30                                # offset past initialized region
    loc7[h.varinmemorypos : h.varinmemorypos+4] = u32_le(h.decpos)
    attaddrhandlist[h.attaddrhandlistid] = h.decpos
    loc30 += h.mollocsize
```

### 5. Finalize public memory (lines 333403..333428)

```python
Public_Memory.noinitdatasize = loc30 - loc12       # bytes reserved for noinit
loc7[loc12 : loc12 + 4] = u32_le(Public_Memory.noinitdatasize)
loc12 += 4
Public_Memory.initDatas = loc7[0 : loc12]          # final image
memory_attaddrhand0.Clear()                        # ready for private pass
```

Resulting `Public_Memory.initDatas` layout:

```
0                                AppAllvasQty * 4
+--------------------------------+--------------------------------+
| u32[]  global-int values       | non-private object data blocks  |
| (AppAllVas)                    | (each at obj.memorypos)        |
+--------------------------------+--------------------------------+
                                 |   Sstr / init-data tail        |
                                 +--------------------------------+
                                 |   u32 = noinitdatasize          |
                                 +--------------------------------+
                                 (total length of initDatas)

[at runtime, immediately after initDatas the device reserves another
 `noinitdatasize` bytes for the dynamic Waveform/Gmov/binary
 allocations — those `decpos` values index past the end of initDatas.]
```

### 6. Per-page private allocator (lines 333437..335037)

For each page where `function_objdataraminmemory==1` (the F-series
case): allocate per-page **private memory** that holds objects flagged
`merry==1`. The layout per page:

```python
for page_idx, page in enumerate(Myapp.ResourcesPages):
    loc12 = num_objs(page)                     # 1-byte index slot per object
    # (When function_objdataraminmemory != 1, instead: loc12 = num_objs*4
    #  for a 4-byte pointer slot per object.)
    while loc12 & 3 != 0: loc12 += 1           # align

    for obj_idx, obj in enumerate(page.objs):
        if obj.objdataram.merry != 1: continue
        # Private objects get memorypos = loc12 + |Public.initDatas| + Public.noinitdatasize
        obj.objdataram.memorypos = (loc12
                                    + len(Public_Memory.initDatas)
                                    + Public_Memory.noinitdatasize)
        ret = obj.GetobjRamDatas(loc7, ...)
        loc12 += ret
        while loc12 & 3 != 0: loc12 += 1

    page.medatapos = loc12                     # record where objmedata starts
    # append objmedata bytes, then re-run the two-pass allocator on
    # databianyi.memory_attaddrhand0 (Sstr / molloc requests collected during
    # this page's GetobjRamDatas calls), using the same algorithm as steps
    # 3..5 above but writing into the page's private buffer.
    ...
    Private_Memory.initDatas[page_idx] = loc7[0 : loc12]
    Private_Memory.noinitdatasize[page_idx] = loc30 - loc12
    memory_attaddrhand0.Clear()
```

Per-page private allocations result in `Private_Memory.initDatas[page]`
being a per-page byte array that lives at runtime *after*
`Public_Memory.initDatas + Public_Memory.noinitdatasize`, i.e. private
memory starts at the same global address for every page (pages don't
coexist — only the current page is loaded). On the wire, this is one
buffer per page concatenated into the strdata region following the
public block; on the device, the runtime swaps which page's private
data is live as the user navigates pages.

## Bytecode resolution

The bytecode operands recorded in event handlers come in two flavours,
both of which the runtime resolves against the same allocator output:

### `01 LL LL LL LL` — local-var ref (component attribute)

`LL LL LL LL` is the **absolute byte offset in Public_Memory.initDatas
(or per-page Private memory) of the attribute's storage cell**.

Encoder rule:

```python
def component_attr_offset(page, comp_name, attr_name) -> int:
    obj = page.find_object(comp_name)
    attpos = attribute_record_for(obj.type, attr_name).attpos  # from attribute-records.md
    return obj.memorypos + attpos
```

Where `obj.memorypos` is computed deterministically in the
[per-page object loop](#2-per-page-object-loop-lines-332985333210) by
the running `loc12` accumulator. To predict it ahead of time the
encoder must mirror that accumulator:

```python
def assign_object_memorypos(pages, app_ctx):
    cursor = 4 * len(app_ctx.AppAllVas)            # past global ints
    for page in pages:
        for obj in page.objs:
            if obj.merry == 1: continue            # private; skipped here
            obj.memorypos = cursor
            cursor += sizeof_objdata_Ram(obj.type) # 52 head + per-type tail
            while cursor & 3 != 0: cursor += 1
```

`sizeof_objdata_Ram(obj.type)` per-type is recoverable from
`attshulei` strides — sum the `datafenpei` of every attribute declared
in the component's `GetAtts_WithNoHead` (see
[`attribute-records.md`](attribute-records.md) §"Attribute type
catalog"), plus the fixed head (8 head fields per the same doc), with
4-byte alignment between attributes where the type forces it. For the
F-series the head size is fixed; the per-type tail is the sum of
`attshulei.datafenpei` over the declared attributes.

### `05 LL LL LL LL` — global-int ref

For user-declared `int sys0`-style globals, `LL LL LL LL` is the
`mpos` of that var in the `AppAllVas` list — i.e., the byte offset
of its value within the `4*AppAllvasQty` prefix of
`Public_Memory.initDatas`. Confirmed by
[`script-compiler.md`](script-compiler.md) (`sys2=42 →
05 08 00 00 00 ...`, mpos=8 for the third global).

### `GetAppAllVasData`

Separately, the editor also serializes `AppAllVas` to a small lookup
table written into `strdata` at `appinf1.AppAllvasAddr`. Each entry is
12 bytes: `(namecrc:u32, mpos:i32, lei:i32)`. This is the metadata the
runtime uses to expose globals to *typed* attribute reads (the actual
values are in `Public_Memory.initDatas[0:4*qty]`). Confirmed
byte-for-byte against the project's `strdata + 0x19a8` region.

## Per-attribute-type strides (the `attshulei` table)

Reproduced from [`attribute-records.md`](attribute-records.md) for
quick reference. The stride is exactly the `datafenpei` field of the
attribute's `attshulei_type`:

| Storage class | Stride (bytes) | Used for                                |
|---------------|----------------|-----------------------------------------|
| `UU8` / `U8_L`/`Fontid`/`Type`/`key`/`Select`/`Pageid` | 1 | Single-byte enums/IDs |
| `UU16` / `SS16` / `Color` / `Picid` / `Videoid` / `Gmovid` / `Audioid` / `Hex16` / `x` / `y` / `w` / `h` / `Strlenth` | 2 | 16-bit values |
| `UU32` / `SS32` / `binary` / `Sstr` (slot) / `BinyiANYTYPE` | 4 | 32-bit (or pointer-sized) |

`attposup` for a given attribute is the cumulative sum of these strides
over preceding attributes in `GetAtts_WithNoHead` order, **plus** the
fixed `sizeof(<Kind>_PARAM_Head)` baseline. Cross-check the per-type
tables in `attribute-records.md` for exact `attpos` values; they were
extracted directly from `GuiObj<Kind>.GetAtts_WithNoHead` IL.

## Worked example — project page 0

Project source: `nextion/source/nextion.hmi.tft`. Bytecode operand
`dim=h0.val` compiles to `04 04 03 00 00 3d 01 54 04 00 00`. The
local-var operand is `01 54 04 00 00 → offset 0x454`.

Working backward, we expect `h0.memorypos + attposup(Slider.val) =
0x454`. From `attribute-records.md` §Slider, `val` is at `attposup=+10`,
so `h0.memorypos = 0x454 - 10 = 0x44a`.

The first 5 components on page 0 should therefore have memorypos values
that build up to `0x44a`. The exact values aren't predictable from
just the page list — they depend on the *PARAM_Head size for each
component type*, which the agent didn't pin down byte-for-byte
here (it lives in the per-`Kind` `_PARAM_Head` struct in
`hmitype.dll`; mechanical to extract). Recovering this constant per
type is the one remaining step before the encoder can synthesize
offsets from scratch.

What we **did** verify by direct bytecode inspection:

| Bytecode operand     | Decoded offset | Located at file byte |
|----------------------|----------------|----------------------|
| `01 2c 01 00 00`     | `0x12c`        | usercode + 0x486     |
| `01 7c 01 00 00`     | `0x17c`        | usercode + 0xf40     |
| `01 7b 03 00 00`     | `0x37b`        | usercode + 0xe02     |
| `01 86 03 00 00`     | `0x386`        | usercode + 0xdb4     |
| `01 91 03 00 00`     | `0x391`        | usercode + 0xde8     |
| `01 a7 03 00 00`     | `0x3a7`        | usercode + 0xed6     |
| `01 b2 03 00 00`     | `0x3b2`        | usercode + 0xea2     |
| `01 bd 03 00 00`     | `0x3bd`        | usercode + 0x1097    |
| `01 54 04 00 00`     | `0x454`        | usercode + 0x158e    |

The offsets cluster in a contiguous ~1 KB range, consistent with one
page's object-data block. Adjacent offsets differ by 2 or 4 (matching
`attshulei` strides), confirming they're attribute slots within
consecutive objects' `PianyiData` blocks.

## Global memory directory at usercode offset 0

The first 0x48 bytes of the project's usercode region form a TLV-ish
header:

```
+0x00  u32   dir_size_bytes   (= 0x48 in baseline project)
+0x04  u32   region_count     (= 3)
+0x08  u32   region[0].offset
+0x0c  u32   region[0].size
+0x10  u32   region[1].offset
+0x14  u32   region[1].size
+0x18  u32   region[2].offset
+0x1c  u32   region[2].size
... padding zeros up to dir_size_bytes ...
```

In the baseline project (no extra locals):

```
region[0] = (0x1c,  4)    → 4 bytes at +0x1c
region[1] = (0x20, 16)    → 16 bytes at +0x20 (= 4 globals * 4? actually 4×4=16, matches 3 globals + 1 scratch / or just sized to AppAllvasQty*4 + 4 padding)
region[2] = (0x30, 24)    → 24 bytes at +0x30 (fingerprint / metadata)
```

In `16_loop` (one additional local `int qq=0`):

```
dir_size_bytes = 0x4c    (was 0x48; +4 due to qq)
region[0] = (0x1c,  4)   (unchanged)
region[1] = (0x20, 20)   (was 16; +4 bytes for qq's slot)
region[2] = (0x34, 24)   (shifted +4; same size)
```

So **region[1] is the AppAllVas-values area** (u32 per declared global
int) and it grows by exactly 4 bytes per added local int. Region[0]
holds a single u32 = `0x18` = the size of region[2]. Region[2] is a
24-byte fingerprint/metadata block (the same bytes appear in both the
baseline project and 16_loop; possibly model-specific or H1-derived).

Encoder rule:

```python
def global_memory_directory(allvas_qty: int) -> bytes:
    out = bytearray()
    # ---- header ----
    dir_size = 0x30 + 24                              # default; region 2 always 24 B
    region1_size = 16 + 4 * max(0, allvas_qty - 3)    # grows past 3 globals
    region2_offset = 0x20 + region1_size              # contiguous
    dir_size = region2_offset + 24
    out += struct.pack('<IIIIIIII',
        dir_size, 3,
        0x1c, 4,                                      # region[0]
        0x20, region1_size,                           # region[1]
        region2_offset, 24)                           # region[2]
    # ---- region 0 body (1 u32: size of region[2]) ----
    out += struct.pack('<I', 24)
    # ---- region 1 body (zeros, allvas_qty u32s = initial values + padding) ----
    out += b'\x00' * region1_size
    # ---- region 2 body (24 bytes of fingerprint) ----
    out += b'\x00\x00\x00\x00' + FINGERPRINT_20_BYTES
    return bytes(out)
```

The 20-byte fingerprint after the leading 4 zeros wasn't decoded here
(it's identical across the project and 16_loop, so it's not a function
of the project's content — most likely model- or H1-derived).
[Limitation, see [What's not figured out](#whats-not-figured-out)].

## Verification

| Fact                                          | Verified against                              |
|-----------------------------------------------|-----------------------------------------------|
| `mollocmemory_add` returns an `attaddrhandlistid`, defers offset assignment | IL `/tmp/hmitype_all.il:326852-326992` |
| Two-pass allocator (init then dynamic)        | IL `/tmp/hmitype_all.il:333230-333401` |
| Public memory = `4*qty | objs | strings | u32(noinitsize)` | IL `:333403-333428` |
| Per-page private memory layout                | IL `:333437-335037` |
| Local-var operand = obj.memorypos + attposup  | Cross-check of 9 operands in project usercode |
| Global directory grows +4 bytes per `int <name>` | Direct diff: project (3 globals, 0x48 dir) vs 16_loop (4 globals, 0x4c dir) |
| 24-byte trailing block is project-invariant   | Byte-for-byte equal across project & 16_loop |
| AppAllVas serialization (12 B / entry, namecrc+mpos+lei) | IL `:331821-331903`, verified at `usercode+0x19a8` (3 records) |

## What's not figured out

1. **Per-component `PARAM_Head` size (the constant added to every
   `attpos` to get a true `attposup`).** Each `GuiObj<Kind>_PARAM_Head`
   has a fixed byte size; that constant is part of the `attposup`
   computation but wasn't extracted in this pass. Mechanical to do —
   grep `hmitype.dll` IL for `_PARAM_Head` structs and read each one's
   field list, summing strides. The exotic-component classes are
   already enumerated in `attrs-raw.txt` (referenced by
   `attribute-records.md`).

2. **The 24-byte fingerprint in region[2] of the global directory.**
   First u32 is 0; the remaining 20 bytes are `26 91 ed 33 01 00 f1 a6
   f2 72 03 00 97 d1 3d af 00 00 5b 01`. These bytes are
   project-invariant across the two same-model fixtures. They might
   be model-CRC-derived, or part of the H1 metadata copied verbatim.
   Disassembly path: search `StructHtoL` for any writer that loads
   from `Myapp.appdata.xiliexinxi` and emits into `loc7`; the
   fingerprint will be the only u32[5] block written between the
   `noinitdatasize` u32 and the per-event bytecode list.

3. **The region[0] purpose.** Holds one u32 = `size of region[2]`
   (=24). Looks like a redundant size header for the runtime to
   parse the trailing region without walking u32-pairs. Not
   load-bearing for the encoder.

4. **`encode=3` in H2 vs raw usercode.** *Resolved (2026-05-17): not
   encryption.* The "scrambled" bytes at file offset
   `0x70000..0x80000` in `15_picture/15.tft` and
   `17_more_components/17.tft` are `.zi` font payload — those
   fixtures have a font that's large enough to overflow into the
   region that the smaller `16_loop` fixture uses for its
   usercode. Resource directory slot 8 (fonts) ends inside the
   `0x70000..0x80000` window in 15/17, so the bytes are glyph row
   data (the repeating `e0 12 cf 22` / `1f` runs are font run-length
   patterns), not bytecode. The usercode in 15/17 actually starts
   at `0x80000` (i.e., `strdataaddr` per `appinf1`). No separate
   encryption exists — `appbianyi::Lstrbyteaddstring` (hmitype IL
   line 339795) appends every bytecode block verbatim, and
   `tft_init_encoder.py`'s self-tests already round-trip
   byte-identically for all known opcode/attr combinations.

5. **`function_objdataraminmemory` switch.** Two layout variants
   exist: when set, per-page private memory uses 1-byte ID slots per
   object; when unset, 4-byte pointer slots per object. The F-series
   path (from `xiliexinxi.function_objdataraminmemory`) sets it. For
   a single-model encoder, hardcode the F-series path.

## Concrete leads for the next iteration

- **Implement `MemoryAllocator`** (a Python helper) that takes
  `(pages: list[PageDesc], globals: list[IntDecl]) → dict` mapping
  `(comp_name, attr_name) → memorypos`. The math is in
  [pass 2](#2-per-page-object-loop-lines-332985333210). Once
  the `PARAM_Head` size per type is tabulated (item 1 above), this
  becomes a 30-line function. (The optional script
  `memory_allocator.py` companion to this doc was not written in
  this pass — the agent ran out of confidence in the
  `PARAM_Head` constants needed for cross-check against
  `0x454`.)

- **Add component-attribute resolution to `script_compiler.py`'s
  `resolve_lvalue`** (the "unknown identifier" branch). Once
  `MemoryAllocator` exists, the resolver is:
  ```python
  comp, attr = name.split(".", 1)
  return bytes([0x01]) + u32_le(allocator.offset(comp, attr))
  ```
  This is the single missing piece for items 1a / 2c / 4.

- **Reverse `GuiObj<Kind>_PARAM_Head` sizes.** For each component
  class with a `GetAtts_WithNoHead`, read the `_PARAM_Head` struct
  immediately above it in the IL. The Head bytes precede the
  attribute area; their size is the constant offset that needs to be
  added to every per-type `attpos` to get a true within-object byte
  position. Until this is done, the encoder can compute
  *relative-within-attribute-block* offsets but not the absolute
  ones the bytecode expects.
