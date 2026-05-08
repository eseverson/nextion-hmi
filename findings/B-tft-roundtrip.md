# B — Round-tripping a TFT through TFTTool: what's lossless, what isn't

Subject file: `source/nextion.hmi.tft` (Miata dashboard, **NX4832F035**, T1/Discovery
series, 480x320, editor `nxt-1.67.1`, file-version 0x21, 504,784 bytes).

Tool under test: `tools/TFTTool/TFTTool.py` (UNUF fork) at the version pulled by
`scripts/setup.sh`.

Methodology: drive TFTTool programmatically (see `scripts/dump_tft.py`,
`scripts/mutate.py`, `scripts/mutate_with_crc.py`), since the CLI aborts on
`nxt-1.67.1` (no matching instruction set). Each mutation is byte-diffed against
the source. Mutated files live in `work/` (gitignored). **None were flashed.**

## TL;DR for this file

**TFTTool cannot losslessly round-trip a T1/F-series TFT.** Even a no-op
load + `update_raw()` corrupts a 128-byte data region inside header 2
(file offsets `0x10c..0x18c`) by overwriting it with `0xFF`. The corruption
is idempotent (second round-trip is stable) but the original 128 bytes are
gone. This region is not parsed by TFTTool at all; it's structured,
high-entropy data.

The tool was designed for T0/Basic and K0/Enhanced. Our F-series file falls
outside that envelope.

## Field map (this file, T1/F-series)

| Field / region                          | TFT offset            | Mutable via TFTTool?                  | Notes |
|-----------------------------------------|-----------------------|---------------------------------------|-------|
| `editor_vendor` (`N`/`T`)               | H1+0x03               | Indirectly, via `setModel(NXT/TJC)`   | Free-byte writeable; TFTTool won't change it without a model swap |
| `editor_version_main/sub/bugfix`        | H1+0x01,0x02,0x1b     | Yes (`-e a.b.c`)                      | Lossless; updates H1 CRC and tail CRC |
| `old_lcd_orientation`, `ui_orientation` | H1+0x00, 0x14         | Programmatically (`header1.content`)  | Worked; updates H1 CRC + tail CRC |
| `lcd_resolution_x/y`                    | H1+0x10, 0x12         | Programmatically                      | Worked; same as above |
| `model_series`                          | H1+0x16               | Programmatic, but **dangerous**       | Hard-coded to {0,1,2,3,100} in `update_raw`, else exception |
| `model_crc`                             | H1+0x2e               | Yes, via `setModel(target)`           | Auto-recomputed from new model name |
| `file_version`                          | H1+0x32               | Programmatically                      | Worked |
| `file_size`, `ressources_files_*`       | H1+0x37..0x47         | Programmatically writable             | Not auto-recomputed — set them wrong, file becomes invalid |
| Various H1 `unknown_*` (driver/binary addresses, file_id, metadata_size) | H1 | Writable, no semantic checks | TFTTool just preserves them across the round-trip |
| **H1 CRC**                              | H1+0xc4 (last 4 of H1)| **Auto-recomputed** by `update_raw`   | Always overwritten on save |
| Header 2 modeled fields (addresses, counts) | H2+0x00..0x44     | Programmatically writable             | Worked, BUT see warning below: H2 XOR key is `0` for this F model — values are still encrypted nonsense and rewriting them keeps them encrypted-as-if-key-zero, not what the device expects |
| **Header 2 unmodeled region (128 B)**   | **H2+0x44..0xc4 (file 0x10c..0x18c)** | **NO — silently nuked** | TFTTool's schema models 68 B of content; the remaining 128 B is treated as `0xFF` padding and overwritten. Real file has high-entropy data here. |
| **H2 CRC**                              | H2+0xc4..0xc8         | **Auto-recomputed** on every save     | Will mismatch original since the data block above is now FFs |
| Body / pages / pictures / fonts / usercode | 0x190..0x7b3cc    | TFTTool itself never edits the body   | You can byte-poke; TFTTool's `update_raw` will fix the trailing file CRC for you |
| **File CRC32 (XORed with bytes 0x03,0x2e,0x3c)** | last 4 bytes (0x7b3cc..0x7b3d0) | **Auto-recomputed** by `update_raw` | `series` must be in {0,1,2,3,100} or it raises |

`F-series` extra: `_modelXORs[NX4832F035_011] = 0`, so TFTTool's header2
"decryption" is a no-op, and the parsed addresses are clearly garbage
(`static_usercode_address = 0xfa122830`, etc — see `work/header_dump.json`).
The real XOR key is unknown to TFTTool. Practical consequence: even the
**modeled** header 2 fields are not reliably mutable on this file.

## TFTTool's hidden invariants (auto-fixed on every save)

- **Header 1 CRC** (CRC32 over the first 0xC4 bytes; trailing 4 bytes of H1).
- **Header 2 CRC** (CRC32 over the encrypted 0xC4 bytes of H2; trailing 4 bytes of H2).
- **Whole-file CRC32**, last 4 bytes of the file. Algorithm differs by series:
  - series 0 (T0), 1 (K0), 100 (T1): byte-wise CRC over `raw[:-4]`.
  - series 2 (X3), 3 (X5): word-wise CRC, padded to 4-byte multiple.
  - The LSB of the result is XORed with `raw[0x03] ^ raw[0x2e] ^ raw[0x3c]`
    (vendor letter, model_crc[0], file_version) before being stored.
