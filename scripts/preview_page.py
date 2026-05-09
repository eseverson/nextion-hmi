#!/usr/bin/env python3
"""
preview_page.py — Render Nextion pages from an HMI file as PNG previews.

Procedural Linux preview tool. Loads the HMI via the Nextion2Text library and
walks each page's component tree, rendering visible components into a Pillow
image using their declared position, size, RGB565 colors, and default values.

Scope: static preview at editor-default values. Does NOT execute event-handler
scripts (codesload/codesup/etc.) or font-correct text from the project's .zi
files — text is rendered with a Liberation Mono substitute.

Usage:
    python3 scripts/preview_page.py [--hmi PATH] [--out DIR] [--scale N]

Outputs PNGs to --out (default: work/) named preview_<pagename>.png.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from PIL import Image  # noqa: E402

from sim.loader import load_hmi  # noqa: E402
from sim.renderer import Renderer  # noqa: E402


def render_page(page, scale: int = 1):
    """Backwards-compat shim. Renders are now driven by sim.renderer.Renderer."""
    raise NotImplementedError("preview_page.py now uses sim.renderer; call main()")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hmi", default=str(REPO_ROOT / "source" / "nextion.hmi.HMI"))
    ap.add_argument("--out", default=str(REPO_ROOT / "work"))
    ap.add_argument("--scale", type=int, default=1, help="Integer upscale factor")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    state = load_hmi(args.hmi)
    print(f"Loaded {args.hmi}: pages={len(state.pages)}")

    renderer = Renderer()
    rendered = 0
    for name, page in state.pages.items():
        state.active_page = page
        state.dirty = True
        img = renderer.render(state)
        if args.scale != 1:
            img = img.resize(
                (img.size[0] * args.scale, img.size[1] * args.scale),
                Image.NEAREST,
            )
        out = out_dir / f"preview_{name}.png"
        img.save(out)
        print(f"  rendered {name}: {img.size[0]}x{img.size[1]} -> {out}")
        rendered += 1

    print(f"done. {rendered} page(s) rendered to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
