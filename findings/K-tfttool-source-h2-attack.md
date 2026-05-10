# Reading TFTTool source for H2 encryption clues

Followed up on T1 by reading `tools/TFTTool/TFTTool.py`'s H2 encryption
code path (no editor experiments needed).

## What TFTTool does for T0/K0/etc.

In `HeaderData` (line 268+):

```python
self.key = struct.pack("<I", key)              # 4-byte LE u32
self.key = self.key * (self.size // len(self.key) + 1)  # repeat to cover region
...
data = bytes([b ^ self.key[i] for i, b in enumerate(data[:self._contentSize])])
```

So for T0/K0, the encryption is **a 4-byte u32 key, repeated, XOR'd
against the H2 content area (0xC4 bytes)**. The keys live in
`_modelXORs` keyed by `model_crc`, e.g.:

```python
"NX3224T024_011": 0x6d713e32,
"NX3224T028_011": 0x965cdd00,
"NX4832K035_011": 0xa1d51bf2,
...
"NX4832F035_011": 0,    # the F-series our project uses
```

So **TFTTool literally has zero for our model**. That's the gap that
makes Path B's H2 corruption bug land — it "decrypts" with key=0 (no-op)
and assumes everything beyond 0x44 is FF padding.

## Tested hypothesis: maybe F-series uses the same 4-byte XOR scheme but with a different key

Two known plaintexts:
- `ressources_files_address` at H2+0x08 = `0x00010000` (resources start
  at file 0x10000 by Nextion convention; cross-validated with H1)
- `pictures_address` at H2+0x18 — likely `0x00070000` (this project has
  no pictures, so the field probably stores `ressources_files_address +
  ressources_files_size = 0x10000 + 0x60000 = 0x70000` as a "next free"
  sentinel)

Derive key candidates from each:

| Position in H2 | Cipher bytes | Assumed plaintext | Derived key bytes |
|---|---|---|---|
| 0x08..0x0b | `de 95 4d 03` | `00 00 01 00` (0x10000) | `de 95 4c 03` |
| 0x18..0x1b | `2d 09 8e 26` | `00 00 07 00` (0x70000) | `2d 09 89 26` |

Both H2+0x08 and H2+0x18 are 4-byte-aligned and at offsets that are
multiples of 4 from H2 start, so under a 4-byte-repeating key they'd
use `key[0..3]` and produce the same derived bytes. They don't.

**Conclusion: F-series doesn't use a simple 4-byte repeating XOR.**

Possibilities (untested):
1. The cipher uses a longer repeating key (≥24 bytes, since lengths up
   through 16 also predict a match here).
2. The pad is position-dependent (e.g., AES-CTR-style with position as
   counter). One-time-pad-like; can't be brute-forced from a few known
   plaintexts.
3. A different cipher entirely (stream cipher, ARX construct, etc.).
4. The schema offsets are different on F-series (maybe
   `ressources_files_address` ISN'T at H2+0x08 there). Less likely
   because the value `0x10000` is a strong known constant.

## What we know that's actionable

- **The XOR-cipher-cancellation property still holds for any XOR-based
  cipher**: encrypted_delta = plaintext_delta. So all the diffusion-map
  observations from [J](J-h2-transform-analysis.md) and the "linear at
  H2+0x18" finding remain valid in plaintext space too. We just can't
  read the plaintext absolute values without the pad.
- **Recovered partial pad bytes**:
  - At H2+0x08..0x0b: pad = `de 95 4c 03` (assuming our `ressources_files_address`
    plaintext is correct).
  - That pad lets us decrypt H2+0x08..0x0b in any F-series TFT (any
    project) that uses the same model and no different key derivation.
- **5 of the H2 address fields shift when file_size grows by 12**, but
  6 don't:
  - Shift: static_usercode, app_attributes_data, usercode, pictures, gmovs
  - No shift: ressources_files (fixed 0x10000), unknown_pages, unknown_objects,
    videos, audios, fonts
  - Hypothesis: fields that don't shift are sentinels (like 0 or fixed
    addresses) for resources that don't exist (this project has no
    videos/audios; fonts have a fixed location).

## Next-step attack vectors

1. **Try a longer-than-4-byte key length on the existing data.** Even
   if the cipher isn't byte-XOR-repeating, having more known plaintext
   could pin a longer key.
2. **Cross-reference with another F-series TFT** (e.g., one of the
   other variants in `_modelXORs` that's set to 0 like NX3224F024_011
   or NX3224F028_011). If they all use the same cipher with model-keyed
   pads, comparison helps.
3. **Read the Nextion firmware** itself (`stm32-binary` from resources
   slot 1, 243 KB) — it contains the decryption code. Reverse-engineer
   the firmware blob to find the XOR routine.
4. **Targeted experiment**: change a SINGLE address-field-affecting
   thing in a controlled way. E.g., add exactly one font (which should
   only change `fonts_address` and `fonts_count`, leaving everything
   else unchanged). The minimal H2 diff isolates the cipher pad for
   those two fields.
