# Loop bytecode (T6) + F-series schema deviation

## T6: loop bytecode partially decoded

Experiment 16 inserted 50 bytes of new bytecode at usercode+0x1425
(after aligning past the 4-byte directory shift caused by the new
`int qq` variable declaration).

Parsed bytes:

```
+0x00 a5 0d 00 00       block-size header (was 0x0DA1, now 0x0DA5 = +4)
+0x04 12 00 00 00       inner block size: 18
+0x08 09 00 04          opcode 0x00 size 4 = `i` (cjmp / conditional jump) ✓
+0x0b 05 0c 00 00 00    local var ref → local 0x0c (variable `qq`)
+0x10 2c                ',' (operand separator)
+0x11 35                ASCII '5' (loop limit literal)
+0x12 2c                ','
+0x13 32                ASCII '2' (??)
+0x14 2c                ','
+0x15 03 1c 00 00 00    int literal 28 (likely a jump target / offset)
+0x1a 0d                ??
+0x1b 00 00 00          ??
+0x1e 00 05 0c 00 00 00 local var qq again
+0x24 3d                '=' (assignment)
+0x25 05 0c 00 00 00    local var qq
+0x2a 2b                '+' (addition)
+0x2b 31                ASCII '1' (increment value)
+0x2c 07 00 00 00       block-size: 7
+0x30 54 20 03          `jmp` opcode (`0x2054` per Path C) — backward jump
```

**Confirmed opcodes for control flow:**
- `09 00 04` = `cjmp` (per Path C — `i` in size 4)
- `54 20 ...` = `jmp` (backward jump; per Path C)

**New insight:** loop literals use ASCII characters not int literals.
The condition `qq < 5` compiles with the literal `5` as ASCII byte
0x35, not as a 4-byte int literal `03 05 00 00 00`. Same for the
increment value `1` → 0x31. This matches Path C's note that operators
are ASCII bytes; literals in compact contexts use ASCII too.

T6 is now cracked in principle. Full decoding into a proper opcode
table would need a few more loop variants (`while`, `for`, nested,
break/continue) but the core mechanism is clear.

## F-series H2 schema deviates from T0/K0

Building on [L](L-h2-cipher-cracked-half.md) and [M](M-loop-bytecode-and-pic.md).
Plaintext-XOR-delta analysis of base→exp15 (Picture added):

```
H2+0x00..0x0f : ae 07 00 00 b2 00 00 00 00 00 00 00 9a 31 00 00
H2+0x10..0x1f : 00 00 00 00 00 00 0e 00 ba 03 1a 00 f2 06 2a 00
H2+0x20..0x2f : (all zeros — videos/audios/fonts addresses unchanged)
H2+0x30..0x3f : b4 84 04 00 00 00 00 00 00 00 01 00 03 00 00 00
H2+0x40..0xc4 : (all zeros — confirms TFTTool's "unmodelled" region is
                actually static / project-fingerprint data)
```

Key insights:

- **`pictures_count` is at H2+0x3a in F-series**, NOT H2+0x34 as
  TFTTool's T0/K0 schema says. The XOR delta is exactly `01 00` (+1)
  matching pictures_count going from 0 → 1.
- **`unknown_objects_count` (or some count field) is at H2+0x3c** and
  grew by 3 when the Picture was added (3 internal objects per
  picture? unclear).
- The H2+0x40..0xc4 region is **not random padding** — it stays
  byte-identical across this experiment (and the file_size-only
  experiments). It's likely a **project fingerprint** (signature, hash,
  or static project metadata) that doesn't change for cosmetic edits.
  This contradicts TFTTool's "fill with FF on save" assumption for
  T0/K0.
- **H2+0x10..0x17 are unchanged** — likely `unknown_pages_address`
  (no pages added) and similar.
- **H2+0x18..0x1f are non-zero deltas** — likely two address fields
  that shifted with the layout. Values 0x001A03BA and 0x002A06F2 don't
  match the file_size delta cleanly, so they're probably internal
  pointers/hashes, not simple addresses.

## Path forward for full F-series schema

To complete the schema map, we'd want experiments that change ONE
specific field at a time:
- Change just pages_count: add ONE empty page (cracks H2+0x34..0x35
  if pages_count is there — currently unverified location).
- Change just unknown_objects_count: add a Number component (different
  type that adds different objects).
- Change usercode_address only: would need a way to grow resources
  without changing usercode (impossible with editor-only edits).

For T1 (full key recovery), each newly-discovered field gives 2-4 more
known-plaintext bytes per experiment. The half-key gap can be closed.
