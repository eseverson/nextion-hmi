#!/usr/bin/env python3
"""List the model XOR table from TFTTool, sorted."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "TFTTool"))
from TFTTool import TFTFile  # noqa: E402

for m, x in TFTFile._modelXORs.items():
    print(f"{m}  XOR={hex(x)}")
