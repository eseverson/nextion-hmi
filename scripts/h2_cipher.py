#!/usr/bin/env python3
"""h2_cipher.py — F-series TFT H2 cipher (HmiSafeAppfree11Encode/DecData)

Transcribed from achmi.dll subcommand 0x21 (DecData) at VA 0x10004ec0.
Subcommand 0x20 (Encode) is the symmetric inverse at VA 0x10004cb0.

The cipher is a stateful 3-stage mix over 4-byte words, parameterised by
the device's ModelCRC (uint32). State is a 16-byte K block plus an 8-step
counter. The forward (encrypt) and reverse (decrypt) directions are
identical except that the order of operations within each mix step is
inverted: encrypt does `out = (in - X - Y) ^ Z`, decrypt does
`in = (out ^ Z) + Y + X`.

Both length and start position must be 4-byte aligned. Lengths are rounded
DOWN to a multiple of 4 (mirrors `length &= ~3` in the original).
"""
from __future__ import annotations
import struct


# Initial 16-byte K block (4 little-endian dwords from the disasm).
_K_INIT = (0x924f6584, 0xfbad3bbf, 0x582659b6, 0xbd829c9c)


def _u32(x: int) -> int:
    return x & 0xFFFFFFFF


def _u8(x: int) -> int:
    return x & 0xFF


def _crypt(data: bytes, model_crc: int, *, decrypt: bool) -> bytes:
    """Run the H2 cipher in either direction. data length is rounded down to 4."""
    n = len(data) & ~3
    out = bytearray(data[:n])

    # K block as a mutable 16-byte array (little-endian dwords stored byte-wise).
    K = bytearray(16)
    struct.pack_into("<IIII", K, 0, *_K_INIT)

    counter = 0
    prev_K0 = _K_INIT[0]      # eax in iter 1; reloaded from K[0..3] at the end of each iter
    state = model_crc & 0xFFFFFFFF  # The asm overwrites [ebp+0x10] (ModelCRC slot) with `mul`
                                    # at the end of each iter, so subsequent iters see state, not
                                    # the original ModelCRC.

    for idx in range(0, n, 4):
        in_word = struct.unpack_from("<I", out, idx)[0]

        key = K[8 + counter]  # K[8..15], cycling

        # mul = state * 2, then mul[idx & 3] += key ^ 0xbc
        mul = _u32(state * 2)
        m = bytearray(struct.pack("<I", mul))
        m[idx & 3] = _u8(m[idx & 3] + (key ^ 0xBC))
        mul = struct.unpack("<I", m)[0]

        # K[0..3] dword <<= 2; then K[3 - (key&3)] += key ^ 0x58
        k03 = _u32(struct.unpack_from("<I", K, 0)[0] << 2)
        struct.pack_into("<I", K, 0, k03)
        K[3 - (key & 3)] = _u8(K[3 - (key & 3)] + (key ^ 0x58))

        # Snapshot pre-mutation K[4..7] for the mix steps below.
        K47_pre = struct.unpack_from("<I", K, 4)[0]

        # K[4..7] dword <<= 3 (mutation; mix steps below use the snapshot).
        struct.pack_into("<I", K, 4, _u32(K47_pre << 3))

        # K[7 - (key&3)] += key ^ 0x7a
        K[7 - (key & 3)] = _u8(K[7 - (key & 3)] + (key ^ 0x7A))

        # Three mixing values. m3 uses `state` (the ModelCRC slot AS IT WAS at iter start),
        # not the next-iter state.
        K0_post = struct.unpack_from("<I", K, 0)[0]
        m1 = _u32(~_u32((K0_post + mul) ^ K47_pre))
        m1 = _u32(m1 - idx - 8)
        m2 = _u32(K47_pre + prev_K0 + mul)
        m3 = _u32(_u32(K47_pre << (key & 7)) + prev_K0 + state)

        # Apply the mixes (encrypt = `(in - X - Y) ^ Z` per stage; decrypt swaps).
        if not decrypt:
            x = in_word
            x = _u32((x - m1 - K47_pre) ^ m1)
            x = _u32((x - m2 - prev_K0) ^ m2)
            x = _u32((x - m3 - state) ^ m3)
        else:
            x = in_word
            x = _u32(((x ^ m3) + m3) + state)
            x = _u32(((x ^ m2) + m2) + prev_K0)
            x = _u32(((x ^ m1) + m1) + K47_pre)

        struct.pack_into("<I", out, idx, x)

        # Tail: advance state. `state` (= [ebp+0x10] = ModelCRC slot) gets overwritten with
        # mul; the next iter's m3 will use that. K[0..3] post-mutation flows into prev_K0.
        counter = (counter + 1) & 7
        prev_K0 = struct.unpack_from("<I", K, 0)[0]
        state = mul

    return bytes(out) + data[n:]


def encrypt(data: bytes, model_crc: int) -> bytes:
    """Encrypt data in-place. Length is rounded down to 4 bytes."""
    return _crypt(data, model_crc, decrypt=False)


def decrypt(data: bytes, model_crc: int) -> bytes:
    """Decrypt data. Length is rounded down to 4 bytes."""
    return _crypt(data, model_crc, decrypt=True)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("-m", "--model-crc", type=lambda s: int(s, 0), required=True)
    p.add_argument("--offset", type=lambda s: int(s, 0), default=0)
    p.add_argument("--length", type=lambda s: int(s, 0), default=0xC8)
    p.add_argument("-o", "--output")
    p.add_argument("-d", "--decrypt", action="store_true")
    args = p.parse_args()

    raw = open(args.input, "rb").read()
    chunk = raw[args.offset:args.offset + args.length]
    fn = decrypt if args.decrypt else encrypt
    out = fn(chunk, args.model_crc)
    print(f"input  [{args.offset:#x}..+{args.length:#x}]: {chunk[:32].hex()}")
    print(f"output [{args.offset:#x}..+{args.length:#x}]: {out[:32].hex()}")
    if args.output:
        new = bytearray(raw)
        new[args.offset:args.offset + args.length] = out
        open(args.output, "wb").write(bytes(new))
        print(f"wrote {args.output}")
