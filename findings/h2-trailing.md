# H2 trailing 120 bytes (F-series)

Status: **resolved** as of 2026-05-10. The 120 bytes at
`H2[0x4c..0xc4]` (file offsets `[0x114..0x18c]` in the decrypted
plaintext) are **constant `0xff` padding** in every F-series TFT the
editor produces. They are not a fingerprint table, not a per-page
record array, and they carry no project-specific data.

Verification helper: [`scripts/lib/h2_trailing.py`](../scripts/lib/h2_trailing.py).
Round-trips all 27 fixtures in `tests/editor outputs/` plus
`source/nextion.hmi.tft` byte-for-byte. No exceptions.

## TL;DR

```
H2 plaintext layout (196 bytes):
+0x00..0x4c   appinf1 struct (76 bytes — see format-tft.md)
+0x4c..0xc4   0xff padding (120 bytes, project-invariant)
```

A writer emits `appinf1.structToBytes() + b"\xff" * 120` for the H2
plaintext, then runs it through `h2_cipher.encrypt` keyed on
`ModelCRC`. That ciphertext re-derives the original H2 region
byte-for-byte for every fixture tested.

## Why earlier analysis got confused

Previous notes (recorded in [`h2-cipher.md`](h2-cipher.md), open
follow-ups) saw "four ~32-byte rows that look like per-page/component
fingerprint records" with "two rows differing at only 5 byte positions
(4, 5, 8, 12, 13)". That analysis was looking at the **ciphertext**,
not the plaintext.

The H2 cipher is stateful and uses an 8-step counter (`c = (c + 1) & 7`),
so the cipher's mixing key cycles every 8 dwords = 32 bytes. Encrypting
a constant `0xff` input produces ciphertext that *appears* to repeat in
32-byte rows because the per-iteration state evolution is determined
by `K`, `prev_K0`, and `state`, which themselves cycle with the
counter. Small per-file ciphertext differences in the trailing region
(the "5 byte positions") come entirely from the cipher state propagated
out of the `appinf1` portion, which differs between projects (different
addresses, counts, etc.). The plaintext underneath those differences
is uniformly `0xff`.

## How the editor produces these bytes

`hmitype.dll!Myapp_inf.BianyiApp` is the TFT compile/output path. The
key sequence in C# (line numbers from the ilspycmd dump):

```csharp
// First: pre-fill the leading duiqisize block (default 0x10000 = 64 KB)
// with 0xff before writing any payload.
array = new byte[duiqisize];                    // line 73632
for (int i = 0; i < array.Length; i++)
    array[i] = byte.MaxValue;                   // 0xff fill
sw.Write(array, 0, array.Length);

// ... resources, fonts, pages, bytecode, etc. get written
//     starting at file offset duiqisize ...

// Finally: rewind to 0 and overlay a 400-byte header buffer that
// contains plaintext appinf0 and appinf1.
sw.lseek(0);                                    // line 73971
array = new byte[400];                          // zero-initialised
app.structToBytes().CopyTo(array, 0);           // appinf0 at [0..196)
app2.structToBytes().CopyTo(array, 200);        // appinf1 at [200..276)
sw.Write(array, 0, array.Length);
```

So as written by the managed code, file bytes `[276..400)` (which
includes the H2 trailing region `[0x114..0x18c]`) end up as **zeros**,
not 0xff. The H2 region at that point is plaintext: appinf1 followed
by 124 bytes of zeros.

The transformation that turns those trailing zeros into `0xff` happens
inside `HmiSafeWriteTFTFileSafe` (achmi.dll subcommand `0x23` at
VA `0x10005110`), which is invoked next:

```csharp
HmiSafe.HmiSafeWriteTFTFileSafe((CFIL*)csr, getFileCRCdelegate);
                                                // line 73996
```

This native routine is responsible for finalising the TFT — it
computes the H1 CRC, encrypts H2, computes the H2 CRC, and writes the
trailing 4-byte file CRC. Empirically (from the round-trip check) it
fills the post-appinf1 region with `0xff` before invoking the H2
encryption path (subcmd `0x20`). Whether it does an explicit
`memset(buf + 0x4c, 0xff, 0x78)` or whether it builds a fresh
`0xff`-prefilled buffer is unconfirmed at the assembly level — but the
observable invariant is exact.

(The same routine also does a "4-byte cycling XOR over a 0x4c-byte
sub-buffer" in the editor's TFT writer — that's the model-id
signature work on the *appinf1* portion, not the trailing region.)

## Worked example: baseline TFT

`tests/editor outputs/_old/base.tft`, `ModelCRC = 0x1ce47603`.

Decrypted H2 region (196 bytes, `h2_cipher.encrypt(cipher, mc)`):

```
[0x00..0x4c]  appinf1 (76 bytes — addresses, counts, encode)
[0x4c..0xc4]  ff ff ff ff ff ff ff ff   ff ff ff ff ff ff ff ff   <-- trailing
              ff ff ff ff ff ff ff ff   ff ff ff ff ff ff ff ff
              ff ff ff ff ff ff ff ff   ff ff ff ff ff ff ff ff
              ff ff ff ff ff ff ff ff   ff ff ff ff ff ff ff ff
              ff ff ff ff ff ff ff ff   ff ff ff ff ff ff ff ff
              ff ff ff ff ff ff ff ff   ff ff ff ff ff ff ff ff
              ff ff ff ff ff ff ff ff   ff ff ff ff ff ff ff ff
              ff ff ff ff ff ff ff ff
```

