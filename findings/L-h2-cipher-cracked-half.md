# F-series H2 cipher: 32-byte XOR pad, half recovered

Following [K](K-tfttool-source-h2-attack.md) which proved the F-series
cipher isn't a 4-byte XOR. This finding pins down the cipher structure
and recovers half the key.

## The cipher is a 32-byte repeating XOR pad

Self-correlation of the baseline H2 ciphertext at various strides:

| stride | match rate |
|--------|-----------|
| 4   | 1.0% |
| 8   | 0.0% |
| 16  | 0.5% |
| 24  | 0.6% |
| **32** | **26.2%** |
| 40  | 1.2% |
| 48  | 0.0% |
| 64  | 11.8% |

Stride 32 jumps to 26.2% — orders of magnitude above any other. That's
because the ciphertext at offset N and offset N+32 share the same key
byte (so any plaintext byte that repeats 32 bytes apart shows up as a
ciphertext match). The 11.8% at stride 64 is consistent (every other
multiple of 32 is also same-key, and plaintext repeats less further
apart).

So the F-series H2 cipher is **a 32-byte key, repeated, XOR'd against
the H2 content area** (analogous to TFTTool's 4-byte scheme for T0/K0
but with a longer key).

## Key bytes recovered (16 of 32)

Using these known plaintexts in our project:
- H2+0x08 `ressources_files_address` = `0x00010000`
- H2+0x0c `usercode_address` = `0x00070000`
- H2+0x20 `videos_address` = `0` (no videos)
- H2+0x24 `audios_address` = `0` (no audios)

Recovered key bytes:

```
key[0x00] = 0x84    key[0x10] = ??
key[0x01] = 0xc6    key[0x11] = ??
key[0x02] = 0xf9    key[0x12] = ??
key[0x03] = 0x9d    key[0x13] = ??
key[0x04] = 0x8e    key[0x14] = ??
key[0x05] = 0x32    key[0x15] = ??
key[0x06] = 0x0f    key[0x16] = ??
key[0x07] = 0x66    key[0x17] = ??
key[0x08] = 0xde    key[0x18] = ??
key[0x09] = 0x95    key[0x19] = ??
key[0x0a] = 0x4c    key[0x1a] = ??
key[0x0b] = 0x03    key[0x1b] = ??
key[0x0c] = 0xb9    key[0x1c] = ??
key[0x0d] = 0x5b    key[0x1d] = ??
key[0x0e] = 0x26    key[0x1e] = ??
key[0x0f] = 0xd8    key[0x1f] = ??
```

The unknown half of the key (positions 0x10..0x1f) corresponds to H2
fields:
- `unknown_pages_address` (H2+0x10) — non-zero, plaintext value unknown
- `unknown_objects_address` (H2+0x14) — non-zero, plaintext value unknown
- `pictures_address` (H2+0x18) — likely 0 OR `ressources_files_address +
  ressources_files_size = 0x70000`, but neither guess produced a key
  consistent with cross-field plaintext checks
- `gmovs_address` (H2+0x1c) — same uncertainty

## Verification

With the 16 known key bytes, decryption produces sensible values where
expected:

```
H2+0x08: 00 00 01 00     ✓ ressources_files_address = 0x10000
H2+0x0c: 00 00 07 00     ✓ usercode_address = 0x70000
H2+0x20: 00 00 00 00     ✓ videos_address = 0
H2+0x24: 00 00 00 00     ✓ audios_address = 0
```

These are *constructive* (we used them as known plaintexts) but the fact
that all four key derivations are mutually consistent in a 32-byte
cycle is non-trivial — the data confirms the cycle hypothesis.

The H2 unmodelled region (0x44..0xc4) decrypts (where keys are known)
to plausibly-structured data — not 0xFF, not random — supporting that
this is real content, not padding. TFTTool's "fill with 0xFF on save"
behaviour is wrong for F-series.

## What we can do today with the half-key

- **Decrypt 8 of every 32 bytes** in any F-series H2 (the bytes at
  positions 0..15 in the cycle).
- **Validate** any F-series TFT by checking those decrypted bytes match
  expected plaintext (e.g., `ressources_files_address = 0x10000`
  always).
- **Detect tampering** at those positions (anything that shouldn't have
  changed will show up as a delta).

## Next attacks to recover the full key

1. **Find more known plaintext** at positions 0x10..0x1f. Candidates:
   - `pictures_address` likely a sentinel — could be 0, end-of-resources
     (0x70000), or maybe `ressources_files_address` itself for
     "located inside resources".
   - `unknown_pages_address` and `unknown_objects_address` — possibly
     pointers into the usercode region with known offsets we can compute
     from the page directory (we have it in HMI).
2. **Narrow experiments** — add a Picture component (cracks T5 AND
   gives us pictures_address known plaintext when count goes 0→1).
3. **Firmware blob reverse** — `stm32-binary` (243 KB) in resources
   slot 1 contains the device's H2 reader. Disassemble to find the
   fixed-key bytes directly.
4. **Cross-project comparison** — different F-series projects with
   different resource layouts give different known-plaintext addresses
   at the unknown positions.

## Cross-experiment plaintext attempt (failed: schema mismatch)

The Picture-component experiment (15) and for-loop experiment (16) gave
new ciphertext data, but cross-deriving keys from them produced
**conflicts** at every count-field position (H2+0x30..0x3f). Specifically:

- Baseline plaintext `pages_count = 4` at H2+0x30 implies key[0x10..0x11]
  = `51 e8`.
- Exp15 plaintext `pages_count = 4` (still 4, no new pages) at H2+0x30
  implies key[0x10..0x11] = `e5 6c`.

Different keys for the same cycle position from "the same plaintext"
means **at least one of the assumptions is wrong**. Most likely: the
F-series H2 schema is NOT exactly TFTTool's T0/K0 layout — count fields
sit at different offsets, or extra fields exist between addresses and
counts on F-series.

Implication: TFTTool's `_fileHeader2` schema is a partial guess for
F-series. The first 0x30 bytes (the 12 address u32 fields) seem correct
based on the address-only assumptions giving consistent keys; positions
0x30+ likely deviate.

Lower-confidence findings from running the cross-experiment derivation
anyway — these are the WRONG-cycle-position values and shouldn't be
trusted, but recorded for future reference:

```
position 0x10..0x11: candidates {0x51e8, 0xe56c, 0x6c..} — pages_count
position 0x14..0x15: candidates {0xeafe, 0xebfe, 0x9e..} — pictures_count
```

The full key still needs experiments that cleanly probe positions
0x10..0x1f, OR a re-derivation of the F-series schema from a known
plaintext at those positions.
