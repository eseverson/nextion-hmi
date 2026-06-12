from __future__ import annotations
from pathlib import Path
import codecs
import sys

from sim.state import DisplayState, Page, Component
from sim.font import parse_zi, ZiFont


_ANSI_CODEC_REGISTERED = False


def _ensure_ansi_codec() -> None:
    """Nextion2Text decodes HMI byte fields as 'ansi'. That alias only exists
    on Windows; on Linux/macOS it raises LookupError. Resolve it to latin-1
    so every byte maps to a code point — strict cp1252 has undefined slots
    that the HMI's binary headers happen to hit, and the text fields we
    actually care about are ASCII anyway."""
    global _ANSI_CODEC_REGISTERED
    try:
        codecs.lookup("ansi")
        return
    except LookupError:
        pass
    if _ANSI_CODEC_REGISTERED:
        return

    fallback = codecs.lookup("latin-1")

    def _search(name: str):
        if name.lower().replace("-", "_") == "ansi":
            return fallback
        return None

    codecs.register(_search)
    _ANSI_CODEC_REGISTERED = True
    codecs.lookup("ansi")  # surface any remaining failure now


def _ensure_nextion2text_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    n2t = repo_root / "tools" / "Nextion2Text"
    if not n2t.exists():
        raise FileNotFoundError(
            f"Nextion2Text not found at {n2t}. Run scripts/setup.sh first."
        )
    if str(n2t) not in sys.path:
        sys.path.insert(0, str(n2t))


def load(path: str | Path) -> DisplayState:
    """Format-dispatching loader: routes `.tft` paths to the TFT loader,
    everything else to the HMI loader. Use this when you don't know the
    file's type up front — e.g. CLI flags."""
    p = Path(path)
    if p.suffix.lower() == ".tft":
        from sim.tft_loader import load_tft
        return load_tft(p)
    return load_hmi(p)


def _pa_declaration_order(path: Path, hmi) -> list[int]:
    """Page .pa stems in main.HMI manifest declaration order.

    The manifest's trailing reference array (main.HMI +0x60, 16-byte
    rows: 8B ext + 8B name, NUL-left-padded) lists every resource in
    declaration order; the compiler emits pages in that order, so a
    page's index here IS its device page id. Returns [] if the manifest
    can't be parsed (caller falls back to the .pa stem)."""
    entry = next(
        (c for c in hmi.header.content if getattr(c, "name", "") == "main.HMI"),
        None,
    )
    if entry is None:
        return []
    raw = Path(path).read_bytes()
    blob = raw[entry.start:entry.start + entry.size]
    order: list[int] = []
    o = 0x60
    while o + 16 <= len(blob):
        ext = blob[o:o + 8].strip(b"\x00 ")
        name = blob[o + 8:o + 16].strip(b"\x00 ")
        if not ext and not name:
            break
        if ext == b"pa":
            try:
                order.append(int(name.split(b".", 1)[0]))
            except ValueError:
                return []
        o += 16
    return order


