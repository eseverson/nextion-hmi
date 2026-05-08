# Path A — Nextion HMI directory format

Source under study: `nextion.hmi.HMI` (7,505,535 bytes, NX4832F035_011 / "Nextion 3.5\" Discovery 320x480").

Cross-referenced against `tools/Nextion2Text/Nextion2Text.py` (`HMIHeader`,
`HMIContentHeader`, `PageHeader` classes) and `tools/nxt-doc/`. nxt-doc's
`File Formats/` directory contains only `TFT.md`; the HMI format is
undocumented there. This note extends/corrects what Nextion2Text shows.

## File-level layout

```
0x00000000  primary directory       (count + 22*28-byte entries; 624 bytes used)
0x00000270  zero padding
0x00080000  backup directory        (verbatim copy of primary; 624 bytes)
0x00080270  zero padding
0x00380000  4-byte sentinel 0xFFFFFFFF
0x00380004  zero padding
0x006FFF00  zero padding
0x006FFFF8  ASCII magic 'ver21234'  (8 bytes, immediately before data area)
0x00700000  start of entry data blobs (run to EOF at 0x0072867f)
```

The whole-file scan finds exactly four non-zero regions, matching the
above. Treat 0x00000000-0x00080000, 0x00080000-0x00380000,
0x00380000-0x00700000 as a coarse three-region partitioning of the file
header reservation (likely 512KB-aligned sectors of an internal
filesystem image), with all real content packed into the final region
starting at 0x00700000.

## Directory header

```
+0x00  u32  count          # number of entries (this file: 22)
+0x04  entry[count]        # 28 bytes each
```

The directory header is mirrored at offset `0x00080000` byte-for-byte.
This file: 620 bytes (4 + 22*28) of meaningful data, both copies match
exactly. Likely a redundancy / wear-levelling backup. Nextion2Text only
reads the primary copy; it does not check the backup or compare them.

## Directory entry (28 bytes)

Best-fit struct, partially confirmed against Nextion2Text's
`HMIContentHeader._headerFormat = "<16sII?bbb"` (28 bytes):

```
+0x00  16s  name           # null-padded ASCII; for tombstoned entries the
                           # bytes are stale/leftover, not zeroed
+0x10  u32  start          # absolute file offset of the data blob
+0x14  u32  size           # length of the data blob in bytes
+0x18  u8   deleted        # 0 = live entry, 1 = tombstoned
+0x19  u8   tail0          # for live page/Program.s entries: (size>>8)&0xff
+0x1A  u8   tail1          # for live page/Program.s entries: (size>>16)&0xff
+0x1B  u8   tail2          # for live page/Program.s entries: (size>>24)&0xff
                           # For fonts and main.HMI these three bytes carry
                           # different (currently un-decoded) data; possibly
                           # a content hash truncation or a parallel field
                           # from a different schema version. Nextion2Text
                           # parses these as `bbb` but never assigns them.
```

This file's full directory (live entries):

| #  | name      | start       | size      | tail (b0,b1,b2) | content type |
|----|-----------|-------------|-----------|-----------------|--------------|
| 0  | 3.pa      | 0x00700000  | 0x00050a  | (5,0,0)         | page "error" |
| 1  | main.HMI  | 0x0070050a  | 0x0000c0  | (213,83,0)      | global metadata |
| 2  | Program.s | 0x007005ca  | 0x0002a7  | (2,0,0)         | global script |
| 7  | 2.pa      | 0x00700e8a  | 0x00050a  | (5,0,0)         | page "gauge" |
| 11 | 0.zi      | 0x007020c6  | 0x00364b  | (152,236,29)    | font "liberiso-8859-1" |
| 13 | 1.pa      | 0x00706778  | 0x001067  | (16,0,0)        | page "settings" |
| 17 | 1.zi      | 0x0070b1aa  | 0x0090ce  | (151,56,119)    | font "liber-48iso-8859-1" |
| 20 | 0.pa      | 0x0071ef4d  | 0x00566b  | (86,0,0)        | page "main" |

The other 14 entries are tombstoned (`deleted == 1`). All of their
`start/size` values still point at valid, parseable data — see
"Tombstones" below.

## Tombstones / append-only journal

The 14 deleted entries fall into version clusters. For each live entry of
non-trivial size there are 0..3 older copies in the file, all flagged
`deleted=1` and all left intact:

