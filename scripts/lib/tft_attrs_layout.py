"""tft_attrs_layout — per-class allattbytes layout for the F-series.

Each component on a page occupies a contiguous run of ``stride`` records
inside the page-wide allattbytes table. Within that run, each attribute
the component class can have lives at a fixed *relative offset*. The
component's ``Attstrpianyi`` slot table (inside its 232-byte ``objxinxi``
entry) stores back-references: slot[N] = page_base + relative_offset for
attribute AppAttNames[N], or 0xffff for "no record".

This module exposes:

- ``PER_LEI_LAYOUT[lei]`` → ``(stride, {attr_name: relative_offset})``
- ``LEI_TO_CLASS[lei]`` → ``GuiObj<Kind>`` class name

The tables were derived from
``tests/editor outputs/_old/17_more_components/17.tft`` (51 objects, 19
distinct ``lei`` codes). Every instance of a given ``lei`` produces the
same layout, so the table is class-deterministic.

**Universal head fields** occupy fixed relative offsets for every
component class:

    0  → lei (component type code, slot 62 = "lei" in AppAttNames)
    1  → id  (slot 49)
    3  → vscope (slot 1)
    9  → x  (slot 42)
    10 → y  (slot 43)
    11 → w  (slot 46)
    12 → h  (slot 47)
    13 → endx (slot 44)
    14 → endy (slot 45)

Offsets {2, 4, 5, 6, 7, 8} are reserved/unused for every type.

**Declared attrs** start at offset 19 for most classes; ``GuiObjPage``
starts its declared attrs at offset 23 (offsets 19..22 are reserved,
probably for the page's `up/down/left/right` navigation attrs even when
unset). Within each class, declared attrs are placed in
``GetAtts_WithNoHead`` order, but with some collapsing where multiple
attribute names share the same ``attpos`` (the exact mapping is encoded
in the table below; deriving it from first principles would require IL
analysis of the editor's ``Allattbytes_set`` allocator).

To extend: capture an editor fixture that includes the missing component
types (Audio, Video, Gmov, all VP variants, etc.) and re-run
``scripts/tools/regen_attrs_layout.py``.
"""

# Map from lei (numeric component type code) to GuiObj<Kind> class name.
# Identified by matching each lei's declared-attr offset map against the
# F-series schemas in ``tft_attrs_schemas.TYPE_SCHEMAS``.
LEI_TO_CLASS: dict[int, str] = {
    0: "GuiObjCurve",
    1: "GuiObjSlider",
    5: "GuiObjTouchcap",
    51: "GuiObjTimer",
    52: "GuiObjVari",
    53: "GuiObjButton_T",   # touch-only Button variant (same offset map as 98)
    55: "GuiObjGText",
    56: "GuiObjCheckBox",
    57: "GuiObjRadio",
    58: "GuiObjQrcode",
    59: "GuiObjXfloat",
    98: "GuiObjButton",
    106: "GuiObjProg",
    109: "Hotspot",         # no GuiObj<Kind> in attrs-raw.txt; head-only
    112: "GuiObjPic",
    113: "GuiObjPicc",
    116: "GuiObjText",
    121: "GuiObjPage",
    122: "GuiObjZhizhen",
}

CLASS_TO_LEI: dict[str, int] = {v: k for k, v in LEI_TO_CLASS.items()}


