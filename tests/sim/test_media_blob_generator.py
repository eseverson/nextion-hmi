"""Byte-exact whole media-blob generation (work-item #4).

``scripts/lib/media_blob_generator`` assembles a page's complete media
blob (``hexpos`` image) from its three generated sections — name-hash
table, frame image, page-init chain.  Verified against
``source/nextion.hmi.tft``: pages 1/2/3 byte-exact, page 0 exact except the
4-byte timer ``L`` re-arm address (back-patched by the usercode assembler).
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.lib.tft_attrs import (  # noqa: E402
    parse_appinf1_corrected, parse_page_directory, parse_objxinxi,
)
from scripts.lib.h2_cipher import encrypt as h2_decrypt  # noqa: E402
from scripts.lib.hmi_model import load_hmi_pages  # noqa: E402
from scripts.lib.tft_attrs_encoder import APP_ATT_NAMES  # noqa: E402
from scripts.lib.media_blob_generator import (  # noqa: E402
    page_record_counts, build_media_blob,
)

H2_START, H2_END = 0xC8, 0xC8 + 196
SENTINEL = 0xFFFFFFFF
HMI_PATH = REPO_ROOT / "source" / "nextion.hmi.HMI"
TFT_PATH = REPO_ROOT / "source" / "nextion.hmi.tft"


def _load():
    data = TFT_PATH.read_bytes()
    model_crc = int.from_bytes(data[0x2E:0x32], "little")
    a1 = parse_appinf1_corrected(h2_decrypt(data[H2_START:H2_END], model_crc))
    return data, a1


def _evstart(objs, pd, p):
    po = objs[pd[p]["objstar"]:pd[p]["objstar"] + pd[p]["objqyt"]]
    return min(s for o in po
               for s in struct.unpack_from("<6I", o.raw, 4) if s != SENTINEL)


def _attr_lookup(objs, pd, hmi, pi):
    po = objs[pd[pi]["objstar"]:pd[pi]["objstar"] + pd[pi]["objqyt"]]
    byname = {hc.name: o for hc, o in zip(hmi[pi].components, po)}

    def lookup(comp, attr):
        return byname[comp].slots[APP_ATT_NAMES.index(attr)]

    return lookup


def test_page_record_counts_match():
    data, a1 = _load()
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    strd = a1["strdataaddr"]
    hmi = load_hmi_pages(HMI_PATH)
    actual = []
    for i in range(len(pd)):
        end = pd[i + 1]["attdataaddr_rel"] if i + 1 < len(pd) else a1["pageadd"] - strd
        actual.append((end - pd[i]["attdataaddr_rel"]) // 24)
    assert page_record_counts(hmi) == actual


def test_media_blobs_byte_exact():
    data, a1 = _load()
    strd = a1["strdataaddr"]
    pd = parse_page_directory(data, a1["pageadd"], a1["pageqyt"])
    objs = parse_objxinxi(data, a1["objxinxiadd"], a1["objqyt"])
    hmi = load_hmi_pages(HMI_PATH)
    counts = page_record_counts(hmi)

    for pi in range(4):
        end = _evstart(objs, pd, pi + 1) if pi + 1 < len(pd) else a1["staticstrBeg"]
        live = data[strd + pd[pi]["hexpos"]:strd + end]
        blob = build_media_blob(hmi[pi], pi, counts, _attr_lookup(objs, pd, hmi, pi))
        diffs = [i for i in range(min(len(blob), len(live))) if blob[i] != live[i]]
        if pi == 0:
            # Page 0: only the timer L-marker address (4c 20 03 <u32>) differs.
            l_pos = blob.find(bytes.fromhex("4c2003"))
            assert l_pos != -1
            assert all(l_pos + 3 <= d < l_pos + 7 for d in diffs), diffs
        else:
            assert diffs == [], f"page {pi} media blob mismatch at {diffs[:4]}"