| live entry      | older copies (deleted) | notes |
|-----------------|------------------------|-------|
| main.HMI (#1)   | #3, #5                 | three identical 192-byte blobs |
| Program.s (#2)  | #6                     | older, longer draft (0x2f6 vs 0x2a7 bytes) |
| 1.pa (#13)      | #9, #12, #15           | sizes 0xc8e, 0x1067, 0xc8e — last save extended the page |
| 0.pa (#20)      | #18, #19, #21          | three older 0.pa snapshots; #19 differs from #20 in only 7 bytes (the leading CRC) |

Diff between live 0.pa (#20) and deleted 0.pa (#19): 7 bytes out of
22,123 — confirming the deleted entry is a previous save of the same
page, not unrelated junk.

This means the Nextion editor's HMI writer is **append-only at the data
area**: a save never rewrites in place, it appends a new copy and flips
the old entry's `deleted` byte. The data area is compacted only when the
editor performs an explicit save-as / clean. **For an undo-history
extractor this is a goldmine — earlier versions of pages and scripts
remain fully recoverable from a single .HMI file.** Nextion2Text's
loader silently filters tombstoned entries (`if obj:` calls
`__bool__ = not deleted`) so it never surfaces this.

The name field of a tombstoned entry is **not** cleared. It holds
whatever bytes happened to be in the buffer when the slot was last
re-initialised — which means stale fragments of earlier names show up
(e.g., `\x00ain.HMI`, `\x00.pa`, `\x00ÊS\x00...`). Treat the name as
meaningless when `deleted == 1`.

## main.HMI blob layout (192 bytes, this file)

This entry is referenced by Nextion2Text but the Python code only pulls
the model-id CRC out of it. Full layout for this file:

```
+0x00  u32   blob crc/hash         (here: 0xe04ea294)
+0x04  u32   ?                     (here: 0x60)
+0x08  u32   model-id CRC          (here: 0x1ce47603 = NX4832F035_011)
+0x0C  bytes ?                     (per-display config; not decoded)
+0x60  ref[6] of (8 bytes ext, 8 bytes name)
                                   each entry has the form
                                   {"zi" or "pa" left-padded with NULs} +
                                   {"N.zi" or "N.pa" left-padded with NULs}
                                   listing the resources referenced by this
                                   project. For this file:
                                     "zi" / "0.zi"
                                     "zi" / "1.zi"
                                     "pa" / "0.pa"
                                     "pa" / "2.pa"
                                     "pa" / "1.pa"
                                     "pa" / "3.pa"
```

i.e., main.HMI is the project's **resource manifest**: tells you which
fonts and pages exist and the order they're declared. The order in this
manifest does **not** match the directory order (manifest is 0,1 fonts /
0,2,1,3 pages; directory order is 3,main,Program,2,0font,1,1font,0).

## Page payload (`*.pa`) — already documented by Nextion2Text

`PageHeader._headerFormat = "<IIIII?bbb16s16b"`, 56 bytes. Verified for
all four live pages in this file:

```
+0x00  u32    crc of the rest of the blob
+0x04  u32    datasize  == directory entry size
+0x08  u32    datainfoaddr — always 0x38 (= sizeof(PageHeader)) here
+0x0C  u32    numberobj   — 2, 30, 2, 5 for the four pages
+0x10  u32    password
+0x14  u8     locked
+0x15  u8     ?
+0x16  u8     fileVersion
+0x17  u8     ?
+0x18  16s    page name   (e.g. "main", "gauge", "error", "settings")
+0x28  16b    reserved
```

After the header, `numberobj` `PageContentHeader` records (`<III` = 12
bytes each: startOffset, size, ?), and the components themselves. Inside
each component the visible substrings (`att-NN`, `codesload-N`,
`codesup-N`, `codesdown-N`, `codestimer-NN`, `codesunload-N`,
`codesslide-N`) are sub-record names; they are **not** top-level
directory entries. Total occurrences in this file:
`att-NN`=148, `codesup-N`=122, `codesdown-N`=121, `codesload-N`=9,
`codesunload-N`=9, `codestimer-N`=4, `codesslide-N`=4. All four pages'
component data lies inside their parent `*.pa` blob.

## Open questions / TBD

1. The 3 trailing bytes of the entry (currently named `tail0/1/2`) for
   font and main.HMI entries do not follow the `size >> 8` pattern that
   pages and Program.s use. They might encode a checksum, a
   creation/modification timestamp, or be an artefact of the editor
   writing a wider field that overlaps the next entry's name. Worth
   diffing against a freshly-edited HMI to see which bytes change.

2. The 4-byte `0xFFFFFFFF` sentinel at 0x00380000 has no obvious purpose
   in a single-file capture. Hypothesis: the .HMI file is a dump of an
   internal flash filesystem with three reserved sector-aligned slots
   (primary dir / backup dir / sentinel) at 0, 0x80000, 0x380000, with
   the user data confined to the last sector. Confirming would require
   another HMI file from a different model.

3. The first `u32` of each page blob is documented as a CRC by
   Nextion2Text but the algorithm is not specified. Trying CRC32 over
   the rest of the blob does **not** match (verified for all four pages
   in this file). Likely a Nextion-specific polynomial or a different
   payload range.

4. The `?` byte at PageHeader+0x15 and +0x17 is consistently non-zero
   in this file (0x4f / 0x21 etc.) and varies per page — possibly a
   page kind / palette index. Worth correlating against page type when a
   second sample is available.
