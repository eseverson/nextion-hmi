# HMI directory checksum

A 4-byte checksum stored immediately after the HMI top-level directory
entries (at file offset `4 + count × 28`) and mirrored at the backup
directory at `0x80000 + 4 + count × 28`. Validated by the editor's
load path; any directory edit that doesn't recompute it triggers
"wrong resource file or resource file has been damaged".

This finding closes the last blocker for HMI-side authoring. Without
it, neither relocating an existing `.pa` blob nor adding a new
directory entry produces a file the editor will open.

## Algorithm

```python
checksum = CRC32_T(seed=0xFFFFFFFF, data = directory_bytes ++ b"ADEC")
```

- `directory_bytes` is the contiguous bytes of `(u32 count) +
  (count × 28-byte entries)` — i.e. exactly the bytes from file
  offset `0` up to the checksum's storage position.
- `"ADEC"` (`0x41 0x44 0x45 0x43`) is a fixed 4-byte sentinel baked
  into `achmi.dll` at `.rdata:0x1001dbc4`.
- `CRC32_T` is the 4-byte-block variant of the achmi CRC mixing
  kernel:

  ```
  r = seed
  for each LE u32 word in data:
      r ^= word
      for _ in range(4):
          r = (r << 8) ^ TAB[(r >> 24) & 0xff]   # CRC-32/MPEG-2 table
  return r
  ```

  Same polynomial and table as the byte-wise `crc32_bytewise` used by
  the page CRC; the only difference is that each iteration XORs a
  full dword rather than a single byte. Native impl at
  `achmi.dll!.text:0x10007990`. Python port:
  [`scripts/lib/page_crc.crc32_T`](../scripts/lib/page_crc.py).

Wrapper [`directory_checksum(directory_bytes) -> int`](../scripts/lib/page_crc.py)
hides the sentinel append.

## Where it's checked

`achmi.dll!CFSOpenSystem` (subcmd `0x00`, native VA `0x10002b50`) is
the editor's entry point for opening any `.HMI` file. It:

1. Opens the file (`fopen`-equivalent via the import at `ds:0x10579c90`
   / `ds:0x10579c94`).
2. Verifies the `"ver21234"` magic at file offset `0x6FFFF8`
   (from `.rdata:0x1001dbcc`).
3. Calls `0x10002580` (load+verify directory), which in turn calls
   `0x10002250` **twice** — the directory checksum compute/verify
   routine.

`0x10002250`'s essential structure (with `edx`/mode = 0, which is what
`CFSOpenSystem` passes):

```
CRC32_T(seed=0xFFFFFFFF, data=count_u32, len=4)   # the +0..+4 count field
CRC32_T(crc, data=entries, len=count*28)          # all entries (when mode != 0)
CRC32_T(crc, data="ADEC", len=4)                  # the sentinel
... then reads from file at 0x80000 or 0x380000 ...
```

For the editor's open path the equivalent end-to-end is the single
`CRC32_T(0xFFFFFFFF, count_u32 + entries + "ADEC")` formula above.

## Verification

Verified against three independent fixtures:

| Fixture                             | count | stored checksum | recomputed |
|-------------------------------------|-------|-----------------|------------|
| `00_baseline/base.HMI`              | 16    | `0x7fbe4e51`    | match      |
| `07_add_hotspot/07.HMI`             | 13    | `0xc477e9a7`    | match      |
| `source/nextion.hmi.HMI` (miata)    | 22    | `0x1ec19017`    | match      |

## Impact on the authoring path

Any tool that mutates the HMI directory (relocates an existing entry,
adds a new entry, tombstones a live one, changes a `start`/`size`/`name`
field) **must** recompute this checksum and write it at both
`4 + count × 28` and `0x80000 + 4 + count × 28`. Failing to do so causes
the editor to reject the file on load.

Used in [`scripts/tools/add_hotspot.py`](../scripts/tools/add_hotspot.py),
which now updates both the primary and backup checksums after editing
the `0.pa` directory entry.

## Open follow-ups

- **Trailing bytes after the checksum**: in `nextion.hmi.HMI` the
  bytes immediately after the 4-byte checksum continue to be non-zero
  (`80 c6 b2 01 4b 50 00 00`). Their format isn't yet decoded; they
  may be a separate timestamp/build counter that the editor accepts
  but also rewrites on save. Not blocking — the editor opens the file
  with these untouched once the primary checksum is valid (to be
  confirmed once the corrected `add_hotspot.py` output is editor-tested).
- **`CFSOpenSystem` mode=2 path**: when the file is opened for write
  rather than read, it does an extra check at offset `0x6FFFF8` (the
  `"ver21234"` magic). Not relevant to read-only validation but
  needed for write-mode opens.
- **The `0x380000` sentinel**: `CFSOpenSystem` reads 4 bytes at
  `0x380000` along the directory-validation path (case `count >
  0x3a98`); the value's role and whether it gates anything is open.
