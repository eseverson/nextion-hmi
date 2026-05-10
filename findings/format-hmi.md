# HMI file format

The `.HMI` file is the Nextion editor's project source format. It is a
single-file image with a primary directory, a mirrored backup
directory, a reserved sentinel sector, and an append-only data area.

## File-level layout

```
0x00000000   primary directory       (count u32 + count * 28-byte entries)
0x00000270   zero padding
0x00080000   backup directory        (verbatim copy of primary)
0x00080270   zero padding
0x00380000   4-byte sentinel 0xFFFFFFFF
0x00380004   zero padding
0x006FFFF8   ASCII magic 'ver21234'  (8 bytes, immediately before data area)
0x00700000   start of entry data blobs (run to EOF)
```

Three 512 KB-aligned regions reserve space for primary directory,
backup directory, and the sentinel slot. All real content lives in the
final region from `0x00700000` onward.

The backup directory at `0x00080000` is a byte-identical mirror of the
primary. Likely wear-levelling redundancy if the file came from an
internal flash image; either copy is authoritative on disk.

## Primary directory

```
+0x00   u32   count             # number of entries
+0x04   entry[count]            # 28 bytes each
```

## Directory entry (28 bytes)

```
+0x00   16s   name              # null-padded ASCII (see "Stale names" below)
+0x10   u32   start             # absolute file offset of the data blob
+0x14   u32   size              # length of the data blob in bytes
+0x18   u8    deleted           # 0 = live, 1 = tombstoned
+0x19   u8    tail0             # for pages/Program.s: (size>>8)&0xff
+0x1A   u8    tail1             # for pages/Program.s: (size>>16)&0xff
+0x1B   u8    tail2             # for pages/Program.s: (size>>24)&0xff
                                # For fonts and main.HMI these three bytes
                                # carry different (un-decoded) data.
```

Stale names: when an entry is tombstoned (`deleted=1`), `name` is not
zeroed. It contains whatever was in the buffer when the slot was last
re-initialised, so deleted entries often show fragmentary names. Treat
`name` as meaningful only when `deleted=0`.

## Append-only journal

The HMI writer never rewrites in place: a save appends a new blob and
flips the old entry's `deleted` byte. `start`/`size` of tombstoned
entries still point at valid parseable data. Earlier versions of pages
and scripts remain fully recoverable from a single `.HMI` file.

Tombstones accumulate per save until the editor performs occasional
compaction. The trigger condition for compaction is unconfirmed; in one
6-save sequence with no content changes, no compaction occurred for 5
saves and exactly one tombstoned `0.pa` was reclaimed on save 6.

Saves with no content changes produce **byte-identical HMI output** —
the compilation pipeline is deterministic.

## main.HMI blob (project manifest)

The directory entry named `main.HMI` is a small project-level manifest:

```
+0x00   u32   blob crc/hash
+0x04   u32   ?                    (observed: 0x60 — header_size?)
+0x08   u32   model-id CRC         (e.g. 0x1ce47603 = NX4832F035_011)
+0x0C   bytes ?                    (per-display config; not decoded)
+0x60   ref[N] of (8 bytes ext, 8 bytes name)
                                   each entry has the form
                                   { "zi" or "pa" left-padded with NULs } +
                                   { "N.zi" or "N.pa" left-padded with NULs }
```

The trailing reference array lists every resource the project declares
(fonts and pages, by stem). Order is the declaration order, not the
directory order.

## Page blob (`*.pa`)

```
+0x00   u32   crc                  # see "Page CRC algorithm" below
+0x04   u32   datasize             # total payload size
+0x08   u32   datainformationaddr  # always 0x38
+0x0c   u32   datainformationqyt   # number of dataentry rows (= numberobj)
+0x10   u32   lockpassword
+0x14   u8    pagelock
+0x15   u8    hmiffid              # editor's page-format magic (0x4f)
+0x16   u8    filever              # editor's page-format magic (0x21)
+0x17   u8    pagelei
+0x18   16s   name                 # ASCII page name
+0x28   u8    upver0
+0x29   u8    upver1
+0x2a   u8    upver2
+0x2b   u8    res1
+0x2c   u32   res2
+0x30   u32   res3
+0x34   u32   res4
+0x38   PageContentHeader[numberobj]
+0x38 + 12*numberobj   per-component data
```

`PageContentHeader` is `<III>` (12 bytes): `startOffset, size, ?`.
Inside each component, plain-text substrings such as `att-NN`,
`codesload-N`, `codesup-N`, `codesdown-N`, `codestimer-NN`,
`codesunload-N`, `codesslide-N` are sub-record names — they are not
top-level directory entries.

### Page CRC algorithm

