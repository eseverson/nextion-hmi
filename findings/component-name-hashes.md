# Component-name hash table (TFT)

Cracked in two stages:

1. **`14_char_name.tft`** — a project where one Hotspot was renamed
   in the editor to `"a" * 14` (the maximum HMI objname length). The
   14-byte ASCII string `aaaaaaaaaaaaaa` does not appear anywhere in
   the compiled TFT — but a u32 of `0x689a44ff` does, exactly once,
   at strdata-relative `0x11a0`, inside a sorted `(u32 hash, u16
   ordinal)` table.
2. **`collision.tft` + `xixr.tft`** (2026-05-18) — a pair of minimal
   3-component fixtures that differ in exactly 14 bytes: two `(hash,
   ord)` entries swap places when one component is renamed `w621q →
   x1`, plus the trailing file CRC. Solving for the function that
   produces the observed hashes pinned down the **null-padding to 14
   bytes** that was masked by stage 1 (whose name was already 14
   bytes, so padding was a no-op).

## Hash function

Same `crc32_bytewise` already used for [`AppAllvas`](format-tft.md#appallvas--global-scalar-name-table)
(documented in [`page_crc.py`](../scripts/lib/page_crc.py)) but
applied to the **14-byte null-padded** objname — matching the HMI
typebyte `0x1e` field width:

```python
def hash_objname(name: bytes) -> int:
    padded = name.ljust(14, b"\x00")[:14]
    return crc32_bytewise(0xFFFFFFFF, padded)
```

Implemented as `hash_objname()` in [`page_crc.py`](../scripts/lib/page_crc.py).

Verified mappings:

| name              | padded input                                            | hash         |
|-------------------|---------------------------------------------------------|--------------|
| `aaaaaaaaaaaaaa`  | `b'aaaaaaaaaaaaaa'` (already 14)                        | `0x689a44ff` |
| `page0`           | `b'page0\x00\x00\x00\x00\x00\x00\x00\x00\x00'`           | `0xac967926` |
| `xixr`            | `b'xixr\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'`        | `0xff6ddc1f` |
| `w621q`           | `b'w621q\x00\x00\x00\x00\x00\x00\x00\x00\x00'`           | `0xd1e1feb9` |
| `x1`              | `b'x1\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'`  | `0x08c28c7b` |

**Caveat about an earlier draft of this file**: the original write-up
said the hash was `crc32_bytewise(0xffffffff, name)` with no padding.
That happened to produce the right answer for `14_char_name.tft`
because the test name was already exactly 14 bytes. The AppAllvas
table (and the parallel "well-known" table at strdata+0x34) uses
the **un**-padded form — same CRC seed, but the input is the raw
variable name (`sys0`, `sys1`, `sys2`). So two distinct hash schemes
coexist in the TFT:

| table location          | input form               | example              |
|-------------------------|--------------------------|----------------------|
| AppAllvas + `+0x34`     | raw name, no padding     | `crc32(0xff, b"sys0") = 0xd9f18195` |
| component-name table    | name padded to 14 bytes  | `crc32(0xff, b"sys0\x00..pad..")` |

## Wire layout

Found at strdata-relative `[0x1158 .. 0x121e]` in `/tmp/14_char_name.tft`
(absolute `[0x91158 .. 0x9121e]`):

```
struct hash_entry {
    u32 hash;          // crc32_bytewise(0xffffffff, name)
    u16 ordinal;       // 0..N-1 (some kind of slot index)
} entries[N];
u32 trailing_terminator;   // 0x00000260 in this fixture; 0xffff in u16
```

Entries are sorted by hash ascending — binary-searchable by hash.

In the fixture, 31 entries cover ordinals 0..30 (the `a*14` entry has
ordinal 30, matching its position as the most-recently-added name). One
hash (`0xaf3dd197`, ordinal 0) also appears in a parallel 4-entry table
at strdata-relative `+0x34` — so the same name can be hashed in two
places.

## Location within strdata

```
+0x00..+0x30    section directory (8 × u32 size pairs)
+0x34..+0x4c    4-entry "well-known" hash table (also (u32,u16))
+0x4c            MainCodeHex — start of Program.s bytecode
...              per-page init bytecode
+0x1158..+0x121e component-name hash table       ← this finding
...              more bytecode / padding
+AppAllvasAddr   AppAllvas global-scalar name table
+staticstrBeg    static string pool
+attdataaddr     binattinf records
```

Both fixtures (`14_char_name.tft` and `miata-dash`) share the 4-entry
well-known table at +0x34 (identical hashes `0x33ed9126`, `0x72f2a6f1`,
`0xaf3dd197`, `0xd252015b`). The names behind those four hashes are
unknown — probably standard event-handler labels (`prep` / `postinit` /
`Pageprestart` or similar). Brute-forcing 3–8-letter lowercase did not
hit them; the source names are likely longer or non-ASCII.

## What this resolves

**The name-lookup mechanism is hash-based.** When the firmware
receives `xname.val=…` over UART, it almost certainly hashes the
incoming name with `crc32_bytewise(0xffffffff, name)` and binary-
searches the corresponding component-name table. The ordinal it gets
back then resolves to a component (likely via an additional table — to
be confirmed).

## Collision behaviour of the hash

The earlier collision pairs in this section were generated against
the **unpadded** hash and don't actually collide under the real
function. For example, `xixr` and `w621q` both hash to `0x00067955`
under the unpadded form, but their 14-byte padded forms hash to
`0xff6ddc1f` and `0xd1e1feb9` — different values, no collision.

### Editor-side enforcement

The editor **rejects compile** when two objnames on the same page
hash to the same value. Empirically tested 2026-05-18 with the pair
`abfa`/`rpfgnc` (both letters-only, hash `0x00000a7f`):

```
Error: Illegal name (CRC):page0:abfa-rpfgnc
Error: Compile failed! 1 Errors, 0 Warnings,
```

Two consequences:

- The string `(CRC)` in the editor's own error message confirms
  the hash is the lookup key.
- The firmware can assume hash-unique objnames within a page. An
  authoring tool only needs to *verify* uniqueness when writing a
  TFT; it never needs a runtime disambiguation policy.

### Structural pattern in the collision set

A parallel C/OpenMP sort-based search across all letters-only
inputs of length 1..6 (~308M candidates) finds 228,800 collisions.
Two structural properties:

1. **Zero same-length collisions.** The hash is injective on
   letters-only inputs of any fixed length ≤ 6. All 228,800
   collisions are length-4 ↔ length-6.
2. **They all share one kernel.** For every collision pair `(a, b)`
   in this run, `pad14(a) XOR pad14(b) == K` where

   ```
   K = 13 12 00 06 6e 63 00 00 00 00 00 00 00 00
   ```

   Confirmed: `crc32_bytewise(0, K) = 0`, so XORing `K` into any
   14-byte input preserves the hash (CRC32 is GF(2)-linear). `K` is
   the shortest such kernel whose XOR keeps both inputs inside the
   letters-only charset — that's why the 6-byte tail forces the
   second name to end in `nc` (bytes `6e 63`) and the byte at
   position 2 to be identical between the two names.

Three confirmed pairs (verified against `hash_objname()`):

| name A | name B    | hash         |
|--------|-----------|--------------|
| `abfa` | `rpfgnc`  | `0x00000a7f` |
| `vjsc` | `exsenc`  | `0x00001041` |
| `twqu` | `geqsnc`  | `0x00002c37` |

## What's still open

1. **Semantics of the u16 `ordinal`.** Appears to be a 0-based
   index. In the `collision.tft`/`xixr.tft` pair the mapping is
   `ord=0 → page0`, `ord=1 → xixr`, `ord=2 → (w621q | x1)`. Pinning
   down whether ordinal indexes into `objxinxi`, `pagexinxi`, or some
   name-specific array is the next step.
2. **The "well-known" table at strdata+0x34** has 4 entries in
   `14_char_name.tft` but only 1 entry (`page0`'s hash) in
   `collision.tft`. So it isn't a fixed compile-time constant — its
   contents vary per project. The other 3 entries in `14_char_name`
   are still unidentified; with the corrected hash function and known
   inputs (page/event-handler labels) they may be brute-forceable.

## Miata-dash speed-gauge case — resolved

Miata-dash **does** have a component-name hash table. The earlier
"no table" claim was an artifact of searching with the wrong (un-
padded) hash. Hashes of `x0`..`x8`, `t0`..`t8`, `m0`, `b0`, `h0`,
`s0` all hit, clustered at strdata-relative `0x10da..0x197f` in
`source/nextion.hmi.tft`. The names *are* the position-derived
defaults the editor assigns — components were never custom-renamed,
so the editor emitted the auto-generated names.

For an authoring tool that adds a 10th XFloat on page 0, the work is
now well-defined: compute `hash_objname("x9")`, insert sorted into
the table region, and update downstream offsets. Mechanics for the
sort/insert are documented above.

## Cross-references

- [`format-tft.md`](format-tft.md#appallvas--global-scalar-name-table) — sibling AppAllvas hash table.
- [`page_crc.py`](../scripts/lib/page_crc.py) — `crc32_bytewise` implementation.
- [`format-hmi.md`](format-hmi.md#attribute-record-format-inside-a-component-hmi-side) — HMI side's typebyte `0x1e` (14-byte objname). The TFT compilation hashes the objname; the original ASCII does **not** survive the compile.
