#!/usr/bin/env python3
"""author — from-scratch Nextion authoring: project IR → ``.tft`` + ``main.HMI``.

This is the end-to-end author path (me/miata-dash#2 / findings/next-steps.md
item 6).  It composes the already-byte-exact building blocks — it does NOT
reimplement them:

* IR → ``.tft``:  reconstruct the compiler's page models from the IR
  (:func:`project_ir.pages_from_ir`) and drive
  :func:`compile_hmi.compile_from_model` — the identical pipeline the
  HMI→TFT compiler uses, so the output inherits its byte-for-byte guarantee.
* IR → ``main.HMI``:  :func:`main_hmi.write_main_hmi` from the IR's manifest
  fields.

The fonts/pictures resources blob (``0x10000``..``0x70000``) + ``Modelcrc``
are explicitly OUT OF SCOPE; they come from a reference ``.tft``
(``--resources``) and the IR's ``model_crc``, consistent with
:func:`compile_hmi.compile_hmi`.

CLI::

    nxt author <ir.yaml|ir.json> --resources <ref.tft> -o <out_prefix>

emits ``<out_prefix>.tft`` and ``<out_prefix>.HMI`` (the main.HMI manifest
blob).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_LIB_DIR = Path(__file__).resolve().parent / "lib"
_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_REPO_ROOT), str(_LIB_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def author_tft(ir, resources_source) -> bytes:
    """Emit the ``.tft`` bytes for a :class:`project_ir.ProjectIR`.

    ``resources_source`` is a reference ``.tft`` of the same model/font
    composition — its resources prefix + resources-coupled appinf1 fields +
    editor-version profile are reused (out of scope), exactly as in
    :func:`compile_hmi.compile_hmi`.
    """
    from scripts.lib.compile_hmi import compile_from_model
    from scripts.lib.project_ir import pages_from_ir

    pages = pages_from_ir(ir)
    return compile_from_model(pages, ir.program_s, resources_source)


def author_main_hmi(ir) -> bytes:
    """Emit the ``main.HMI`` manifest blob for a :class:`project_ir.ProjectIR`."""
    from scripts.lib.main_hmi import write_main_hmi

    mh = ir.main_hmi
    return write_main_hmi(
        model_crc=mh.model_crc,
        resources=mh.resources,
        guidire=mh.guidire,
        encode=mh.encode,
        upver0=mh.upver0,
        upver1=mh.upver1,
        picencodever=mh.picencodever,
    )


def author(ir_path, resources_source, out_prefix) -> tuple[Path, Path]:
    """Author ``<out_prefix>.tft`` + ``<out_prefix>.HMI`` from an IR file.

    Returns the two written paths.
    """
    from scripts.lib.project_ir import load_ir

    ir = load_ir(ir_path)
    tft_bytes = author_tft(ir, resources_source)
    main_hmi_bytes = author_main_hmi(ir)

    out_prefix = Path(out_prefix)
    tft_path = out_prefix.with_suffix(".tft")
    hmi_path = out_prefix.with_suffix(".HMI")
    tft_path.write_bytes(tft_bytes)
    hmi_path.write_bytes(main_hmi_bytes)
    return tft_path, hmi_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="nxt author",
        description="From-scratch author: project IR -> .tft + main.HMI.",
    )
    ap.add_argument("ir", help="project IR (.yaml/.yml/.json)")
    ap.add_argument("-o", "--output", required=True,
                    help="output path prefix (writes <prefix>.tft and <prefix>.HMI)")
    ap.add_argument("--resources", required=True,
                    help="reference .tft (same model/font composition); its "
                         "resources prefix + resources-coupled appinf1 fields "
                         "are reused (out of scope).")
    args = ap.parse_args(argv)

    tft_path, hmi_path = author(args.ir, args.resources, args.output)
    print(f"authored {args.ir} -> {tft_path} ({tft_path.stat().st_size} bytes) "
          f"+ {hmi_path} ({hmi_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