The leading `crc` u32 is a **five-segment chained CRC-32/MPEG-2** with
a four-rounds-per-byte mixing kernel. Before the CRC is computed, the
header is patched in place: `[0x15..0x17] = 0x214f` (the page-format
magic) and `[0x1a]/[0x1c]/[0x1d] = upver0/upver2/upver1` from an
editor-managed state block. Any roundtrip writer must apply the same
patches.

```python
TAB256 = CRC32_MPEG2_TABLE  # standard polynomial 0x04C11DB7, MSB-first

def crc32_bytewise(seed, data):
    r = seed & 0xFFFFFFFF
    for b in data:
        r ^= b                                       # XOR into low byte
        for _ in range(4):                           # 4 rounds per byte
            r = ((r << 8) & 0xFFFFFFFF) ^ TAB256[r >> 24]
    return r

def page_crc(page_bytes):
    crc = crc32_bytewise(0xFFFFFFFF, page_bytes[4:])             # body
    crc = crc32_bytewise(crc,         page_bytes[4:8])            # datasize
    crc = crc32_bytewise(crc,         page_bytes[0xc:0x10])       # datainformationqyt
    crc = crc32_bytewise(crc,         page_bytes[0x14:0x15])      # pagelock
    crc = crc32_bytewise(crc,         page_bytes[0x15:0x16])      # hmiffid (0x4f)
    return crc
```

The four trailing micro-runs re-mix bytes already covered by the body
sweep — a deliberate redundant chain that defeats brute-forcing
textbook CRC-32 variants. Reference implementation:
[`scripts/page_crc.py`](../scripts/page_crc.py).

Open question: whether `Program.s`, `*.zi`, and `main.HMI` use the same
algorithm with different segment chains (their entries also have a
leading u32 that looks CRC-shaped).

## Component records inside `*.pa`

Field byte locations within a page payload, pinpointed by
known-plaintext experiments:

| Attribute    | Component | Location method                          |
|--------------|-----------|------------------------------------------|
| `val` (u32)  | Variable  | u32 LE at the variable's record         |
| `txt` (str)  | Text      | ASCII, prefixed by colour/flag bytes    |
| `bco` (u16)  | any       | RGB565 LE inside attribute record       |

The full attribute-record schema (mapping attribute IDs to byte
positions) has been pinpointed via several known-plaintext experiments
but not yet fully tabulated. See
[`experiments.md`](experiments.md) for the queued experiments that fill
in remaining attribute positions.

Component overhead (an empty Hotspot added to a page) is **+516 bytes**:
every component has a fixed-size attribute record plus per-event slot
allocation, even when no events are bound.

## Coordinate encoding under rotation

Page-level orientation lives at H1+0x14 of the TFT (not in the HMI
itself); the editor consults it during compile. Three observed values:

| H1+0x14 | Orientation                |
|---------|----------------------------|
| `0x01`  | 0° (original)              |
| `0x00`  | 90°                        |
| `0x03`  | 180°                       |
| `0x02`  | 270° (predicted, untested) |

180° rotation is runtime-applied — the editor preserves the user code
and only flips the display at render time. 90°/270° rotation rebakes
component coordinates to the new screen aspect ratio (literal x/y/w/h
values change in the compiled output).

## Resource section growth

The HMI grows in **0x10238-byte chunks** (64 KB + 568 bytes) when a
structural change exhausts the current data sector. Adding a Hotspot,
deleting a component, and adding an empty page all produce the same
sector-sized growth — consistent with the file being a dump of a
flash-image filesystem with 64 KB pre-allocation.

Adding an empty page grows the TFT by only **+1180 bytes**, so a page's
HMI-side footprint is dominated by directory + manifest growth rather
than the compiled page itself.

## Open structural questions

- **H2 (directory tail bytes)**: 3 trailing bytes of font and main.HMI
  entries don't follow the `size>>8` pattern that pages and Program.s
  use. Possibly a content hash, timestamp, or wider field overlap.
- **H3 (0x380000 sentinel)**: purpose of the lone `0xFFFFFFFF` at file
  offset `0x380000`. Hypothesis: marker for a 512KB-aligned filesystem
  sector. Unverified.
- **H4 (main.HMI bytes 0x0C..0x60)**: per-display config block; blocked
  on access to a second hardware model.
- **H5 (Program.s / .zi / main.HMI CRCs)**: whether the same chained
  CRC family applies, just with different segment chains.
- **H7 (PageHeader+0x15 / +0x17)**: known to be page-format magic
  (`0x4f` and `0x21` respectively, per the page CRC pre-patch). Why
  they're stored per-page rather than once per file is unclear.
- **H13/H14 (new-page id allocation)**: whether the editor uses
  lowest-free vs. `len(pages)` for new page IDs; whether main.HMI's
  reference array grows on add-page.
- **H16 (editor version field location)**: still unknown; would require
  saves from a different editor version on the same project.

See [`next-steps.md`](next-steps.md) for which of these unblock
authoring from scratch.
