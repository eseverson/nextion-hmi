"""tft_bytecode — disassembler for per-component init bytecode.

Each component on a page has an init bytecode block at
`strdataaddr + PianyiData[+0x34]`. The block is length-prefixed (u32)
and uses a hybrid opcode/ASCII format:

    <u32 length>            — block size (excluding the 4-byte length itself)
    <opcode_byte_3>         — 3-byte opcode header, e.g. `09 1d 08`
    <ASCII args>            — comma-separated args for the call, e.g.
                              "0,20,160,50," (component x,y,w,h)
    <5-byte LOAD ops>       — `01 XX YY 00 00` = LOAD u32(0xYYXX), where
                              the loaded value is an attribute ID
                              (an index into the per-page attribute
                              record table)
    <2c=',' separators>     — between args
    <other literal ASCII>   — `1`, `0`, etc. for fixed args

Observed opcodes (first 3 bytes of bytecode):

    09 0d 04   page-meta (Page type 121)        — init the page itself
    09 1d 08   XFloat (59) / Text (116) /       — visible widget with
               ScrollingText (55)                  ~9-10 attrs
    09 1c 08   alternative widget init (TFTTool
               variant for some fonts/firmware)
    09 00 04   Button (98) / Dual-State (53)
    09 01 04   Picture (112)
    09 08 08   QR Code (58)
    09 0a 04   q0 / type 113
    04 04 05   Checkbox (56) / Radio (57)       — very short opcode
                                                  with `=N/M` ascii

Empty bytecode (length=0) is observed for: Hotspot (109), Timer (51),
Variable (52), Slider (1), Waveform (0), CropPicture (5). These types
have their attribute values set elsewhere (Slider/Button records,
Variable val array, picxinxiadd, etc.).

The LOAD placeholders refer to **attribute IDs**, not values. The
actual `bco`/`pco`/`val` data lives in an attribute-records region
that maps attr ID → value; that region's exact layout is still being
mapped (see findings/R for status).

This module currently:
- Parses the high-level structure (opcode + ASCII args + LOADs).
- Exposes `disassemble(block)` returning a list of instructions.
- Does NOT yet resolve attribute IDs to values — that needs the value
  table decoded.
"""
from __future__ import annotations
from dataclasses import dataclass
import struct


@dataclass
class Instr:
    """One disassembled instruction."""
    kind: str         # 'op', 'ascii', 'load', 'sep'
    bytes: bytes
    payload: object   # for 'load': int; for 'ascii': str; for 'op': tuple


def disassemble(block: bytes) -> list[Instr]:
    """Disassemble one component's bytecode block (NOT including the
    leading 4-byte length prefix). Returns a list of instructions in
    file order.
    """
    out: list[Instr] = []
    n = len(block)
    if n == 0:
        return out

    # First 3 bytes are the opcode header.
    if n >= 3:
        out.append(Instr(kind="op", bytes=block[:3], payload=tuple(block[:3])))
        i = 3
    else:
        # Tiny block — bail out as raw bytes.
        out.append(Instr(kind="ascii", bytes=block, payload=block.decode("latin-1", errors="replace")))
        return out

    # Walk the body. Collect ASCII runs; surface `01 XX YY 00 00` as
    # LOAD ops and `2c` as separators.
    ascii_buf = bytearray()

    def flush_ascii():
        if ascii_buf:
            try:
                s = bytes(ascii_buf).decode("ascii")
            except UnicodeDecodeError:
                s = bytes(ascii_buf).decode("latin-1", errors="replace")
            out.append(Instr(kind="ascii", bytes=bytes(ascii_buf), payload=s))
            ascii_buf.clear()

    while i < n:
        # 5-byte LOAD u32: `01 XX YY 00 00`
        if i + 5 <= n and block[i] == 0x01 and block[i + 3] == 0 and block[i + 4] == 0:
            flush_ascii()
            v = struct.unpack_from("<I", block, i + 1)[0]
            out.append(Instr(kind="load", bytes=block[i:i + 5], payload=v))
            i += 5
            continue
        # 5-byte LOAD with u32 (alternative form): `03 XX XX YY YY` —
        # observed on Button/QRCode bytecode (e.g. `03 93 00 00 00`).
        if i + 5 <= n and block[i] == 0x03:
            flush_ascii()
            v = struct.unpack_from("<I", block, i + 1)[0]
            out.append(Instr(kind="load", bytes=block[i:i + 5], payload=v))
            i += 5
            continue
        b = block[i]
        if b == 0x2c:
            flush_ascii()
            out.append(Instr(kind="sep", bytes=bytes([0x2c]), payload=","))
            i += 1
            continue
        if 0x20 <= b < 0x7f:
            ascii_buf.append(b)
            i += 1
            continue
        # Unknown byte — flush ASCII, surface as raw
        flush_ascii()
        out.append(Instr(kind="ascii", bytes=bytes([b]), payload=f"\\x{b:02x}"))
        i += 1

    flush_ascii()
    return out


def format_instrs(instrs: list[Instr]) -> str:
    """Pretty-print a disassembly for human inspection."""
    parts = []
    for ins in instrs:
        if ins.kind == "op":
            parts.append(f"OP[{ins.payload[0]:02x} {ins.payload[1]:02x} {ins.payload[2]:02x}]")
        elif ins.kind == "load":
            parts.append(f"LOAD({ins.payload})")
        elif ins.kind == "sep":
            parts.append(",")
        elif ins.kind == "ascii":
            parts.append(repr(ins.payload))
        else:
            parts.append(f"<{ins.kind}: {ins.bytes.hex()}>")
    return " ".join(parts)


# Opcode mnemonics for known patterns. Keyed by the 3-byte opcode.
OPCODE_NAMES = {
    (0x09, 0x0d, 0x04): "PAGE_INIT",
    (0x09, 0x1d, 0x08): "WIDGET_INIT_9",   # XFloat/Text/ScrollingText
    (0x09, 0x1c, 0x08): "WIDGET_INIT_8",
    (0x09, 0x00, 0x04): "BUTTON_INIT",     # Button + DualStateButton
    (0x09, 0x01, 0x04): "PICTURE_INIT",
    (0x09, 0x08, 0x08): "QRCODE_INIT",
    (0x09, 0x0a, 0x04): "Q0_INIT",         # type 113 (untyped)
    (0x04, 0x04, 0x05): "CHECKBOX_INIT",   # Checkbox + Radio
}


def opcode_name(opcode_bytes: tuple) -> str:
    return OPCODE_NAMES.get(opcode_bytes, f"OP[{opcode_bytes[0]:02x} "
                                          f"{opcode_bytes[1]:02x} {opcode_bytes[2]:02x}]")


if __name__ == "__main__":
    # Self-test: disassemble a known XFloat block.
    sample = bytes.fromhex(
        "091d08302c32302c3136302c35302c01370000002c01390000002c01380000002c"
        "013a0000002c013b0000002c312c013f0000002c01400000002c01410000002c302c30"
    )
    instrs = disassemble(sample)
    print(format_instrs(instrs))
