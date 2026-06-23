"""project_ir — a hand-editable project IR for the from-scratch author CLI.

The IR is the from-scratch authoring input: a YAML/JSON document describing
the project's **pages → components → scripts** plus the small set of
``main.HMI`` manifest fields, all in plain dicts/lists of ints and strings.
It is the *content* model; the out-of-scope fonts/pictures resources blob +
``Modelcrc`` are referenced (not embedded) and copied from a reference
``.tft`` at author time, consistent with how :func:`compile_hmi.compile_hmi`
already works.

Two directions, both byte-exact round-trippable:

* :func:`ir_from_hmi` — DECOMPILE an existing ``.HMI`` into the IR.  This is
  what makes the round-trip test possible: ``ir_from_hmi(p.HMI)`` →
  :func:`scripts.author.author_tft` / :func:`author_main_hmi` → ``cmp``
  against the reference ``.tft`` and ``main.HMI`` must be byte-identical.
* :func:`pages_from_ir` — reconstruct the *identical*
  :class:`hmi_model.HmiPageModel` list the compiler consumes, so the author
  path drives the exact same byte-exact assembly pipeline
  (:func:`compile_hmi.compile_from_model`).

## Schema (YAML/JSON)

```yaml
program_s: |            # raw Program.s text (global int decls + power-on init)
  int sys0=0,sys1=0,sys2=0
main_hmi:              # the main.HMI manifest fields (see scripts.lib.main_hmi)
  model_crc: 0x1ce47603 # CRC of the model-name string (resources-coupled)
  guidire: 1            # GUI orientation byte
  encode: 3             # text-encoding id
  upver0: 1             # editor major-version stamp
  upver1: 0x43          # editor minor-version stamp
  picencodever: 2       # picture-encoding version
  resources:            # ordered (ext, name) resource refs, declaration order
    - [zi, 0.zi]
    - [pa, 0.pa]
pages:                  # device-id-ordered pages
  - page_id: 0          # device page id (== position in main.HMI .pa decl order)
    name: main
    attrs: {type: 121, id: 0, objname: main, x: 0, ...}   # page pseudo attrs
    events: {}          # page-level codesload/codesunload (non-empty only)
    components:         # objxinxi order (page pseudo is NOT listed here)
      - lei: 59         # component type code
        name: x0
        id: 1           # component id on the page
        attrs: {type: 59, x: 0, y: 20, ...}
        events: {codesdown: "..."}   # codes* event scripts (non-empty only)
```

The ``page.attrs`` dict IS the page pseudo-component's attribute dict (lei
121) — :func:`hmi_model.load_hmi_pages` builds the page pseudo from exactly
this dict, so the IR stores it verbatim.  Every value is an ``int`` or
``str`` (the loader's ``rawData['att']`` shape); the IR is therefore a pure
data document with no binary blobs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# IR dataclasses
# ---------------------------------------------------------------------------

@dataclass
class IRComponent:
    """One component on a page, in objxinxi (= declaration) order."""

    lei: int
    name: str
    id: int
    attrs: dict
    events: dict = field(default_factory=dict)   # codes* key → script (non-empty)


@dataclass
class IRPage:
    """One page in device-id order.  ``attrs`` is the page pseudo-component's
    attribute dict (lei 121); ``components`` are the user components only (the
    page pseudo is synthesised from ``attrs`` when building the model)."""

    page_id: int
    name: str
    attrs: dict
    events: dict = field(default_factory=dict)
    components: list[IRComponent] = field(default_factory=list)


@dataclass
class IRMainHmi:
    """The ``main.HMI`` manifest fields (see :mod:`scripts.lib.main_hmi`).

    ``model_crc`` is resources-coupled (a property of the model-name string,
    not the project content) and is the one field that, with ``resources``,
    feeds :func:`main_hmi.write_main_hmi`."""

    model_crc: int
    resources: list[tuple[str, str]]
    guidire: int = 0x01
    encode: int = 0x03
    upver0: int = 0x01
    upver1: int = 0x44
    picencodever: int = 0x02


@dataclass
class ProjectIR:
    """The whole from-scratch project IR."""

    program_s: str
    pages: list[IRPage]
    main_hmi: IRMainHmi


# ---------------------------------------------------------------------------
# Load / save (YAML or JSON)
# ---------------------------------------------------------------------------

def _coerce_int(v):
    """Accept ints, and ``"0x..."``/decimal strings (YAML/JSON hex strings)."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        return int(v, 0)
    raise TypeError(f"expected int, got {type(v).__name__}: {v!r}")


