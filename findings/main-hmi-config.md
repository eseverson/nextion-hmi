# `main.HMI` header — full per-display config layout (Lookup 2)

Closes [`format-hmi.md`](format-hmi.md)'s H4 open question — what are
bytes `0x0C..0x60` of the `main.HMI` blob? They are not opaque
per-display config; they are the rest of the editor's `hmifilehead`
struct, fully decoded below. The originating offsets in
[`format-hmi.md`](format-hmi.md#mainhmi-blob-project-manifest) are off
by 8 bytes — the `Modelcrc` field lives at `+0x10`, not `+0x08`.

## Where the registry lives

The model-CRC-keyed registry itself (Prname / Modelstring / resolution /
inch / Flash / LcdMemoryLenth / ComstrBufLenth / HexstrbufLenth / GPU /
roundscreen) is **not** in any managed DLL — it lives in the encrypted
native helper `asp<X>.dll` (where `<X>` is the `xiliemark`, `100` for
the F-series, so `asp100.dll`). The managed code loads it via a
`DllImport` declared on `hmiprmodel.hmiprmodel_LoadFile`:

```csharp
// hmitype.AppData.LoadModels  (IL ~14588-14645)
fixed (void* bytes = Encoding.UTF8.GetBytes(Application.StartupPath + "\\\0")) {
    fixed (void* ptr = …) { fixed (void* ptr3 = …) {
        hmiprmodel.hmiprmodel_LoadFile(
            (byte*)bytes,           // editor install dir
            &hmimodelapptype,       // out: app-level metadata
            ptr2,                   // out: per-series array (hmimodelxilietype[16])
            ptr4);                  // out: per-model array (hmimodeltype[128])
        for (int i = 0; i < hmimodelapptype.XilieQty; i++) {
            …
            for (int k = 0; k < array[i].modelsqty; k++) {
                hmimodeltype* ptr5 = ptr4 + (array[i].modelsstar + k);
                modelxinxi item = new modelxinxi {
                    Prname = AppServer.GetptrtoString((byte*)(&ptr5->Prnamebytes24),  UTF8),
                    Modelstring = AppServer.GetptrtoString((byte*)(&ptr5->modelbytes24), UTF8),
                    resolution = ptr5->resolutionW + "X" + ptr5->resolutionH,
                    inch = AppServer.GetptrtoString((byte*)(&ptr5->inchbytes16), UTF8),
                    Flash = ptr5->FlashSize + "M",
                    LcdMemoryLenth = (int)ptr5->LcdMemoryLenth,
                    ComstrBufLenth = (int)ptr5->ComstrBufLenth,
                    HexstrbufLenth = (int)ptr5->HexstrbufLenth,
                    GPU = ptr5->GPU + "M",
                    Modelcrc = ptr5->modelcrc,
                    roundscreen = ptr5->roundscreen
                };
                if (item.Modelstring.Length > 3 && …) {
                    Appxilies[num].Modes.Add(item);
                }
            }
        }
    }}}
```

The actual file `model<X>.sa` (e.g. `model100.sa` if it existed, or
`model0.sa` for the un-F-series tables) on disk is encrypted and only
decoded by `asp<X>.dll`'s `hmiprmodel_LoadFile`. Static analysis of the
encrypted blob is out of scope for this pass.

**Crucially**, the registry produces an in-memory `modelxinxi` record
whose fields are **NOT written into `main.HMI`**. The only model
reference in `main.HMI` is the `Modelcrc` u32 — everything else
(resolution, GPU size, comstr-buf length, etc.) is consulted at compile
time but feeds into a *different* output: the TFT firmware header `H1`
and the runtime memory pools. `main.HMI` itself only stores the CRC.

## What `main.HMI` bytes 0x0C..0x60 actually are

The full `hmifilehead` struct (96 bytes, ending at `+0x60`):

```csharp
// hmitype.hmifilehead  (IL ~31752-31823)
public struct hmifilehead {
    public uint  crc;                    // +0x00  blob CRC (chained CRC-32/MPEG-2 over the rest)
    public uint  Datasize;               // +0x04  always 0x60 (header size)
    public byte  upver0;                 // +0x08  editor major version stamp
    public byte  upver1;                 // +0x09  editor minor; legacy gate (`upver1 <= 54` ⇒ skip features)
    public byte  filever;                // +0x0a  file-format magic (0x21)
    public byte  xiliemark;              // +0x0b  series mark (0x64 = 100 = F-series)
    public byte  guidire;                // +0x0c  GUI direction / orientation (0..3)
    public byte  encode;                 // +0x0d  text encoding id (3 = utf-8 or gb2312)
    public byte  hmiffid;                // +0x0e  HMI format magic (0x4f)
    public byte  otp;                    // +0x0f  OTP protection flag
    public uint  Modelcrc;               // +0x10  CRC of "Modelstring", e.g. NX4832F035_011 → 0x1ce47603
    public uint  password;               // +0x14  project lock password (0 = none)
    public uint  ResourcesFileTypeAddr;  // +0x18  offset to the resource-type array (= 0x60, immediately after header)
    public uint  ResourcesFileQyt;       // +0x1c  number of resource entries (pages + fonts)
    public int   MemoryFileSystemLenth;  // +0x20  embedded-FS length (0 unless project ships extras)
    public byte  upver2;                 // +0x24  patch-version stamp
    public byte  RAM1OPEN;               // +0x25  1 if RAM1 expansion enabled
    public byte  resourcescancel_font;   // +0x26  flag: omit fonts on download
    public byte  resourcescancel_pic;    // +0x27  flag: omit pictures
    public uint  APPMEDATAHEX0;          // +0x28  6×u32: memory-FS hex metadata (zeroed if no MemoryFS)
    public uint  APPMEDATAHEX1;          // +0x2c
    public uint  APPMEDATAHEX2;          // +0x30
    public uint  APPMEDATAHEX3;          // +0x34
    public uint  APPMEDATAHEX4;          // +0x38
    public uint  APPMEDATAHEX5;          // +0x3c
    public byte  TT_asp100_tc;           // +0x40  asp100 calibration flag (0 unless explicitly set)
    public byte  picencodever;           // +0x41  picture-encoding version (2 in 1.67.x)
    public ushort res1;                  // +0x42  reserved
    public uint  res15;                  // +0x44  7×u32 reserved
    public uint  res16;                  // +0x48
    public uint  res17;                  // +0x4c
    public uint  res18;                  // +0x50
    public uint  res19;                  // +0x54
    public uint  res20;                  // +0x58
    public uint  res21;                  // +0x5c
}                                        // total 0x60 bytes
```

After this fixed 96-byte header, starting at `+0x60`, comes the
resource-type / resource-reference array described in
[`format-hmi.md`](format-hmi.md#mainhmi-blob-project-manifest).

Load site: `hmitype.Myapp_inf.openHMIfile` IL line 347-407, which
re-marshals these bytes into the struct and propagates the fields into
`Myapp.appdata` (orientation, encoding, password, OTP, memory-FS,
RAM1OPEN, asp100 calibration).

## Concrete bytes for `NX4832F035_011`

Verified against `nextion/source/nextion.hmi.HMI` and
`nextion/tests/editor outputs/17_more_components/17.HMI`. The model
CRC `0x1ce47603` lives at `+0x10`.

`nextion/source/nextion.hmi.HMI`, full 96-byte header:

```
+0x00  94 a2 4e e0  60 00 00 00  01 43 21 64  01 03 4f 00
+0x10  03 76 e4 1c  00 00 00 00  60 00 00 00  06 00 00 00
+0x20  00 00 00 00  01 00 00 00  00 00 00 00  00 00 00 00
+0x30  00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
+0x40  00 02 00 00  00 00 00 00  00 00 00 00  00 00 00 00
+0x50  00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
```

Decoded fields:

| Offset | Field            | Value          |
|--------|------------------|----------------|
| 0x00   | crc              | 0xe04ea294     |
| 0x04   | Datasize         | 0x60 = 96      |
| 0x08   | upver0           | 0x01           |
| 0x09   | upver1           | 0x43 = 67      |
| 0x0a   | filever          | 0x21           |
| 0x0b   | xiliemark        | 0x64 = 100 (F-series) |
| 0x0c   | guidire          | 0x01 (orientation 0°) |
| 0x0d   | encode           | 0x03           |
| 0x0e   | hmiffid          | 0x4f           |
| 0x0f   | otp              | 0x00           |
| 0x10   | Modelcrc         | 0x1ce47603 ← NX4832F035_011 |
| 0x14   | password         | 0              |
| 0x18   | ResourcesFileTypeAddr | 0x60      |
| 0x1c   | ResourcesFileQyt | 6              |
| 0x20   | MemoryFileSystemLenth | 0         |
| 0x24   | upver2           | 0x01           |
| 0x25   | RAM1OPEN         | 0              |
| 0x26   | resourcescancel_font | 0          |
| 0x27   | resourcescancel_pic  | 0          |
| 0x28..0x3f | APPMEDATAHEX0..5 | all 0      |
| 0x40   | TT_asp100_tc     | 0              |
| 0x41   | picencodever     | 0x02           |
| 0x42   | res1             | 0              |
| 0x44..0x5f | res15..21    | all 0          |

`17.HMI` differs only in `crc` (0x52f7396e), `upver1` (0x44 → editor v1.67.2
vs 0x43 → v1.67.1), `ResourcesFileQyt` (8 instead of 6), and the
crc-dependent leading bytes. Every other field matches the table above
for any NX4832F035_011 project on the same editor major version.

## Authoring template for a new `main.HMI`

For a single-model authoring path on NX4832F035_011, every byte
between `+0x14` and `+0x60` is deterministic given:

1. `password = 0` (no project password)
2. `ResourcesFileTypeAddr = 0x60` (immediately after the header)
3. `ResourcesFileQyt` set to the count of pages+fonts in the project
4. `MemoryFileSystemLenth = 0` (no embedded FS)
5. `upver2 = 1`, `RAM1OPEN = 0`, both `resourcescancel_*` = 0
6. `APPMEDATAHEX0..5 = 0`
7. `TT_asp100_tc = 0`, `picencodever = 2` (1.67.x default)
8. `res1 = 0`, `res15..21 = 0`

So the encoder can fill the first 96 bytes from just three inputs:
`Modelcrc`, `ResourcesFileQyt`, and the editor version (encoded as
`upver0/upver1/upver2`). The trailing `crc` (and the four directory
tail bytes H2 in [`format-hmi.md`](format-hmi.md)) is recomputed after
the body is finalised — that's the chained CRC documented in
[`format-hmi.md`](format-hmi.md#page-crc-algorithm), almost certainly
applied to the `main.HMI` body the same way it's applied to pages
(open question H5).

## Multi-model authoring

The full per-model dynamic state (resolution, ComstrBuf, HexstrBuf, GPU,
LcdMemory, Flash, inch, roundscreen) is consulted at compile time but
never serialised into `main.HMI`. The only model-dependent byte in
the header is the `Modelcrc` u32 at `+0x10`. Resolution and orientation
flow into the **TFT file header `H1`** (see
[`format-hmi.md`](format-hmi.md#coordinate-encoding-under-rotation)), so
to author for a different model you only need to:

1. Compute the new `Modelcrc` from the model name string via
   `AppServer.Getcrc(name.GetbytesssASCII())` — the standard
   CRC-32/MPEG-2 from [`format-hmi.md`](format-hmi.md#page-crc-algorithm).
2. Write that CRC to `main.HMI[+0x10]`.
3. Use the corresponding resolution / orientation in the TFT H1 header.

The rest of `main.HMI` is identical across F-series models. **No
52-byte per-display blob to copy** — the bytes between the Modelcrc and
the resource array are common-format header, fully decoded above.

## References

- `hmitype.hmifilehead` struct — IL line 31752-31823
- `hmitype.AppData.LoadModels` — IL line 14588-14645 (registry load
  via `asp<X>.dll`'s `hmiprmodel_LoadFile`)
- `hmitype.modelxinxi` struct (in-memory record) — IL line 13874-13897
- `hmitype.Myapp_inf.openHMIfile` — IL line 347-407 (consumes header,
  pushes fields into `Myapp.appdata`)
- Encrypted on-disk registry — `<EditorInstall>/model<X>.sa` decoded by
  `asp<X>.dll!hmiprmodel_LoadFile` (DLL itself encrypted with the same
  ACTR-style wrapper as the other PEs, decryption unverified here)
