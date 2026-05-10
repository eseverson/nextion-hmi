# H2 stable-region analysis + firmware blob attempt

Continuing T1 work without new editor samples.

## Firmware blob analysis (negative result)

The TFT contains a 243 KB STM32 firmware blob in resource slot 1.
Searched all 8 populated resource slots for any of our 16 recovered key
bytes:

```
slot 0 (17.5 KB tables): 0 hits for any key prefix
slot 1 (243 KB main fw): 0 hits
slot 2 (24 KB LCD driver): 0 hits
slot 3-6 (small tables): 0 hits
```

So the cipher key isn't stored as raw bytes in any resource. It's
either:
- Computed at runtime from `model_crc` (or some other constant),
- Embedded in compressed/packed firmware,
- Held in the bootloader (not in this TFT — only the user-loadable
  region is shipped here).

Slot 1 has entropy 7.39 bits/byte (suggesting compression/packing).
Disassembly attempts at offset 0 and 0x100 produced nonsense Thumb
code, confirming it's not raw ARM. RE'ing this without a known-good
unpack/decrypt routine is blocked.

## H2+0x40..0xc4 is a stable 132-byte fingerprint

Computed H2 byte-level change frequency across 11 experiments
(orientation flip, val change, txt change, bco change, +Hotspot,
delete component, page change, +page, +Picture, +loop):

```
H2+0x00..0x0f: 10 2 . . 10 1 . . 1 . . . 10 3 . .
H2+0x10..0x1f:  . . . .  . . 1 .  9 3 1 .  10 4 1 .
H2+0x20..0x2f:  . . . .  . . . .  . . . .   . . . .   ← never changed (sentinel addresses?)
H2+0x30..0x3f:  1 1 1 .  2 . . .  1 . 3 .   1 . . .
H2+0x40..0xc3:  . . . . . . (all zero, all rows) . . .   ← never changed
H2+0xc4..0xc7: 11 11 11 11   ← H2 CRC, every experiment
```

Key observations:
- **H2+0x20..0x2f stable across all** — the "sentinel address" region for
  resources we don't have (videos / audios / fonts addresses for
  empty types).
- **H2+0x40..0xc3 stable across all** — 132 bytes of project-specific
  invariant data. NEVER touched by any of the 11 cosmetic edits.

## Decrypting the H2+0x40..0xc4 region (with the half-key)

```
H2+0x40: b3 a1 7e 51  c1 84 37 29  98 fd 78 22  8a 18 96 ae
H2+0x60: 46 e6 85 c8  2d 6d 98 dc  ec f5 ca 32  4a 19 c6 43
H2+0x80: c6 d9 85 ce  ed 6d 98 e8  2c ca ca 0a  0a 19 c6 13
H2+0xa0: c6 d9 85 ce  2d 6c 98 e8  6c ca ca 0a  ca 18 c6 13
```

Notice **H2+0x80 and H2+0xa0 are nearly identical** — they differ at
exactly 5 byte positions (4, 5, 8, 12, 13 within their 32-byte slot).
The differences look like single-bit or few-bit flips:
- byte 4: ed vs 2d (XOR 0xc0 = top 2 bits)
- byte 5: 6d vs 6c (XOR 0x01 = LSB)
- byte 8: 2c vs 6c (XOR 0x40 = one bit)
- byte 12: 0a vs ca (XOR 0xc0)
- byte 13: 19 vs 18 (XOR 0x01)

This pattern is consistent with **an array of N similar 32-byte
records** where each record has mostly the same content + a few flag
or counter bits per record. 132 bytes ≈ 4 × 32 + 4 trailing bytes.

Hypothesis: H2+0x40..0xc3 is an array of **per-page or per-component
fingerprint records**, each ~32 bytes. The records are stable across
cosmetic edits, supporting a structural / model-level interpretation
rather than per-content.

## Implication for T1 (key recovery)

Since H2+0x40..0xc3 is project-stable but its plaintext is unknown, we
can't directly use it to recover the missing 16 key bytes (positions
0x10..0x1f).

However: if we ever find another F-series project (different model OR
different content) and compare H2+0x40..0xc3 between them, the
differences (XOR'd) reveal plaintext changes WITHOUT requiring the
key. That's a viable path with a second device.

## Untried/blocked attacks

- Firmware reverse-engineering — blocked on the packed/compressed
  format of slot 1.
- Custom CRC algorithm for page CRC — could be implemented in slot 1
  too. If the cipher and CRC are both in there, they're inaccessible
  until we crack the firmware unpacking.
- Cross-device comparison — needs a second F-series TFT.

## Path forward without new samples (productive options)

Now that the cipher's structure is known (32-byte XOR pad), even
without the full key we can:

1. **Validate** any F-series TFT by checking that the visible 16 bytes
   decrypt to plausible values (`ressources_files_address = 0x10000`
   etc.). Done in `scripts/inspect_tft.py`.
2. **Detect tampering** in the visible-half H2 fields.
3. **Build a partial F-series writer**: modify only fields whose
   key bytes we know (most of the address fields), recompute H1+H2
   CRCs, write back. The unknown half stays untouched.

For the user's actual goal (Linux dashboard editing), the half-key is
sufficient for "patch text/colour/value fields in TFT and re-CRC" —
the plaintext modifications happen in the body, the key only matters
for re-encrypting H2 after H1 changes. With the half-key we cover the
critical address fields.
