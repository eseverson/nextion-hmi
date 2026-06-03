# Header 2 cipher (F-series)

Status: **fully cracked** as of 2026-05-10. F-series H2 is decryptable
and re-encryptable. Reference implementation:
[`scripts/lib/h2_cipher.py`](../scripts/lib/h2_cipher.py), round-trips every
F-series TFT in `tests/editor outputs/`.

For T0/K0/X3/X5 series, TFTTool's existing 4-byte repeating XOR scheme
applies — see [`tools/TFTTool/TFTTool.py`](../tools/TFTTool/TFTTool.py).
This document is F-series-specific.

## Where it lives

The cipher is implemented in `achmi.dll` subcommand `0x21` (managed
wrapper name `HmiSafeAppfree11DecData`), at VA `0x10004ec0`. The
dispatch table at `.rdata:0x1ca28` is shared by the editor's wire
protocol and its file-format crypto; subcommand IDs are
`(call_id − 0x10000)`. (The editor-unpacking provenance behind this
extraction is kept in a separate private repo.)

Related subcommands in the same table:

| Subcmd | Wrapper                       | What it does                                 |
|--------|-------------------------------|----------------------------------------------|
| 0x1f   | `HmiSafeAppfree10`            | Older H2 cipher (non-F-series, `xiliemark != 100`) |
| 0x20   | `HmiSafeAppfree11Encode`      | F-series H2 encrypt                          |
| 0x21   | `HmiSafeAppfree11DecData`     | F-series H2 decrypt (this doc)               |
| 0x23   | `HmiSafeWriteTFTFileSafe`     | TFT trailing 4-byte CRC writer               |
| 0x25   | `HmiSafeWriteHMIFileSafe`     | HMI-level finalise                           |
| 0x27   | `HmiSafeWritePageFileSafe`    | Page CRC (see [`format-hmi.md`](format-hmi.md)) |

## Encrypted region layout

```
0x000..0x0c4   appinf0 (H1 — plaintext, 196 B)
0x0c4..0x0c8   CRC-32/MPEG-2 of H1
0x0c8..0x114   appinf1 (encrypted, 76 B = sizeof(appinf1))
0x114..0x18c   rest of encrypted region (120 B, "trailing")
0x18c..0x190   CRC-32/MPEG-2 of [0x0c8..0x18c]
```

The encrypted region is 196 bytes total. Only the first 76 are the
`appinf1` struct the caller reads. The remaining 120 bytes are
project-specific data covered by the same CRC and consumed by other
runtime paths.

## Algorithm (verbatim from achmi.dll)

A stateful 3-stage subtract/XOR mix over 4-byte words. State held in
the cipher routine:

- 16-byte **key block** `K[0..15]`, initialised to a hardcoded constant
  block (LE dwords): `0x924f6584`, `0xfbad3bbf`, `0x582659b6`, `0xbd829c9c`.
- 8-step counter `c ∈ [0..7]`.
- An evolving `state` value, initialised to `ModelCRC` (the value at
  H1+0x2e). **Critical**: the asm overwrites the `ModelCRC` parameter
  slot on the stack with `mul` at the end of each iteration, so each
  subsequent iteration sees a different "ModelCRC" than the caller
  supplied. The cipher is *not* a simple keyed function of the
  caller-supplied ModelCRC.
- `prev_K0` = the value of `K[0..3]` at the start of the iteration.

Per word at offset `idx`:

```
key     = K[8 + c]
mul     = state * 2 ; mul[idx & 3] += key ^ 0xbc
K[0..3] <<= 2
K[3 - (key & 3)]  += key ^ 0x58
K47_pre  = K[4..7]
K[4..7] <<= 3
K[7 - (key & 3)]  += key ^ 0x7a

m1 = (~((K[0..3] + mul) ^ K47_pre)) - idx - 8
m2 = K47_pre + prev_K0 + mul
m3 = (K47_pre << (key & 7)) + prev_K0 + state

# Decrypt (DecData; what the asm verbatim does):
out = (((in - m1 - K47_pre) ^ m1) - m2 - prev_K0) ^ m2
out = (out - m3 - state) ^ m3

# Encrypt is the symmetric inverse applied in reverse order:
out = ((in ^ m3) + m3 + state)
out = ((out ^ m2) + m2 + prev_K0)
out = ((out ^ m1) + m1 + K47_pre)

# Tail (both directions):
c        = (c + 1) & 7
prev_K0  = K[0..3]
state    = mul
```

Both directions live in [`scripts/lib/h2_cipher.py`](../scripts/lib/h2_cipher.py)
as `encrypt` and `decrypt`. Verified by:

- round-trip on synthetic input (`encrypt(decrypt(x)) == x`);
- round-trip on every real TFT in the test corpus (re-encrypted output
  matches stored ciphertext byte-for-byte);
- decryption produces sensible `appinf1` fields (reserved fields zero,
  page/object/font counts match experiment deltas, e.g. exp 11 "add
  page" yields `pageqyt=5` vs. baseline 4).

## Why brute force missed it for so long

Earlier brute-force attempts assumed a 32-byte repeating XOR pad based
on self-correlation analysis (stride-32 ciphertext match rate was
26.2%, orders of magnitude above other strides). Half the apparent key
was recoverable from known-plaintext addresses, but the unrecoverable
half never agreed across experiments — because the cipher is stateful,
not a fixed pad. The state evolution per iteration produces apparent
periodicity at stride 32 (since `c` cycles every 8 dwords = 32 bytes)
without the cipher actually being key-repeating.

The stateful structure (each iteration mutates `K`, `state`, `prev_K0`)
defeated XOR-pad recovery. The algorithm only fell out of static
disassembly of `achmi.dll`.

## What this unblocks

- **Reading any F-series TFT's `appinf1`** — addresses, counts, font
  table entries, all decryptable in one pass.
- **Writing F-series TFTs end-to-end** — combined with the page CRC,
  H1 CRC, and the trailing file CRC at subcommand 0x23, all four
  invariants of the file are now reproducible.
- **TFTTool's H2-nuke bug is no longer a concern** — F-series writers
  can preserve and re-encrypt the unmodelled `[0x114..0x18c]` region
  losslessly.

## Open follow-ups

- The 120 trailing bytes (`0x114..0x18c`) decrypt to plausibly-structured
  data — partly decoded as four ~32-byte rows that look like
  per-page/component fingerprint records. Their semantics aren't fully
  pinned down. Two rows differ at only 5 byte positions (4, 5, 8, 12,
  13 within their 32-byte slot), suggesting an array of similar
  records with a few flag/counter bits each.
- A cleaner schema map of `appinf1` fields beyond the address block
  (positions 0x30..0x3F) remains on the disassembly task list.