Corresponding ciphertext at file offset `[0xc8 + 0x4c .. 0xc8 + 0xc4)`
= `[0x114..0x18c]`:

```
33 43 b0 76 b3 aa a7 0f   b7 c9 6a 12 af 18 94 ac
21 4b c8 20 c2 20 7c 55   a3 5f 97 ba 32 60 86 31
f3 42 e0 9b b3 aa c7 a7   37 c6 2a d1 2f 18 14 0d
e1 4a c8 79 42 1f 7c 53   63 5f 97 8e f2 5f 86 09
b3 42 e0 cb 33 aa c7 87   37 c6 2a 11 2f 18 14 8d
a1 4a c8 79 42 1f 7c 53   a3 5e 97 8e b2 5f 86 09
73 43 e0 cb 33 aa c7 87   b7 c5 2a 11 af 17 14 8d
61 4b c8 79 c2 1e 7c 53
```

`h2_cipher.encrypt(b"\xff" * 120, ...)` won't produce that exact
ciphertext on its own, because the cipher's state at the start of the
trailing region depends on the preceding 76 bytes of appinf1. The
fixture-by-fixture round-trip in
[`scripts/lib/h2_trailing.py`](../scripts/lib/h2_trailing.py) confirms that
encrypting `appinf1 || 0xff*120` against ModelCRC always reproduces
the stored ciphertext.

## Writer recipe

```python
from scripts.h2_cipher import decrypt as h2_encrypt   # asm-name "decrypt"
                                                     # is the ENCRYPT direction
appinf1_bytes = pack_appinf1(...)                    # 76 bytes
h2_plaintext  = appinf1_bytes + b"\xff" * 120        # 196 bytes
h2_ciphertext = h2_encrypt(h2_plaintext, model_crc)  # 196 bytes
file[0xc8:0x18c] = h2_ciphertext
file[0x18c:0x190] = crc32_mpeg2(h2_ciphertext).to_bytes(4, "little")
```

(Note: the function literally named `encrypt` in `scripts/lib/h2_cipher.py`
is the asm-verbatim *DecData* routine — see the alias comment at
[`sim/tft_loader.py:54`](../sim/tft_loader.py). The function literally
named `decrypt` is the symmetric inverse used to *encrypt* plaintext
back into the file. Same call signature as the writer recipe above —
just be aware of the swapped naming.)

## Verification

All 27 TFT fixtures in the corpus round-trip cleanly under this
hypothesis:

```
$ python3 scripts/lib/h2_trailing.py
checking 27 fixtures...
  base.tft               trail=0xff: True  recipher_match: True
  01.tft                 trail=0xff: True  recipher_match: True
  04.tft                 trail=0xff: True  recipher_match: True
  ... (24 more) ...
  nextion.hmi.tft        trail=0xff: True  recipher_match: True

OK — 27 / 27 fixtures match
```

Inputs covered: baseline, orientation flip, Variable val change,
hotspot add, hotspot delete, page add, program changes, picture add,
loop construct, "more components" stress test, six save iterations of
the same project, plus older `_old/` fixtures (baud changes, dim
changes, sleep changes, vertical orientation) and the full project
under `source/`.

## Why TFTTool's "H2-nuke" still corrupted F-series files

TFTTool writes `0xff` into `H2[0x44..0xc4]` of the on-disk **ciphertext**,
not the plaintext. Since the H2 region is encrypted on F-series and
TFTTool's cipher is a no-op (its `_modelXORs[NX4832F035_011] = 0`),
overwriting 128 ciphertext bytes with `0xff` corrupts whatever
plaintext the H2 cipher would have produced under those bytes — both
the trailing region and the tail of appinf1. The fact that the
plaintext trailing region *happens* to be all-`0xff` doesn't save
TFTTool; the cipher is stateful and the ciphertext for plaintext-0xff
is not itself 0xff. F-series writers must encrypt
`appinf1 || 0xff*120` with the proper cipher to produce a valid file.

## What this unblocks

- **Lossless writes**: an F-series TFT writer can emit the H2 region
  without preserving any project-specific data beyond the 76-byte
  `appinf1` struct. The trailing region is a fixed constant.
- **From-scratch authoring**: no further fingerprint-table machinery is
  needed for the H2 region. The remaining authoring gaps are in the
  resource/body sections, not in the header.
- **TFTTool's H2-nuke bug** can now be patched cleanly: keep H2
  decrypted, only zero/preserve `appinf1` fields the editor doesn't
  set, fill 120 trailing bytes with `0xff`, re-encrypt and recompute
  the H2 CRC.

## What we couldn't figure out

- The exact instruction sequence inside `HmiSafeWriteTFTFileSafe`
  (achmi.dll subcmd `0x23`, VA `0x10005110`) that fills the trailing
  region with `0xff` before encrypting. We have no disassembly of
  `achmi.dll` checked into the repo, and the .NET-layer code clearly
  writes zeros at that position before handing off to the native
  routine. The 0xff fill therefore happens somewhere in the native
  subcmd 0x23 path. A targeted disassembly of `0x10005110` (looking
  for either a `memset` to a 120-byte region or a separate
  preallocation pattern) would confirm; the observable round-trip is
  already exact.

- Whether T0/K0/X3/X5-series TFTs also have a 120-byte trailing region
  with a different invariant. They use the older `HmiSafeAppfree10`
  cipher (subcmd `0x1f`), and TFTTool's existing T0/K0 schema treats
  the same region as `0xff` padding — which, by analogy with what we
  now know about F-series, may simply be correct on those series too.
  Not investigated here; out of scope.
