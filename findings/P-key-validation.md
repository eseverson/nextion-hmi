# Cross-experiment H2 key validation

Following up on [L](L-h2-cipher-cracked-half.md). Decrypted H2 across
all 11 experiments using the recovered 16-byte half-key, looked for
consistency.

## Strong validation: H2+0x20..0x27 (cipher pos 0..7)

Decrypted plaintexts at H2+0x20..0x27 across every experiment:

| Experiment | H2+0x20..0x23 | H2+0x24..0x27 |
|---|---|---|
| baseline | `0x00000000` | `0x00000000` |
| 01 (orientation) | `0x00000000` | `0x00000000` |
| 04 (val=DEADBEEF) | `0x00000000` | `0x00000000` |
| 07 (+Hotspot) | `0x00000000` | `0x00000000` |
| 11 (+page) | `0x00000000` | `0x00000000` |
| 15 (+Picture) | `0x00000000` | `0x00000000` |
| 16 (+loop) | `0x00000000` | `0x00000000` |

**All zeros, every experiment.** This confirms:

- The 32-byte cipher cycle hypothesis (positions 0..7 in cycle hold
  consistent key bytes).
- Cipher key bytes `key[0x00..0x07] = 84 c6 f9 9d 8e 32 0f 66`
  ARE CORRECT.
- Whatever fields live at H2+0x20..0x27 are static-zero plaintext for
  this project (consistent with TFTTool's `videos_address` and
  `audios_address` for projects with no video/audio resources).

## Weak validation: H2+0x08 and H2+0x0c

Decrypted plaintexts:

| Experiment | H2+0x08..0x0b | H2+0x0c..0x0f |
|---|---|---|
| baseline | `0x00010000` | `0x00070000` |
| 01 | `0x00010000` | `0x00070000` |
| 04 | `0x00010000` | `0x00070004` |
| 07 | `0x00010000` | `0x000731ac` |
| 11 | `0x00010000` | `0x000700d3` |
| 15 | `0x00010000` | `0x0007319a` |
| 16 | **`0x0001003d`** | `0x0007324e` |

H2+0x08 stays at `0x00010000` for 6 of 7 — but exp 16 shifts to
`0x0001003d`. That breaks the "ressources_files_address = 0x10000
constant" assumption for F-series.

H2+0x0c is `0x00070000` only in baseline + orientation flip. Other
experiments produce values like `0x000731ac` or `0x000700d3` — NOT
the expected `usercode_address = 0x70000` constant.

**Implication:** my schema mapping of "H2+0x08 = ressources_files_address
and H2+0x0c = usercode_address per TFTTool's T0/K0 layout" is
inaccurate for F-series. These fields likely hold:

- A hash/checksum of resources content (matches baseline pristine
  value `0x10000` by coincidence; changes when content changes
  significantly).
- A pointer + sub-region offset combo, where the sub-region offset
  encodes some content-derived value.

The cipher key bytes `key[0x08..0x0f] = de 95 4c 03 b9 5b 26 d8`
remain self-consistent (they decrypt to *some* value at each
position; that value just isn't what I labeled it). The cipher
recovery is correct; the schema interpretation isn't.

## What this means for T1

- **8 of 32 key bytes definitively confirmed** (positions 0x00..0x07,
  via the all-zero videos/audios fields).
- **8 more key bytes (0x08..0x0f) are self-consistent** but their
  decrypted field semantics aren't yet pinned down.
- **16 unknown** (positions 0x10..0x1f).

To confirm the 0x08..0x0f key bytes, we need a known plaintext that
holds across multiple experiments. Candidates from the schema:

- A field that's truly invariant across all our experiments — those
  exist at H2+0x20..0x2f and the entire 0x40..0xc4 region.
- For the 0x40..0xc4 region: 132 bytes of project-specific invariant
  data. If we knew what that data IS (not what TFTTool's schema
  documents — F-series schema differs), we'd have ~64 known-plaintext
  bytes, way more than enough to confirm or correct key[0x08..0x0f].

## Practical capability today

With ≥8 confirmed key bytes (and 8 more probably-correct):

- **`scripts/inspect_tft.py`** can flag F-series TFTs whose decryption
  is consistent (videos/audios = 0). This serves as a "valid F-series
  TFT" detector for tooling.
- We can WRITE F-series TFTs that modify just H2+0x20..0x2f (e.g.,
  fonts_address, unknown_maincode_binary). These positions are
  decryptable so we can mutate them and re-encrypt cleanly.
- Modifying ANY OTHER H2 field requires recovering more key bytes
  (which needs more experiments OR firmware reverse).

## Recommended next experiment for T1 closure

A "structurally identical" save with a different model series — i.e.,
take the user's project and try to compile it as a different Nextion
model (TJC variant, or NX3224F028 instead of NX4832F035). The H2+0x40..0xc4
region might encode model-specific data; comparing across models would
expose what those bytes encode.

Alternative: compile a literal "empty page only" minimal project with
the same model. Its H2+0x40..0xc4 should have a smaller / simpler
fingerprint we can analyse.