def _attrs_from_doc(raw: dict) -> dict:
    """Normalise an attrs dict from a loaded document: keys stay strings,
    int-looking values are coerced to int (YAML may load hex as strings),
    everything else stays a string."""
    out = {}
    for k, v in raw.items():
        if isinstance(v, bool):
            out[k] = int(v)
        elif isinstance(v, int):
            out[k] = v
        elif isinstance(v, str):
            # Numeric strings (incl. negatives / hex) → int, else keep str.
            s = v.strip()
            try:
                out[k] = int(s, 0)
            except ValueError:
                out[k] = v
        else:
            out[k] = v
    return out


def ir_from_dict(doc: dict) -> ProjectIR:
    """Build a :class:`ProjectIR` from a plain dict (parsed YAML/JSON)."""
    mh = doc["main_hmi"]
    main_hmi = IRMainHmi(
        model_crc=_coerce_int(mh["model_crc"]),
        resources=[(str(e), str(n)) for e, n in mh["resources"]],
        guidire=_coerce_int(mh.get("guidire", 0x01)),
        encode=_coerce_int(mh.get("encode", 0x03)),
        upver0=_coerce_int(mh.get("upver0", 0x01)),
        upver1=_coerce_int(mh.get("upver1", 0x44)),
        picencodever=_coerce_int(mh.get("picencodever", 0x02)),
    )
    pages = []
    for p in doc["pages"]:
        comps = [
            IRComponent(
                lei=_coerce_int(c["lei"]),
                name=str(c["name"]),
                id=_coerce_int(c["id"]),
                attrs=_attrs_from_doc(c.get("attrs", {})),
                events={k: v for k, v in (c.get("events") or {}).items() if v and str(v).strip()},
            )
            for c in p.get("components", [])
        ]
        pages.append(IRPage(
            page_id=_coerce_int(p["page_id"]),
            name=str(p["name"]),
            attrs=_attrs_from_doc(p.get("attrs", {})),
            events={k: v for k, v in (p.get("events") or {}).items() if v and str(v).strip()},
            components=comps,
        ))
    return ProjectIR(program_s=doc.get("program_s", ""), pages=pages,
                     main_hmi=main_hmi)


def ir_to_dict(ir: ProjectIR) -> dict:
    """Serialise a :class:`ProjectIR` to a plain dict (for YAML/JSON dump)."""
    return {
        "program_s": ir.program_s,
        "main_hmi": {
            "model_crc": ir.main_hmi.model_crc,
            "guidire": ir.main_hmi.guidire,
            "encode": ir.main_hmi.encode,
            "upver0": ir.main_hmi.upver0,
            "upver1": ir.main_hmi.upver1,
            "picencodever": ir.main_hmi.picencodever,
            "resources": [list(r) for r in ir.main_hmi.resources],
        },
        "pages": [
            {
                "page_id": p.page_id,
                "name": p.name,
                "attrs": dict(p.attrs),
                "events": dict(p.events),
                "components": [
                    {
                        "lei": c.lei,
                        "name": c.name,
                        "id": c.id,
                        "attrs": dict(c.attrs),
                        "events": dict(c.events),
                    }
                    for c in p.components
                ],
            }
            for p in ir.pages
        ],
    }


def load_ir(path: str | Path) -> ProjectIR:
    """Load a project IR from a ``.yaml``/``.yml`` or ``.json`` file."""
    p = Path(path)
    text = p.read_text()
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml
        doc = yaml.safe_load(text)
    else:
        doc = json.loads(text)
    return ir_from_dict(doc)


