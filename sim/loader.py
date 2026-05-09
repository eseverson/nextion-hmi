from __future__ import annotations
from pathlib import Path
import codecs
import sys

from sim.state import DisplayState, Page, Component


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


def load_hmi(path: str | Path) -> DisplayState:
    """Load a Nextion HMI file and return a populated DisplayState."""
    _ensure_ansi_codec()
    _ensure_nextion2text_on_path()
    from Nextion2Text import HMI

    hmi = HMI(str(path))
    # Pair each parsed Page with the matching directory entry so we can
    # recover the page id from the .pa filename prefix (e.g. "0.pa" → 0).
    # The HMI's `page <n>` command, the firmware's commands, and Touch
    # event payloads all use this id, NOT the directory's iteration order.
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
        # dir_entry.name is something like "0.pa"; the integer prefix is the id.
        try:
            page_id = int(dir_entry.name.split(".", 1)[0])
        except (ValueError, AttributeError):
            page_id = len(pages)
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
        )
    if not pages:
        raise ValueError(f"no pages parsed from {path}")
    return DisplayState(pages=pages)
