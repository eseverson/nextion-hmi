from __future__ import annotations
from pathlib import Path
import sys

from sim.state import DisplayState, Page, Component


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
    _ensure_nextion2text_on_path()
    from Nextion2Text import HMI

    hmi = HMI(str(path))
    pages: dict[str, Page] = {}
    for n2t_page in hmi.pages:
        page_comp = next(
            (c for c in n2t_page.components if c.rawData["att"].get("type") == 121),
            None,
        )
        if page_comp is None:
            continue
        pa = page_comp.rawData["att"]
        page_name = pa.get("objname") or f"page{len(pages)}"
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
            id=pa.get("id") or 0,
            attrs=dict(pa),
            components=components,
        )
    if not pages:
        raise ValueError(f"no pages parsed from {path}")
    return DisplayState(pages=pages)
