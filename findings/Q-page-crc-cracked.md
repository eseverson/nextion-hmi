# Q — Page CRC algorithm cracked

Date: 2026-05-10. Resolves [G-research-roadmap.md](G-research-roadmap.md) row
H1 (Page CRC algorithm). Brute force against 384 textbook CRC-32 variants ×
9 payload ranges had failed (`scripts/crack_page_crc.py`); the algorithm is
**not** a stock byte-wise CRC. It's a five-segment chained CRC-32/MPEG-2
that re-runs four header fields through the register *after* the body sweep.
The chain is implemented in the editor's bundled native `achmi.dll`, not in
managed code, which is why decompiling `hmitype.dll` alone wasn't enough.

## How the CRC was located

1. `hmitype.Myapp_inf::OutPutPageFile` writes a 56-byte
   `hmipagehead` (crc=0) followed by `datainformation` records and
   per-component blobs, then hands the in-memory file pointer to
   `achmiface.HmiSafe::HmiSafeWritePageFileSafe`.
2. `achmiface.HmiSafe::HmiSafeWritePageFileSafe` is a thin shim over
   `ControlInterFace.ControlInterCenter.CallControl02_(value0, value1,
   comid=0x10027)` — a managed dispatcher that calls
   `LibraryCallS[1].CallControl02(value0, value1, comid & 0xFFFF)`.
3. `LibraryCallS[1]` is populated from the native `achmi.dll`. Its
   `CallControl02` is a 200-entry function table at `achmi.dll!.rdata:0x1ca28`
   indexed by `comid & 0xFFFF`. Subcommand `0x27` is the page-CRC writer at
   `achmi.dll!.text:0x100058a0`.
4. `achmi.dll` itself is *embedded* inside the (decrypted) managed
   `achmiface.dll` as a resource (`achmiface.Properties.Resources.achmi`,
   ~120KB PE32). It exports `GetComIn` and `SetCallControl`. The
   editor extracts it to `%APPDATA%\Nextion Editor\achmi.bin` on first
   run if no on-disk copy exists.

## The algorithm

Page blob layout (matches the existing `hmipagehead` struct):

```
+0x00  u32   crc                   ← what we're computing
+0x04  u32   datasize               (size of the whole blob)
+0x08  u32   datainformationaddr    (always 0x38 for current files)
+0x0c  u32   datainformationqyt     (number of dataentry rows)
+0x10  u32   lockpassword
+0x14  u8    pagelock
+0x15  u8    hmiffid                (the editor's `0x4f` magic)
+0x16  u8    filever                (the `0x21` magic)
+0x17  u8    pagelei
+0x18  16s   name                   (ASCII page name, null-padded)
+0x28  u8    upver0
+0x29  u8    upver1
+0x2a  u8    upver2
+0x2b  u8    res1
+0x2c  u32   res2
+0x30  u32   res3
+0x34  u32   res4
```

Before computing the CRC, the native handler **patches** four bytes of the
header in place and rewrites them:

* `[0x15..0x17] = 0x214f` (i.e. `hmiffid=0x4f`, `filever=0x21`) — the
  editor's page-format magic.
* `[0x1a] = upver0`, `[0x1c] = upver2`, `[0x1d] = upver1` — pulled from
  global bytes at `achmi.dll!.data:0x1001f400/401/402`. These overwrite
  bytes inside the `name` field, so any roundtrip writer must mirror the
  editor's upver state to produce a byte-identical save.

Then the CRC is computed by chaining `CRC32(reg, slice)` — the byte-wise
Nextion variant — over five spans:

```
crc = CRC32(0xFFFFFFFF, page[4 : filesize])    # body sweep
crc = CRC32(crc,        page[4 : 8])           # datasize
crc = CRC32(crc,        page[0xc : 0x10])      # datainformationqyt
crc = CRC32(crc,        page[0x14 : 0x15])     # pagelock (1 byte)
crc = CRC32(crc,        page[0x15 : 0x16])     # hmiffid  (1 byte)
                                               #   (== 0x4f)
```

Stored at `page[0:4]` little-endian.

The four trailing micro-runs cover bytes the body sweep already passed
through, so they're effectively a four-step extra mixing of those exact
bytes against the running register — likely a deliberate "weight" on the
header fields the editor cares about validating most. The implementation
detail is unusual but not cryptographic; it's just a redundant chain.

## The byte-wise CRC

The lookup table `tab256` is the standard CRC-32/MPEG-2 table —
polynomial `0x04C11DB7`, MSB-first, no reflection. The same 1024-byte
blob lives in `hmitype.dll!.text` (as a `<PrivateImplementationDetails>`
field, initialised in `AppServer.cctor`) and in `achmi.dll!.rdata:0x1d760`,
byte-identical.

Where the byte-wise routine `CRC32` differs from a textbook table CRC is
**how each byte is mixed in**: instead of one round per byte, it does
four:

```python
def crc32_bytewise(seed, data):
    r = seed & 0xFFFFFFFF
    for byte in data:
        r ^= byte                                  # XOR into low byte
        for _ in range(4):
            r = ((r << 8) & 0xFFFFFFFF) ^ TAB256[r >> 24]
    return r
```

The XOR puts the byte into the LSB; four shift+lookup rounds then mix it
all the way to the high byte and back. This is *not* equivalent to any
of the textbook CRC-32 variants (which is why standard CRC libraries
miss it), but it is fully linear and the differential cancellation
property still holds — the algorithm just isn't in any reference list.

There is also a `CRC32_T` / word-wise variant (4-byte blocks, with one
mixing pass per dword) used elsewhere in the runtime; it's not what the
page CRC uses.

## Verification

`scripts/page_crc.py` contains a 60-line standalone implementation. Self-test
against `tests/editor outputs/00_baseline/base.HMI` matches all four live
pages:

```
[OK] # 0 3.pa     size=0x050a  stored=0xc867af1e  computed=0xc867af1e
[OK] # 2 2.pa     size=0x0c0c  stored=0x858469f7  computed=0x858469f7
[OK] # 9 0.pa     size=0x566b  stored=0xdd4ba010  computed=0xdd4ba010
[OK] #12 1.pa     size=0x127c  stored=0x6e65438b  computed=0x6e65438b
```

Also verified against `04_red_val_deadbeef/04.HMI` (the experiment-04
clean-substrate page that flips one variable's `val` to `0xdeadbeef`):

```
stored CRC = 0x2c26c5f8
computed   = 0x2c26c5f8  (match)
```

## Roadmap impact

* **H1 — RESOLVED.** No more "tabling pending deeper RE."
* **K-editor-binary-re.md** can drop the "still hunting page CRC" caveat.
* The simulator can now emit byte-identical `*.pa` payloads if it ever
  becomes a writer (currently read-only).
* The same byte-wise CRC routine is reused for runtime checks in
  `hmitype.MiddleFileSystem` (page header in MidFile) and for the
  per-component object-name lookup tables. Knowing the algorithm
  retroactively explains a few "what is this 4-byte magic" questions
  scattered across A/B/C.
* The native `achmi.dll` extraction proves the editor's "managed-only"
  story is wrong; the same approach can crack T1 (F-series H2 transform)
  if it lives in achmi.dll's other 199 subcommands. That's the next
  obvious move on the roadmap.