def save_ir(ir: ProjectIR, path: str | Path) -> None:
    """Dump a project IR to ``.yaml``/``.yml`` or ``.json``."""
    p = Path(path)
    doc = ir_to_dict(ir)
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml
        p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    else:
        p.write_text(json.dumps(doc, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Decompile: .HMI → IR
# ---------------------------------------------------------------------------

def _extract_main_hmi_blob(hmi_path: str | Path) -> bytes:
    """Pull the embedded ``main.HMI`` manifest blob out of an ``.HMI`` file."""
    from sim.loader import _ensure_ansi_codec, _ensure_nextion2text_on_path
    _ensure_ansi_codec()
    _ensure_nextion2text_on_path()
    from Nextion2Text import HMI

    h = HMI(str(hmi_path))
    entry = next(
        (c for c in h.header.content if getattr(c, "name", "") == "main.HMI"),
        None,
    )
    if entry is None:
        raise ValueError(f"no main.HMI entry in {hmi_path}")
    raw = Path(hmi_path).read_bytes()
    return raw[entry.start:entry.start + entry.size]


def main_hmi_from_blob(blob: bytes) -> IRMainHmi:
    """Decode an embedded ``main.HMI`` blob into :class:`IRMainHmi`."""
    import struct

    model_crc = struct.unpack_from("<I", blob, 0x10)[0]
    o = 0x60
    resources: list[tuple[str, str]] = []
    while o + 16 <= len(blob):
        ext = blob[o:o + 8].rstrip(b"\x00 ")
        name = blob[o + 8:o + 16].rstrip(b"\x00 ")
        if not ext and not name:
            break
        resources.append((ext.decode("latin1"), name.decode("latin1")))
        o += 16
    return IRMainHmi(
        model_crc=model_crc,
        resources=resources,
        guidire=blob[0x0C],
        encode=blob[0x0D],
        upver0=blob[0x08],
        upver1=blob[0x09],
        picencodever=blob[0x41],
    )


def ir_from_hmi(hmi_path: str | Path) -> ProjectIR:
    """DECOMPILE an existing ``.HMI`` into the project IR.

    Reads the page/component model via :func:`hmi_model.load_hmi_pages`,
    ``Program.s`` via :func:`sim.loader.load_hmi`, and the ``main.HMI``
    manifest fields from the embedded blob.  The result, fed back through the
    author path, reproduces the source ``.tft`` + ``main.HMI`` byte-for-byte.
    """
    from scripts.lib.hmi_model import load_hmi_pages
    from sim.loader import load_hmi

    program_s = load_hmi(str(hmi_path)).program_s
    pages_model = load_hmi_pages(hmi_path)

    pages: list[IRPage] = []
    for hp in pages_model:
        # hp.components[0] is the page pseudo (lei 121); its attrs == page attrs.
        comps = [
            IRComponent(lei=c.lei, name=c.name, id=c.comp_id,
                        attrs=dict(c.attrs), events=dict(c.events))
            for c in hp.components[1:]
        ]
        pages.append(IRPage(
            page_id=hp.page_id,
            name=hp.name,
            attrs=dict(hp.components[0].attrs),
            events=dict(hp.page_events),
            components=comps,
        ))

    main_hmi = main_hmi_from_blob(_extract_main_hmi_blob(hmi_path))
    return ProjectIR(program_s=program_s, pages=pages, main_hmi=main_hmi)


# ---------------------------------------------------------------------------
# Reconstruct: IR → HmiPageModel list (what the compiler consumes)
# ---------------------------------------------------------------------------

def pages_from_ir(ir: ProjectIR):
    """Reconstruct the device-id-ordered :class:`hmi_model.HmiPageModel`
    list from the IR — IDENTICAL in shape to
    :func:`hmi_model.load_hmi_pages`, so :func:`compile_hmi.compile_from_model`
    drives the same byte-exact pipeline.

    The page pseudo-component (lei 121) is prepended to each page's component
    list, built from :attr:`IRPage.attrs` (matching ``load_hmi_pages``).
    Pages are sorted by ``page_id`` (device order).
    """
    from scripts.lib.hmi_model import HmiPageModel, HmiComponentModel

    pages = []
    for p in sorted(ir.pages, key=lambda pg: pg.page_id):
        comps = [HmiComponentModel(
            lei=121, name=p.name, comp_id=0,
            attrs=dict(p.attrs),
            events={k: v for k, v in p.events.items() if v and str(v).strip()},
        )]
        for c in p.components:
            comps.append(HmiComponentModel(
                lei=c.lei, name=c.name, comp_id=c.id,
                attrs=dict(c.attrs),
                events={k: v for k, v in c.events.items() if v and str(v).strip()},
            ))
        pages.append(HmiPageModel(
            page_id=p.page_id, name=p.name,
            page_attrs=dict(p.attrs),
            page_events={k: v for k, v in p.events.items() if v and str(v).strip()},
            components=comps,
        ))
    return pages


__all__ = [
    "IRComponent",
    "IRPage",
    "IRMainHmi",
    "ProjectIR",
    "ir_from_dict",
    "ir_to_dict",
    "load_ir",
    "save_ir",
    "main_hmi_from_blob",
    "ir_from_hmi",
    "pages_from_ir",
]
