"""compute_appinf1 — pack and encrypt the H2 appinf1 struct.

Produces the 196-byte encrypted H2 payload from a body_layout dict.
The H2 region is then written at file offset 0xC8; a CRC-32/MPEG-2 of
those 196 bytes goes at 0x18C.
"""
from __future__ import annotations
import struct

# 14 u32s + 8 u16s + 2 u8s + 1 u16 = 76 bytes (0x4c)
_APPINF1_FMT = "<" + "I" * 14 + "H" * 8 + "BBH"
_APPINF1_SIZE = struct.calcsize(_APPINF1_FMT)   # 76
_H2_SIZE = 196                                   # encrypted region size


def _pack_appinf1(bl: dict) -> bytes:
    return struct.pack(
        _APPINF1_FMT,
        bl["staticstrBeg"],
        bl["AppAllvasAddr"],
        bl["AppAllvasQty"],
        bl["attdataaddr"],
        bl["resourcesfileddr"],
        bl["strdataaddr"],
        bl["pageadd"],
        bl["objxinxiadd"],
        bl["picxinxiadd"],
        bl["gmovxinxiadd"],
        bl["videoxinxiadd"],
        bl["wavxinxiadd"],
        bl["zimoxinxiadd"],
        bl["MainCodeHex"],
        bl["pageqyt"],
        bl["objqyt"],
        bl["picqyt"],
        bl["gmovqyt"],
        bl["videoqyt"],
        bl["wavqyt"],
        bl["zimoqyt"],
        0,              # res1
        bl["encode"],
        0,              # res2
        0,              # res3
    )


def compute_appinf1(body_layout: dict) -> bytes:
    """Pack the 76-byte appinf1 struct from body_layout, append 120 xFF bytes,
    encrypt with h2_cipher, and return the 196-byte H2 payload.

    body_layout keys (all int, all required):
        staticstrBeg, AppAllvasAddr, AppAllvasQty,
        attdataaddr,
        resourcesfileddr,   # default 0x10000
        strdataaddr,        # default 0x70000
        pageadd, objxinxiadd, picxinxiadd,
        gmovxinxiadd, videoxinxiadd, wavxinxiadd, zimoxinxiadd,
        MainCodeHex,
        pageqyt, objqyt, picqyt, gmovqyt, videoqyt, wavqyt, zimoqyt,
        encode
    """
    # h2_cipher names are asm-verbatim: encrypt() = DecData (read direction),
    # decrypt() = Encode (write direction). Use decrypt() to produce ciphertext.
    from h2_cipher import decrypt as _h2_encode
    plain = _pack_appinf1(body_layout)
    assert len(plain) == _APPINF1_SIZE, len(plain)
    plain += b"\xff" * (_H2_SIZE - _APPINF1_SIZE)   # 120 bytes of padding
    model_crc = body_layout["model_crc"]
    return _h2_encode(plain, model_crc)


def _unpack_appinf1(a: bytes) -> dict:
    vals = struct.unpack(_APPINF1_FMT, a[:_APPINF1_SIZE])
    keys = [
        "staticstrBeg", "AppAllvasAddr", "AppAllvasQty", "attdataaddr",
        "resourcesfileddr", "strdataaddr", "pageadd", "objxinxiadd",
        "picxinxiadd", "gmovxinxiadd", "videoxinxiadd", "wavxinxiadd",
        "zimoxinxiadd", "MainCodeHex",
        "pageqyt", "objqyt", "picqyt", "gmovqyt", "videoqyt", "wavqyt",
        "zimoqyt", "_res1",
        "encode", "_res2", "_res3",
    ]
    return dict(zip(keys, vals))


def _run_fixture(path: str, label: str) -> bool:
    import tft_format

    raw = open(path, "rb").read()
    hdr = tft_format.parse(raw)
    appinf1_bytes = hdr.appinf1        # 76 decrypted bytes

    bl = _unpack_appinf1(appinf1_bytes)
    # Remove internal reserved keys; add model_crc (needed by cipher).
    for k in ("_res1", "_res2", "_res3"):
        bl.pop(k)
    bl["model_crc"] = hdr.model_crc

    got = compute_appinf1(bl)
    want = raw[0xC8:0x18C]

    if got == want:
        print(f"PASS  {label}")
        return True
    else:
        print(f"FAIL  {label}")
        for i in range(0, len(got), 4):
            g = got[i:i+4]
            w = want[i:i+4]
            if g != w:
                print(f"  byte {i:#05x}: got {g.hex()}  want {w.hex()}")
        return False


if __name__ == "__main__":
    import os, sys

    # Locate the test fixtures relative to this script's repo root.
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)          # nextion/
    # tft_format uses `from scripts.h2_cipher import …`, so root must be on
    # sys.path. here (scripts/) must also be on sys.path for bare imports.
    sys.path.insert(0, root)
    sys.path.insert(0, here)

    fixtures_base = os.path.join(root, "tests", "editor outputs", "_old")
    fixtures = [
        (os.path.join(fixtures_base, "16_loop", "16.tft"),            "16_loop"),
        (os.path.join(fixtures_base, "17_more_components", "17.tft"), "17_more_components"),
        (os.path.join(fixtures_base, "15_picture", "15.tft"),         "15_picture"),
        (os.path.join(fixtures_base, "11_add_page", "11.tft"),        "11_add_page"),
        (os.path.join(fixtures_base, "01_orientation_flip", "01.tft"), "01_orientation_flip"),
    ]

    results = [_run_fixture(path, label) for path, label in fixtures]
    sys.exit(0 if all(results) else 1)
