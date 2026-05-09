#!/usr/bin/env python3
"""inspect_hmi.py — structured dump of an HMI file.

Loads an HMI through the simulator's loader and prints (or emits as JSON)
a summary of its pages, components, event handlers, fonts, and Program.s.
Pair with `diff` (or `jq`) to compare two saves at a higher level than
raw bytes.

Usage:
    scripts/inspect_hmi.py source/nextion.hmi.HMI
    scripts/inspect_hmi.py path.HMI --json > dump.json
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sim.loader import load_hmi  # noqa: E402


def _component_summary(c) -> dict:
    a = c.attrs
    out = {
        "name": c.name,
        "id": c.id,
        "type": c.type,
        "vscope": a.get("vscope"),
    }
    for k in ("x", "y", "w", "h", "bco", "pco", "font",
              "txt", "val", "vvs0", "vvs1", "tim", "en", "vis"):
        if k in a and a[k] not in (None, ""):
            out[k] = a[k]
    if c.events:
        out["events"] = sorted(c.events.keys())
    return out


def _summary(state) -> dict:
    pages = {}
    for name, p in state.pages.items():
        pages[name] = {
            "id": p.id,
            "size": [p.attrs.get("w"), p.attrs.get("h")],
            "bco": p.attrs.get("bco"),
            "events": sorted(p.events.keys()),
            "components": [_component_summary(c) for c in p.components],
        }
    fonts = {}
    for fid, f in (state.fonts or {}).items():
        fonts[fid] = {
            "width": getattr(f, "width", None),
            "height": getattr(f, "height", None),
            "name": getattr(f, "name", None),
        }
    return {
        "pages": pages,
        "fonts": fonts,
        "program_s_lines": [l for l in (state.program_s or "").splitlines() if l.strip()],
    }


def _print_text(s: dict) -> None:
    for name, p in s["pages"].items():
        print(f"page {p['id']:>2}  {name:<12} {p['size'][0]}x{p['size'][1]} "
              f"bco={p['bco']} events={p['events']} ({len(p['components'])} components)")
        for c in p["components"]:
            extras = []
            if "txt" in c:
                extras.append(f"txt={c['txt']!r}")
            if "val" in c:
                extras.append(f"val={c['val']}")
            if c.get("events"):
                extras.append(f"ev={c['events']}")
            extra = " ".join(extras)
            box = ""
            if "x" in c:
                box = f"({c['x']},{c['y']},{c['w']}x{c['h']})"
            print(f"    [{c['id']:>3}] type={c['type']:<3} {c['name']:<10} "
                  f"{box} bco={c.get('bco')} pco={c.get('pco')} {extra}")
    if s["fonts"]:
        print("fonts:")
        for fid, f in s["fonts"].items():
            print(f"  {fid}: {f}")
    if s["program_s_lines"]:
        print(f"Program.s ({len(s['program_s_lines'])} live lines):")
        for line in s["program_s_lines"]:
            print(f"  {line}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("hmi")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON for diffing instead of human text.")
    args = ap.parse_args()

    state = load_hmi(args.hmi)
    s = _summary(state)
    if args.json:
        print(json.dumps(s, indent=2, default=str, sort_keys=True))
    else:
        _print_text(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