# Per-class layout: lei → (stride_in_records, {attr_name: relative_offset}).
# The offset map covers head fields and all declared attrs that the class
# allocates a record for. Names not in the inner dict have no record and
# must be addressed as 0xffff in Attstrpianyi.
PER_LEI_LAYOUT: dict[int, tuple[int, dict[str, int]]] = {
    0: (41, {  # GuiObjCurve (Waveform)
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'sta': 19, 'dir': 20, 'ch': 21, 'bco': 22,
        'gdc': 23, 'gdw': 24, 'gdh': 25, 'pco0': 28, 'dis': 30,
    }),
    1: (40, {  # GuiObjSlider
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'mode': 19, 'sta': 20, 'psta': 21, 'wid': 22, 'hig': 23,
        'bco': 25, 'bco1': 26, 'pco': 27,
        'val': 28, 'maxval': 29, 'minval': 30, 'ch': 31,
    }),
    5: (10, {  # GuiObjTouchcap
        'type': 0, 'id': 1, 'vscope': 3, 'val': 7,
    }),
    51: (11, {  # GuiObjTimer
        'type': 0, 'id': 1, 'vscope': 3, 'tim': 7, 'en': 8,
    }),
    52: (11, {  # GuiObjVari (head fields share offset 0/1/3; no x/y/w/h)
        'type': 0, 'id': 1, 'vscope': 3, 'sta': 7, 'val': 8,
    }),
    53: (42, {  # GuiObjButton_T
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'sta': 19, 'style': 20, 'font': 21, 'bco': 22, 'bco2': 23,
        'pco': 24, 'pco2': 25, 'xcen': 26, 'ycen': 27, 'val': 28,
        'txt': 29, 'txt_maxl': 30, 'isbr': 31, 'spax': 32, 'spay': 33,
    }),
    55: (48, {  # GuiObjGText
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'sta': 19, 'style': 20, 'font': 22, 'bco': 23, 'pco': 24,
        'xcen': 25, 'ycen': 26, 'dir': 27, 'dis': 28, 'tim': 29, 'en': 30,
        'txt': 31, 'txt_maxl': 32, 'isbr': 33, 'spax': 34, 'spay': 35,
        'vvs0': 36, 'vvs1': 37, 'vvs2': 38, 'vvs3': 39,
    }),
    56: (31, {  # GuiObjCheckBox
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'style': 19, 'bco': 20, 'pco': 21, 'val': 22,
    }),
    57: (30, {  # GuiObjRadio
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'bco': 19, 'pco': 20, 'val': 21,
    }),
    58: (33, {  # GuiObjQrcode
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'sta': 19, 'dis': 20, 'bco': 21, 'pco': 22,
        'txt': 23, 'txt_maxl': 24,
    }),
    59: (41, {  # GuiObjXfloat
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'sta': 19, 'style': 20, 'font': 22, 'bco': 23, 'pco': 24,
        'xcen': 25, 'ycen': 26, 'val': 27,
        'vvs0': 28, 'vvs1': 29, 'isbr': 30, 'spax': 31, 'spay': 32,
    }),
    98: (42, {  # GuiObjButton
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'sta': 19, 'style': 20, 'font': 21, 'bco': 22, 'bco2': 23,
        'pco': 24, 'pco2': 25, 'xcen': 26, 'ycen': 27, 'val': 28,
        'txt': 29, 'txt_maxl': 30, 'isbr': 31, 'spax': 32, 'spay': 33,
    }),
    106: (33, {  # GuiObjProg (ProgressBar)
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'sta': 19, 'dez': 20, 'val': 21, 'dis': 22, 'bco': 23, 'pco': 24,
    }),
    109: (27, {  # Hotspot (head fields only)
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
    }),
    112: (28, {  # GuiObjPic
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'pic': 19,
    }),
    113: (30, {  # GuiObjPicc (CropPicture)
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'picc': 19, 'vvs0': 20, 'vvs1': 21,
    }),
    116: (41, {  # GuiObjText
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'sta': 19, 'style': 20, 'font': 22, 'bco': 23, 'pco': 24,
        'xcen': 25, 'ycen': 26, 'pw': 27,
        'txt': 28, 'txt_maxl': 29, 'isbr': 30, 'spax': 31, 'spay': 32,
    }),
    121: (33, {  # GuiObjPage (declared attrs start at offset 23, not 19)
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'sta': 23, 'bco': 24,
    }),
    122: (41, {  # GuiObjZhizhen (Gauge/Pointer)
        'type': 0, 'id': 1, 'vscope': 3,
        'x': 9, 'y': 10, 'w': 11, 'h': 12, 'endx': 13, 'endy': 14,
        'sta': 19, 'bco': 20, 'val': 21, 'pco': 26, 'wid': 29,
    }),
}


def get_layout(lei: int) -> tuple[int, dict[str, int]]:
    """Return ``(stride, attr_name → relative_offset)`` for ``lei``.

    Raises ``KeyError`` if the component type is not in the table
    (i.e. it wasn't present in fixture 17). Coverage today: 19 of the
    49 known component classes — extend by capturing a fixture that
    includes the missing classes.
    """
    return PER_LEI_LAYOUT[lei]
