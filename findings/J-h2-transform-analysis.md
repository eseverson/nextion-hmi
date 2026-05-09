# H2 transform analysis (T1 progress)

Building on G11 from batch 2. Question: what is the function that maps
H1 → H2 in F-series TFT files?

## Confirmed structure

Running `scripts/crack_h2_transform.py` over the experimental data:

| fs_Δ (file_size delta from baseline) | # experiments | H2 byte changes (excl CRC) | All identical? |
|---|---|---|---|
| 0    | 1 (01) | 0  | trivially |
| +12  | 5 (04, 05, 06, 08, 09) | 5  | yes — verified across all 5 |
| +24  | 1 (10) | 5  | n/a |
| +1044 | 1 (07) | 10 | n/a |
| +1180 | 7 (11 + iter1..6) | 10 | yes — verified |

So at the byte-position level, the transform is **fully deterministic
on file_size**. Change file_size by the same amount → identical H2 byte
positions and values change.

## Affected H2 positions per file_size byte

Comparing the +12 case (where only file_size **byte 0** changed) vs the
+1180 case (where bytes 0 and 1 both changed):

- **file_size byte 0** affects H2 positions: `0x00, 0x04, 0x0c, 0x18, 0x1c`
- **file_size byte 1** affects H2 positions: `0x01, 0x1d, 0x34, 0x38, 0x3a`

Each byte input affects **5 H2 byte positions**. The patterns interleave
(`0x00..0x01` close together, `0x18..0x1c..0x1d` close together) which
hints at a permutation network that distributes input bits across the
output region.

This is not a simple fixed-pad XOR. If it were, the XOR delta at each
affected position would equal the H1 byte XOR delta — but the observed
deltas don't match.

## Next-step attack vectors

The data already-in-hand isn't quite enough to crack the transform
fully — too many unknowns per equation. To bootstrap further:

1. **Two single-byte file_size mutations.** Changing fs by +1 vs +2 (or
   any two single-byte deltas) gives us more known-input/output pairs
   for the byte-0 input column. Today we only have +12 and the byte-0
   contribution of +1180 (which is `bc`).
2. **Mutating other H1 fields** — we know orientation (H1+0x14) doesn't
   propagate into H2. But other H1 fields (file_id, metadata_size,
   resource_address) likely do. Each non-propagating field is one we
   can rule out; each propagating one gives us its H2 column.
3. **Run TFTTool on a known T0-series file** to see the *plaintext* H2
   for a model where the XOR key is known (TFTTool's `_modelXORs` table
   has T0 keys). That reveals what H2 is *supposed* to look like
   plaintext, and we can then derive how F-series scrambles it.

## What this gives us today

Even partial: we know **H2 carries multiple copies / digests of H1's
file_size**. Each H1 byte input scrambles into 5 H2 byte outputs. So
H2 is a 200-byte block containing redundant encoded copies of a small
number of H1 fields.

For a *write* attack (e.g., wanting to mutate a field and produce a
valid TFT), we'd need to either:
- (a) Fully reverse-engineer the H2 function and apply it, or
- (b) Enumerate all bytes that need updating and brute-force the
  resulting H2 CRC fixup — feasible if we know the propagation map.

Option (b) is reachable today for limited mutations (e.g., pure
file_size adjustments and orientation flips, since orientation
doesn't propagate at all and file_size's propagation is now mapped).

## Action items added to the roadmap

- **T1 status updated**: still `[~]` but the diffusion map is now
  documented.
- New experiment proposal: "single-byte file_size mutations" — could
  be done by editing one Variable val by +1, giving us the +1 byte-0
  change without other noise.
