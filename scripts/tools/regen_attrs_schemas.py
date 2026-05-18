#!/usr/bin/env python3
"""regen_attrs_schemas.py — regenerate ``lib/tft_attrs_schemas.py`` from
``findings/attrs-raw.txt``.

``attrs-raw.txt`` lists every ``GuiObj<Kind>`` class with both the F-series
(T1) and the alternate-model attribute declarations. The F-series block
always appears first; the alternate block (when present) re-uses the same
attr names. We split on the first repeated attr name and keep only the
F-series block.

Run after re-extracting ``attrs-raw.txt`` from ``hmitype.dll``.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def parse(raw_text: str) -> dict[str, list[tuple[str, int, str]]]:
    out: dict[str, list[tuple[str, int, str]]] = {}
    blocks = re.split(r"^== (GuiObj\w+) \(\d+ attrs\) ==\s*$", raw_text,
                      flags=re.MULTILINE)
    for i in range(1, len(blocks), 2):
        cls, body = blocks[i], blocks[i + 1]
        seen: set[str] = set()
        attrs: list[tuple[str, int, str]] = []
        for line in body.splitlines():
            m = re.match(r"\s*(\w+)\s+\+\s+(\d+)\s+type=(\w+)", line)
            if not m:
                continue
            name, attpos, typename = m.group(1), int(m.group(2)), m.group(3)
            if name in seen:
                break
            seen.add(name)
            attrs.append((name, attpos, typename))
        out[cls] = attrs
    return out


def emit(schemas: dict[str, list[tuple[str, int, str]]]) -> str:
    lines = [
        '"""tft_attrs_schemas — F-series (T1) per-component attribute schemas.',
        "",
        'Generated from ``findings/attrs-raw.txt`` (which itself was extracted from',
        "``hmitype.dll``'s ``GuiObj<Kind>.GetAtts_WithNoHead`` IL). Each entry is",
        "``(attr_name, attpos, type_name)`` where:",
        "",
        "- ``attpos`` is the byte offset within the component's ``PianyiData`` block",
        "  (offset from ``sizeof(<Kind>_PARAM_Head)``).",
        "- ``type_name`` is one of the keys in ``ATTSHULEI_BY_NAME`` in",
        "  ``tft_attrs_encoder``.",
        "",
        "The list order is the order ``GetAtts_WithNoHead`` declares attributes,",
        "which is the order ``mpage.refallatt()`` enumerates them for the per-page",
        "record table.",
        "",
        "To regenerate, run ``scripts/tools/regen_attrs_schemas.py``.",
        '"""',
        "",
        "TYPE_SCHEMAS: dict[str, list[tuple[str, int, str]]] = {",
    ]
    for cls in sorted(schemas):
        items = schemas[cls]
        if not items:
            lines.append(f"    {cls!r}: [],")
            continue
        lines.append(f"    {cls!r}: [")
        for name, attpos, tn in items:
            lines.append(f"        ({name!r}, {attpos}, {tn!r}),")
        lines.append("    ],")
    lines.extend([
        "}",
        "",
        "",
        "def get_schema(class_name: str) -> list[tuple[str, int, str]]:",
        '    """Return the F-series ``GetAtts_WithNoHead`` schema for ``class_name``.',
        "",
        "    Raises ``KeyError`` if the class is not in the table.",
        '    """',
        "    return TYPE_SCHEMAS[class_name]",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    raw = (REPO_ROOT / "findings" / "attrs-raw.txt").read_text()
    schemas = parse(raw)
    out_path = REPO_ROOT / "scripts" / "lib" / "tft_attrs_schemas.py"
    out_path.write_text(emit(schemas))
    print(f"wrote {out_path} ({len(schemas)} component classes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
