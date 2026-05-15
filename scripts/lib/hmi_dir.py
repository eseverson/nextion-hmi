#!/usr/bin/env python3
"""
hmi_dir.py - parse the directory at the start of a Nextion .HMI source file
and produce a structured dump.

Layout (deduced from nextion.hmi.HMI; partly cross-referenced with
MMMZZZZ/Nextion2Text's HMIContentHeader struct):

  +0x00  u32  count                # number of directory entries
  +0x04  entry[count]               # 28 bytes each:
              16s  name             # null-padded ASCII; may contain stale
                                    # bytes for deleted entries
              u32  start            # absolute file offset of entry data
              u32  size             # length of entry data in bytes
              u8   deleted          # 0 = live, 1 = deleted/tombstoned
              u8   tail0            # for live pages/Program.s: (size>>8)&0xff
              u8   tail1            # for live pages/Program.s: (size>>16)&0xff
              u8   tail2            # for live pages/Program.s: (size>>24)&0xff
                                    # (semantics for fonts/main.HMI unclear)

Beyond the directory the file also contains:
  - 0x00080000  : a verbatim copy of the directory header (count + entries)
                  used as a backup/redundancy
  - 0x00380000  : a 4-byte sentinel (FF FF FF FF) followed by zeros
  - 0x006FFFF8  : the ASCII magic 'ver21234', 8 bytes before the first
                  data blob's offset (0x00700000)

Live entry kinds (by name suffix / shape):
  - N.pa             page N, structure documented by Nextion2Text PageHeader
                     (CRC, size, datainfoaddr, numberobj, password, locked,
                      version, name, ...)
  - main.HMI         192-byte global metadata block; first u32 is a model-id
                     CRC matched against Nextion2Text's _models table; tail
                     is six 16-byte references (to fonts and pages)
  - Program.s        ASCII text of the global "Program.s" script
  - N.zi             font, with the 0x04 0xFF 0x00 0x0A signature documented
                     by hagronnestad/nextion-font-editor
"""

import argparse
import re
import struct
import sys
from pathlib import Path

ENTRY_FMT = "<16sIIBBBB"
ENTRY_SIZE = struct.calcsize(ENTRY_FMT)
assert ENTRY_SIZE == 28, ENTRY_SIZE

BACKUP_DIR_OFFSET = 0x00080000
SENTINEL_OFFSET = 0x00380000
DATA_MAGIC_OFFSET = 0x006FFFF8
DATA_MAGIC = b"ver21234"


def parse_directory(data: bytes, base: int = 0):
    """Yield (index, name, start, size, deleted, tail0, tail1, tail2)."""
    (count,) = struct.unpack_from("<I", data, base)
    for i in range(count):
        off = base + 4 + i * ENTRY_SIZE
        name_raw, start, size, deleted, t0, t1, t2 = struct.unpack_from(
            ENTRY_FMT, data, off
        )
        nul = name_raw.find(b"\x00")
        if nul >= 0:
            name = name_raw[:nul]
        else:
            name = name_raw
        try:
            name_str = name.decode("ascii")
        except UnicodeDecodeError:
            name_str = name.decode("latin-1", errors="replace")
        yield (i, name_str, start, size, bool(deleted), t0, t1, t2, name_raw, off)


def categorize(name: str, blob: bytes) -> str:
    if name.endswith(".pa"):
        return "page"
    if name.endswith(".zi"):
        return "font"
    if name == "Program.s":
        return "program-script"
    if name == "main.HMI":
        return "main-meta"
    if blob[:4] == b"\x04\xff\x00\x0a":
        return "font(unnamed)"
    if blob.startswith(b"//"):
        return "script(unnamed)"
    if name == "":
        return "deleted-or-empty"
    return "other"