- **Header 2 "empty" region**: bytes `H2+content_size..H2+0xc4` are filled
  with `0xFF` on save. For T0/K0 this matches reality; for T1/F-series this
  destroys 128 bytes of data.
- **Header 2 XOR encryption key**: derived from `model_crc` lookup in
  `_modelXORs`. Only changeable by `setModel`.

What TFTTool does **not** touch:
- The entire body after `0x190` (pages, pictures, fonts, usercode, bootloader,
  font data) — it never edits these. You can byte-poke them and TFTTool will
  happily keep your edit *and* repair the trailing file CRC. No checksum is
  recomputed for any sub-region (font tables, picture catalogs, page records).

## Mutation experiments — outcomes

All artifacts under `work/`. Diff regions reported relative to file offset 0.

| Experiment | Body change | Tool-induced changes | Verdict |
|------------|-------------|----------------------|---------|
| `set_model_NX4832K035` (force) | none | H1+0x2e (model_crc), H1+0xc4 (H1 CRC), H2 entire (re-encrypted with K-series key + 128 B nuked), tail CRC | "Works", but H2 unknown region lost |
| `set_model_NX4832T035` (force) | none | same scope | as above |
| `set_model_TJC_F`          | none | H1+0x03 (vendor), H1+0x2e, H1 CRC, H2 nuked region, tail CRC | as above |
| `editor_version_to_1.65.1` | none | H1+0x02 (sub), H1 CRC, H2 nuked region, tail CRC | Mutable; but H2 collateral damage |
| `h1_lcd_resolution_x`      | none | H1+0x10, H1 CRC, H2 nuked, tail CRC | Mutable |
| `h1_ui_orientation`        | none | H1+0x14, H1 CRC, H2 nuked, tail CRC | Mutable |
| `h1_file_version`          | none | H1+0x32, H1 CRC, H2 nuked, tail CRC | Mutable |
| `h2_static_usercode_addr=0`| none | H2+0x00, H2 nuked, tail CRC. **H1 CRC unchanged** | Mutable per schema, but stored value is XOR-encrypted with key=0, which probably isn't what the firmware expects |
| `byte_poke_0x500=0xAA` (no repair) | 1 byte | none — but file CRC at tail no longer matches | Edit succeeds; resulting file fails CRC validation |
| `text_replace 'Coolant'->'AAAAAAA'` (no repair) | 7 bytes at 0x712db | none | Tail CRC stale; same caveat |
| `text_replace 'Coolant'` (with repair) | 7 bytes | + H2 nuked region + tail CRC | Tail CRC valid again; H2 collateral damage |
| `text_replace 'Battery'` (with repair) | 6 bytes at 0x7134c | + H2 nuked region + tail CRC | Same |
| `single_byte_poke 0x71300=0x42` (with repair) | 1 byte | + H2 nuked region + tail CRC | Same |
| `noop_repair_only`         | none | H2 nuked region + tail CRC | **Round-trip itself is lossy on this file** |
| `raw_baud_115200_to_9600` (no repair) | 4 bytes at 0x70057 | none | Tail CRC stale |

## Practical implications

1. **If the goal is to mutate text labels or byte-level constants for a
   T0/K0 file**: do the raw byte edit yourself, then run TFTTool's
   `update_raw()` to refresh the three CRCs. This is the lossless path.
2. **For this T1/F-series file**: **do not** use TFTTool for repair. You
   need to (a) preserve the original H2+0x44..H2+0xc4 region byte-for-byte,
   (b) recompute H2 CRC manually, (c) recompute the tail CRC manually using
   the same byte-wise variant. A small standalone helper would be better.
3. The cleartext text labels we can see (`Coolant`, `Battery`, `RPM`,
   `Pulsewidth m...`) sit in fixed-stride 32-byte slots starting around
   `0x712a0`. Same-length replacements look feasible, but until the H2
   structure is reverse-engineered, save/repair must avoid the
   `update_raw()` path.

## Suggested next steps (out of scope for this task)

- Reverse-engineer the 128-byte H2+0x44 region. Likely the F-series specific
  metadata block (font table indices? object directory? bootloader hash?).
- Discover the F-series XOR key. It's likely `Checksum.CRC` of something in
  the file — TFTTool's table for K/T/X is presumably derived this way.
- Add an instruction set entry for `nxt-1.67.1` so usercode decoding works.
- Wrap the working CRC algorithms in a tiny helper that **doesn't** assume
  the H2 empty region.

## Code committed

- `scripts/setup.sh` — clones third-party tools (already existed)
- `scripts/dump_tft.py` — header-only dump bypassing instruction-set check
- `scripts/list_models.py` — list TFTTool's model XOR table
- `scripts/mutate.py` — primary mutation experiment harness
- `scripts/mutate_with_crc.py` — secondary harness, body edit + TFTTool repair
- `findings/B-tft-roundtrip.md` — this file
- `.gitignore` — excludes `work/` and `tools/`
