#!/usr/bin/env python3
"""
Dump a TFT file's headers + Usercode using TFTTool without aborting when the
editor version is unsupported. Writes a JSON-ish text report to stdout.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "TFTTool"))

from TFTTool import TFTFile, hexStr  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("usage: dump_tft.py <tft>", file=sys.stderr)
        sys.exit(2)
    path = Path(sys.argv[1])
    raw = path.read_bytes()
    # decode_usercode=False avoids the hard error when no instruction set matches.
    tft = TFTFile(raw, hexVals=True, decode_usercode=False)
    out = {
        "model": tft.model,
        "editor_version": tft.getEditorVersionStr(),
        "header2_encrypted": tft.header2.encrypted,
        "file_size_actual": len(raw),
        "Header1": {k: hex(v) if isinstance(v, int) else v for k, v in tft.header1.content.items()},
        "Header2": {k: hex(v) if isinstance(v, int) else v for k, v in tft.header2.content.items()},
        "Header1_CRC": hex(tft.header1.crc) if hasattr(tft.header1, "crc") else None,
        "Header2_CRC": hex(tft.header2.crc) if hasattr(tft.header2, "crc") else None,
        "tail_crc": hex(int.from_bytes(raw[-4:], "little")),
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