def find_substrings(blob: bytes, limit: int = 6, min_len: int = 4):
    return [
        (m.start(), m.group())
        for m in list(re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, blob))[:limit]
    ]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("hmi", type=Path, help="path to .HMI file")
    p.add_argument("--verify", action="store_true", help="probe each entry's data")
    p.add_argument("--check-backup", action="store_true",
                   help=f"check that directory at 0x{BACKUP_DIR_OFFSET:x} matches")
    p.add_argument("--show-deleted", action="store_true",
                   help="include deleted entries in main listing")
    p.add_argument("--dump-entry", type=int, default=None,
                   help="hex-dump first 256 bytes of entry N's data")
    args = p.parse_args(argv)

    data = args.hmi.read_bytes()
    print(f"file: {args.hmi}")
    print(f"size: {len(data)} (0x{len(data):x})")

    (count,) = struct.unpack_from("<I", data, 0)
    print(f"count: {count}")
    print()

    entries = list(parse_directory(data))

    print(f"{'#':>2}  {'name':<14}  {'start':>10}  {'size':>8}  del  tail(b0,b1,b2)  kind")
    print("-" * 78)
    for (i, name, start, size, deleted, t0, t1, t2, name_raw, _) in entries:
        if deleted and not args.show_deleted:
            continue
        blob = data[start : start + min(size, 64)]
        kind = categorize(name, blob)
        disp_name = repr(name) if (deleted or not name) else name
        print(
            f"{i:>2}  {disp_name:<14}  0x{start:08x}  0x{size:06x}  "
            f"{int(deleted)}    ({t0:>3},{t1:>3},{t2:>3})  {kind}"
        )

    if args.check_backup:
        print()
        primary = data[0 : 4 + count * ENTRY_SIZE]
        backup = data[BACKUP_DIR_OFFSET : BACKUP_DIR_OFFSET + len(primary)]
        if primary == backup:
            print(f"backup directory at 0x{BACKUP_DIR_OFFSET:x}: IDENTICAL ({len(primary)} bytes)")
        else:
            print(f"backup directory at 0x{BACKUP_DIR_OFFSET:x}: MISMATCH")
            for j in range(len(primary)):
                if primary[j] != backup[j]:
                    print(f"  first diff at +0x{j:x}: {primary[j]:02x} vs {backup[j]:02x}")
                    break

        # sentinel + magic
        sentinel = data[SENTINEL_OFFSET : SENTINEL_OFFSET + 4]
        expected_sentinel = b"\xff\xff\xff\xff"
        sentinel_label = "FF FF FF FF as expected" if sentinel == expected_sentinel else "unexpected"
        print(f"sentinel at 0x{SENTINEL_OFFSET:x}: {sentinel.hex()} ({sentinel_label})")
        magic = data[DATA_MAGIC_OFFSET : DATA_MAGIC_OFFSET + len(DATA_MAGIC)]
        print(f"magic at 0x{DATA_MAGIC_OFFSET:x}: {magic!r} "
              f"({'as expected' if magic == DATA_MAGIC else 'unexpected'})")

    if args.verify:
        print()
        print("=== entry verification (live entries) ===")
        for (i, name, start, size, deleted, *_rest) in entries:
            if deleted:
                continue
            blob = data[start : start + size]
            if len(blob) != size:
                print(f"  #{i:2} {name!r}: TRUNCATED ({len(blob)} of {size})")
                continue
            ok = True
            note = ""
            if name.endswith(".pa"):
                # PageHeader: <IIIII?bbb16s16b - the embedded name should match
                if size < 56:
                    ok = False; note = "too small for PageHeader"
                else:
                    crc, dsize, dstart, nobj, pwd = struct.unpack_from("<IIIII", blob, 0)
                    page_name = blob[24:40].rstrip(b"\x00").decode("latin-1", errors="replace")
                    note = f"crc=0x{crc:08x} datasize=0x{dsize:x} datastart=0x{dstart:x} nobj={nobj} page_name={page_name!r}"
                    if dsize != size:
                        ok = False; note += " (datasize != entry size!)"
            elif name.endswith(".zi"):
                if blob[:4] != b"\x04\xff\x00\x0a":
                    ok = False
                else:
                    note = f"font; magic OK"
            elif name == "main.HMI":
                model_crc = struct.unpack_from("<I", blob, 16)[0]
                note = f"model-id CRC = 0x{model_crc:08x}"
            elif name == "Program.s":
                head = blob[:60].decode("ascii", errors="replace")
                note = f"text starts: {head!r}"
            print(f"  #{i:2} {name:<14} {'OK ' if ok else 'BAD'}  {note}")

    if args.dump_entry is not None:
        idx = args.dump_entry
        if idx < 0 or idx >= count:
            print(f"entry index out of range")
            return 2
        e = entries[idx]
        i, name, start, size, *_ = e
        print()
        print(f"=== entry {i} {name!r} dump (first 256 bytes of {size}) ===")
        chunk = data[start : start + min(size, 256)]
        for off in range(0, len(chunk), 16):
            row = chunk[off : off + 16]
            ascii_row = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
            print(f"  +0x{off:04x}: {row.hex():<32}  {ascii_row}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
