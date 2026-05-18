#!/usr/bin/env python3
"""list_components_tft.py — list every component in a .tft with the
component's name inferred from (lei, position-among-same-lei-on-page).

The Nextion runtime's serial protocol addresses components by name
(e.g. ``x9.val=42``). Where those names live in the TFT isn't yet
mapped (see findings/next-steps.md "Last blocker") — but the most
plausible hypothesis is that names are derived at runtime from
``(type_prefix, position-on-page-among-components-of-same-lei)``. This
tool prints the inferred name under that hypothesis so you can verify
against the project's authored names.

The convention used:

  lei → prefix       Page=121 → "page", XFloat=59 → "x",
                     Text=116 → "t", Hotspot=109 → "m",
                     ProgVar=106 → "j", Variable=52 → "v",
                     Timer=51 → "tm", ...

  inferred_name = "{prefix}{position_among_same_lei_on_page}"

Pages get their own naming (page0, page1, ...).

Usage:
    list_components_tft.py source.tft [--page N]
"""
from __future__ import annotations
import argparse
import struct
import sys
from pathlib import Path
from collections import defaultdict

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.h2_cipher import encrypt as h2_decrypt  # noqa: E402
from scripts.lib.tft_format import H2_START, H2_END, read_model_crc  # noqa: E402
from scripts.lib.tft_attrs import (  # noqa: E402
    parse_appinf1_corrected, parse_page_directory, parse_objxinxi,
)
from scripts.lib.tft_attrs_layout import LEI_TO_CLASS  # noqa: E402


# Map from class name → conventional serial-name prefix. Based on the
# names used in miata-dash's project (the only F-series project we have)
# and standard Nextion conventions.
CLASS_TO_PREFIX = {
    "GuiObjPage":         "page",
    "GuiObjXfloat":       "x",
    "GuiObjText":         "t",
    "Hotspot":            "m",
    "GuiObjProg":         "j",
    "GuiObjVari":         "v",
    "GuiObjTimer":        "tm",
    "GuiObjSlider":       "h",
    "GuiObjButton":       "b",
    "GuiObjButton_T":     "b",
    "GuiObjCheckBox":     "c",
    "GuiObjRadio":        "r",
    "GuiObjQrcode":       "qr",
    "GuiObjPic":          "p",
    "GuiObjPicc":         "p",
    "GuiObjCurve":        "s",
    "GuiObjZhizhen":      "z",
    "GuiObjGText":        "g",
    "GuiObjTouchcap":     "tc",
    # VP variants and exotic types not commonly used:
    "GuiObjQrcodeVP":     "qrvp",
    "GuiObjTextVP":       "tvp",
    "GuiObjXfloatVP":     "xvp",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="source .tft")
    ap.add_argument("--page", type=int, default=None,
                    help="restrict listing to this page index")
    args = ap.parse_args()

    data = Path(args.input).read_bytes()
    a1 = parse_appinf1_corrected(
        h2_decrypt(data[H2_START:H2_END], read_model_crc(data)))
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    entries = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])

    print(f"{args.input}: {a1['pageqyt']} pages, {a1['objqyt']} objects")
    for page_idx, page in enumerate(pd):
        if args.page is not None and page_idx != args.page:
            continue
        print(f"\npage {page_idx}: objstar={page['objstar']} "
              f"objqyt={page['objqyt']}  "
              f"attdataaddr_rel=0x{page['attdataaddr_rel']:x}  "
              f"hexpos=0x{page['hexpos']:x}")
        # Count components of each lei on this page so we can assign
        # inferred names.
        lei_position: dict[int, int] = defaultdict(int)
        for obj_off in range(page["objstar"],
                             page["objstar"] + page["objqyt"]):
            e = entries[obj_off]
            cls = LEI_TO_CLASS.get(e.lei, f"lei{e.lei}")
            prefix = CLASS_TO_PREFIX.get(cls, "?")
            pos = lei_position[e.lei]
            inferred = f"{prefix}{pos}" if e.lei != 121 else f"page{page_idx}"
            lei_position[e.lei] += 1
            print(f"  obj{obj_off:>3}: lei={e.lei:>3} id={e.id:>3}  "
                  f"{cls:<18}  inferred={inferred!r:>10}  "
                  f"(x={e.endx - e.w + 1}, y={e.endy - e.h + 1}, "
                  f"w={e.w}, h={e.h})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