def load_hmi(path: str | Path) -> DisplayState:
    """Load a Nextion HMI file and return a populated DisplayState."""
    _ensure_ansi_codec()
    _ensure_nextion2text_on_path()
    from Nextion2Text import HMI

    hmi = HMI(str(path))
    # Pair each parsed Page with the matching directory entry so we can
    # recover the page's .pa stem (e.g. "0.pa" → 0). Numeric `page <n>`
    # refs do NOT use that stem: the editor compiles pages in the
    # main.HMI manifest's declaration order, and that index is what the
    # device (and therefore every numeric page command) means. The two
    # diverge as soon as pages are reordered in the editor (miata-dash:
    # declaration order 0,2,1,3 = main,gauge,settings,error).
    decl_order = _pa_declaration_order(Path(path), hmi)
    page_dir_entries = [c for c in hmi.header.content if c.isPage()]
    assert len(page_dir_entries) == len(hmi.pages), (
        f"directory page count {len(page_dir_entries)} != "
        f"parsed page count {len(hmi.pages)}"
    )
    pages: dict[str, Page] = {}
    for n2t_page, dir_entry in zip(hmi.pages, page_dir_entries):
        page_comp = next(
            (c for c in n2t_page.components if c.rawData["att"].get("type") == 121),
            None,
        )
        if page_comp is None:
            continue
        pa = page_comp.rawData["att"]
        page_name = pa.get("objname") or f"page{len(pages)}"
        # dir_entry.name is something like "0.pa"; map its stem through
        # the manifest's declaration order to get the device page id.
        try:
            pa_stem = int(dir_entry.name.split(".", 1)[0])
            page_id = decl_order.index(pa_stem) if decl_order else pa_stem
        except (ValueError, AttributeError):
            page_id = len(pages)
        # Page-level event scripts (codesload, codesloadend, codesunload) live
        # alongside "att" in the page-meta component's rawData.
        page_events = {
            k: v for k, v in page_comp.rawData.items()
            if k.startswith("codes") and isinstance(v, str) and v.strip()
        }
        components: list[Component] = []
        for c in n2t_page.components:
            ca = c.rawData["att"]
            ctype = ca.get("type")
            if ctype == 121:
                continue
            events = {
                k: v for k, v in c.rawData.items()
                if k.startswith("codes") and isinstance(v, str) and v.strip()
            }
            components.append(
                Component(
                    name=ca.get("objname") or f"c{ca.get('id')}",
                    id=ca.get("id") or 0,
                    type=ctype,
                    attrs=dict(ca),
                    events=events,
                )
            )
        pages[page_name] = Page(
            name=page_name,
            id=page_id,
            attrs=dict(pa),
            components=components,
            events=page_events,
        )
    if not pages:
        raise ValueError(f"no pages parsed from {path}")
    state = DisplayState(pages=pages)
    state.program_s = getattr(hmi, "programS", "") or ""

    # Pictures only live in the compiled TFT in a renderer-friendly
    # format (RGB565). The HMI's `*.ib` / `*.is` entries are PNG
    # sources, useful for round-trip but not for runtime rendering. So
    # if a sibling .tft exists, sniff it for pictures.
    tft_path = Path(str(path).removesuffix(".HMI") + ".tft")
    if not tft_path.exists():
        tft_path = Path(path).with_suffix(".tft")
    if tft_path.exists():
        try:
            from scripts.lib.tft_format import extract_pictures
            tft_bytes = tft_path.read_bytes()
            state.pictures = extract_pictures(tft_bytes)
        except Exception:
            pass

    # Pull each ZI font directory entry out of the HMI raw bytes. Keyed by
    # the integer prefix of the .zi filename — `0.zi` -> 0 — which matches
    # the `font` attribute on Text/XFloat/Number components.
    for entry in hmi.header.content:
        name = entry.name
        if not name.endswith(".zi"):
            continue
        try:
            font_id = int(name.split(".", 1)[0])
        except ValueError:
            continue
        blob = hmi.raw[entry.start:entry.start + entry.size]
        try:
            state.fonts[font_id] = parse_zi(blob)
        except Exception:
            # Unsupported / malformed font → leave it absent; renderer will
            # use the TTF fallback for this font_id.
            continue

    # Page CRC sanity check — per finding Q (2026-05-10), each `*.pa`
    # entry has a 5-segment chained CRC32-MPEG2 at offset 0. Verify all
    # live pages and warn (don't fail) on mismatch — the user might have
    # hand-edited a file in a way that didn't update the CRC.
    try:
        from scripts.lib.page_crc import page_crc as _page_crc
    except ImportError:
        _page_crc = None
    if _page_crc is not None:
        import logging
        log = logging.getLogger("sim.loader")
        for entry in hmi.header.content:
            name = getattr(entry, "name", "") or ""
            if not name.endswith(".pa") or getattr(entry, "deleted", 0):
                continue
            blob = hmi.raw[entry.start:entry.start + entry.size]
            if len(blob) < 0x38:
                continue
            import struct as _s
            stored = _s.unpack_from("<I", blob, 0)[0]
            try:
                computed = _page_crc(blob)
            except Exception:
                continue
            if stored != computed:
                log.warning(
                    "page %s: CRC mismatch (stored=0x%08x computed=0x%08x) — "
                    "file may have been hand-edited without CRC fixup",
                    name, stored, computed)

    return state
