"""sim/tft_loader — load an F-series .tft directly into a DisplayState.

Two modes:

    foo.tft + foo.HMI  → HMI loader's full fidelity (text, colors, fonts,
                          event scripts) with TFT-derived orientation
                          spliced in. Use this when both files are
                          available — the HMI is authoritative.

    foo.tft alone      → Components are reconstructed from the TFT's
                          on-disk `objdata_Ram` records (52 bytes per
                          component, found at `appinf1.objxinxiadd`).
                          That gives us per-component **type, id, x, y,
                          w, h** for every page — enough to render
                          component outlines at correct positions and
                          dispatch events by id/type. Attribute *values*
                          (text, color, font, event-script source) live
                          in regions that aren't fully decoded yet, so
                          components come back with empty `attrs` /
                          `events`.

The TFT-only path is best-effort: it gets you a layout-faithful
DisplayState that the renderer can place rectangles for, but it can't
yet show text or run scripts. Closing that gap means decoding the
180-byte per-object "PianyiData" trailer (which carries attribute IDs
but routes through binattinf records still being mapped) — a follow-up
task tracked in findings/R.
"""
from __future__ import annotations
import struct
from pathlib import Path

from sim.state import DisplayState, Page, Component
from sim.font import parse_zi


# Re-use the format constants without forcing an import cycle through
# `scripts.tft_format` — those values are physical layout constants of
# the file, not implementation details.
_H1_END    = 0x0c4
_H2_START  = 0x0c8
_H2_END    = 0x18c
_MODELCRC_OFF = 0x2e


def _decrypt_h2(data: bytes) -> bytes:
    """Decrypt the H2 ciphertext using the cipher in `scripts.h2_cipher`."""
    # Imported lazily so this module is importable even when scripts/ isn't
    # on sys.path yet (the sim entrypoint adds it).
    import sys
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from scripts.h2_cipher import encrypt as h2_decrypt   # asm-verbatim = decrypt
    model_crc = struct.unpack_from("<I", data, _MODELCRC_OFF)[0]
    return h2_decrypt(data[_H2_START:_H2_END], model_crc)


def _parse_appinf0(h1: bytes) -> dict:
    """Plaintext H1 fields useful for runtime."""
    return {
        "screenw":       struct.unpack_from("<H", h1, 0x0c)[0],
        "screenh":       struct.unpack_from("<H", h1, 0x0e)[0],
        "lcdscreenw":    struct.unpack_from("<H", h1, 0x10)[0],
        "lcdscreenh":    struct.unpack_from("<H", h1, 0x12)[0],
        "guidire":       h1[0x14],     # orientation: 0=0°, 1=90°, 2=180°, 3=270° (matches HMI loader's H1+0x14)
        "xiliemark":     h1[0x15],     # series mark (100=F-series)
        "model_crc":     struct.unpack_from("<I", h1, _MODELCRC_OFF)[0],
        "filever":       h1[0x32],
    }


def _parse_appinf1(h2_plain: bytes) -> dict:
    """First 76 bytes of decrypted H2 are the appinf1 struct."""
    u = struct.unpack_from("<14I8H2BH", h2_plain, 0)
    return {
        "staticstrBeg":   u[0],
        "AppAllvasAddr":  u[1],
        "AppAllvasQty":   u[2],
        "attdataaddr":    u[3],
        "resourcesfileddr": u[4],
        "strdataaddr":    u[5],
        "pageadd":        u[6],
        "objxinxiadd":    u[7],
        "picxinxiadd":    u[8],
        "gmovxinxiadd":   u[9],
        "videoxinxiadd":  u[10],
        "wavxinxiadd":    u[11],
        "zimoxinxiadd":   u[12],
        "MainCodeHex":    u[13],
        "pageqyt":        u[14],
        "objqyt":         u[15],
        "picqyt":         u[16],
        "gmovqyt":        u[17],
        "videoqyt":       u[18],
        "wavqyt":         u[19],
        "zimoqyt":        u[20],
    }


def _extract_text_slots(data: bytes) -> list[tuple[int, str]]:
    """Wraps `scripts.tft_format.extract_text_slots` so the loader can
    avoid an absolute import at module top."""
    import sys as _sys
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))
    from scripts.tft_format import extract_text_slots
    return extract_text_slots(data)


def _extract_page_bco(data: bytes) -> int | None:
    import sys as _sys
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))
    from scripts.tft_format import extract_page_bco
    return extract_page_bco(data)


def _extract_variable_vals(data: bytes, n_variables: int) -> list[int]:
    import sys as _sys
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))
    from scripts.tft_format import extract_variable_vals
    return extract_variable_vals(data, n_variables)


def _extract_xfloat_records(data: bytes) -> list[dict]:
    import sys as _sys
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))
    from scripts.tft_format import extract_xfloat_records
    return extract_xfloat_records(data)


def _extract_progressbar_records(data: bytes) -> list[dict]:
    import sys as _sys
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))
    from scripts.tft_format import extract_progressbar_records
    return extract_progressbar_records(data)


def _extract_text_colors(data: bytes, slot_offset: int, comp_type: int) -> dict:
    import sys as _sys
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))
    from scripts.tft_format import extract_text_colors
    return extract_text_colors(data, slot_offset, comp_type)


def _extract_slider_records(data: bytes) -> list[dict]:
    import sys as _sys
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))
    from scripts.tft_format import extract_slider_records
    return extract_slider_records(data)


def _extract_zi_fonts(data: bytes) -> dict:
    import sys as _sys
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))
    from scripts.tft_format import extract_zi_fonts
    return extract_zi_fonts(data)


def _extract_pictures(data: bytes) -> dict:
    import sys as _sys
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))
    from scripts.tft_format import extract_pictures
    return extract_pictures(data)


def _parse_pages(data: bytes, info: dict) -> list[dict]:
    """Page directory at `pageadd`: 16 bytes per entry (`pagexinxi` struct)."""
    pages = []
    for i in range(info["pageqyt"]):
        off = info["pageadd"] + i * 16
        objstar, objqyt, _res, hexpos, attaddr, mediapos = struct.unpack_from(
            "<HBBIII", data, off
        )
        pages.append({
            "id": i,
            "objstar": objstar,
            "objqyt": objqyt,
            "hexpos": hexpos,
            "attaddr": attaddr,
            "mediapos": mediapos,
        })
    return pages


def _try_load_sibling_hmi(tft_path: Path) -> DisplayState | None:
    """If a sibling `.HMI` exists with the same stem, load components from there."""
    candidates = [
        tft_path.with_suffix(".HMI"),
        tft_path.with_suffix(".hmi"),
    ]
    for c in candidates:
        if c.exists():
            from sim.loader import load_hmi
            try:
                return load_hmi(c)
            except Exception:
                # Fall through — caller will get a TFT-only state.
                return None
    return None


def load_tft(path: str | Path) -> DisplayState:
    """Load an F-series TFT into a DisplayState.

    For full component fidelity, also have the source `.HMI` next to the
    `.tft` (same stem); the loader will pull components from there.
    Without an HMI, you get a valid DisplayState with the right number
    of pages at the right screen size, but no component definitions.
    """
    path = Path(path)
    raw = path.read_bytes()

    if len(raw) < _H2_END + 4:
        raise ValueError(f"file too small to be a TFT: {len(raw)} bytes")

    h1 = raw[:_H1_END]
    h0 = _parse_appinf0(h1)
    if h0["xiliemark"] != 100:
        # Not an F-series TFT; the cipher won't apply. Bail rather than
        # produce garbage.
        raise ValueError(
            f"unsupported TFT: xiliemark={h0['xiliemark']} (only F-series, "
            f"xiliemark=100, is supported)"
        )

    h2_plain = _decrypt_h2(raw)
    h1info = _parse_appinf1(h2_plain)
    page_dir = _parse_pages(raw, h1info)

    # Try the sibling HMI for component data.
    hmi_state = _try_load_sibling_hmi(path)
    if hmi_state is not None:
        # Splice in TFT-derived runtime fields. The HMI loader sets
        # orientation from its own sniff (it looks at sibling .tft already);
        # we override here in case the HMI was authored at a different
        # rotation than the TFT was compiled at.
        hmi_state.orientation = _orientation_from_guidire(h0["guidire"])
        return hmi_state

    # Standalone TFT path: parse objdata_Ram records to reconstruct
    # per-component layout. See `_parse_objdata_ram`.
    objs_by_page = _parse_objdata_ram(raw, h1info, page_dir)
    text_slots = _extract_text_slots(raw)
    page_bco = _extract_page_bco(raw)
    n_variables = sum(1 for objs in objs_by_page.values()
                      for o in objs if o["type"] == 52)
    var_vals = _extract_variable_vals(raw, n_variables)
    xfloat_recs = _extract_xfloat_records(raw)
    progressbar_recs = _extract_progressbar_records(raw)
    slider_recs = _extract_slider_records(raw)

    # Walk all text-bearing components in TFT order and pair them with
    # extracted text slots. Best-effort: assumes the editor emits txt
    # values in the same order it walks components. Falls back to no
    # txt if counts diverge. See `_extract_text_slots`.
    text_iter = iter(text_slots)

    def _next_txt_slot():
        try:
            return next(text_iter)
        except StopIteration:
            return None

    var_iter = iter(var_vals)

    def _next_val():
        try:
            return next(var_iter)
        except StopIteration:
            return None

    xfloat_iter = iter(xfloat_recs)

    def _next_xfloat():
        try:
            return next(xfloat_iter)
        except StopIteration:
            return None

    progressbar_iter = iter(progressbar_recs)

    def _next_progressbar():
        try:
            return next(progressbar_iter)
        except StopIteration:
            return None

    slider_iter = iter(slider_recs)

    def _next_slider():
        try:
            return next(slider_iter)
        except StopIteration:
            return None

    pages: dict[str, Page] = {}
    for entry in page_dir:
        pid = entry["id"]
        name = f"page{pid}"
        page_objs = objs_by_page[pid]
        page_meta = next((o for o in page_objs if o["type"] == 121), None)
        canvas_w = page_meta["w"] if page_meta else h0["lcdscreenw"]
        canvas_h = page_meta["h"] if page_meta else h0["lcdscreenh"]
        components = []
        for o in page_objs:
            if o["type"] == 121:
                continue
            attrs = {
                "x": o["x"], "y": o["y"],
                "w": o["w"], "h": o["h"],
                "endx": o["endx"], "endy": o["endy"],
                "objname": o["name"],
                "id": o["id"],
                "type": o["type"],
            }
            # Visible-text-bearing types: Text(116), Button(98),
            # ScrollingText(55). Skip Variable(52) — `txt='newtxt'` is
            # the editor's default for a non-displayed scratch value.
            if o["type"] in (116, 98, 55):
                slot = _next_txt_slot()
                if slot is not None:
                    slot_off, t = slot
                    attrs["txt"] = t
                    # Pull bco/pco (and bco2/pco2 for Buttons) from the
                    # slot prefix.
                    attrs.update(_extract_text_colors(raw, slot_off, o["type"]))
                    # font for Texts/Buttons isn't carried in the slot
                    # prefix; the editor's default is font=0 (the
                    # project's primary body font, by convention).
                    attrs.setdefault("font", 0)
            # Variables (type=52) carry their `val` from the dedicated
            # u32 array after the `90 01 01 00` marker.
            if o["type"] == 52:
                v = _next_val()
                if v is not None:
                    attrs["val"] = v
            # XFloats (type=59) get bco/pco/sta/font/val from the
            # per-component records region. Non-XFloat components
            # break the iteration once we hit them — the records
            # extractor only walks the leading XFloat run.
            if o["type"] == 59:
                rec = _next_xfloat()
                if rec is not None:
                    attrs["bco"] = rec["bco"]
                    attrs["pco"] = rec["pco"]
                    attrs["sta"] = rec["sta"]
                    attrs["font"] = rec["font"]
                    attrs["val"] = rec["val"]
                    attrs["vvs0"] = rec["vvs0"]
                    attrs["vvs1"] = rec["vvs1"]
            # Progress Bar (type=106) records sit between XFloats; the
            # heuristic extractor finds them by their non-`0xffde` pco.
            if o["type"] == 106:
                rec = _next_progressbar()
                if rec is not None:
                    attrs["bco"] = rec["bco"]
                    attrs["pco"] = rec["pco"]
                    attrs["val"] = rec["val"]
                    # Keep `sta=1` (paint background); ProgressBar
                    # extraction's sta byte doesn't always agree with HMI.
                    attrs["sta"] = 1
            # Picture (type=112): the actual image lives in
            # state.pictures[pic]; we just need the `pic` attribute on
            # the component. The Picturexinxi records run in pictureid
            # order at picxinxiadd, so a given pictureid is found by
            # iteration. For now, assume a single Picture component
            # uses pictureid=0 (matching the editor default).
            if o["type"] == 112:
                attrs.setdefault("pic", 0)
            # Slider (type=1) records sit in a per-page records region.
            if o["type"] == 1:
                rec = _next_slider()
                if rec is not None:
                    attrs["bco"] = rec["bco"]
                    attrs["pco"] = rec["pco"]
                    attrs["val"] = rec["val"]
                    attrs["maxval"] = rec["maxval"]
                    attrs["minval"] = rec["minval"]
                    attrs["ch"] = rec["ch"]
            components.append(Component(
                name=o["name"], id=o["id"], type=o["type"], attrs=attrs,
                events={},
            ))
        page_attrs = {
            "objname": name,
            "w": canvas_w,
            "h": canvas_h,
            "sta": 1,   # `1` = use bco (Nextion's default-fill mode)
        }
        if page_bco is not None:
            page_attrs["bco"] = page_bco
        pages[name] = Page(
            name=name,
            id=pid,
            attrs=page_attrs,
            components=components,
            events={},
        )

    state = DisplayState(pages=pages)
    state.orientation = _orientation_from_guidire(h0["guidire"])
    # Extract embedded ZI fonts (per-font 44-byte headers at
    # zimoxinxiadd, then name + glyph data following). Renderer uses
    # these via state.fonts; missing fonts fall back to TTF.
    state.fonts = _extract_zi_fonts(raw)
    # Extract embedded pictures (RGB565 pixel data after each
    # Picturexinxi index record at picxinxiadd).
    state.pictures = _extract_pictures(raw)
    return state


def _parse_objdata_ram(data: bytes, info: dict, page_dir: list[dict]) -> dict[int, list[dict]]:
    """Decode each component's 52-byte `objdata_Ram` record from the
    TFT's `objxinxiadd` region.

    Per-component on-disk stride is `52 + PianyiDataSize_Bianyi`. For
    the F-series (xiliemark=100) that field is 180 (set in
    `attinit_T1`), so each record is 232 bytes. Layout of the leading
    52 bytes:

        +0   byte    objType
        +1   byte    id              (component id within page)
        +2   byte    merry
        +3   byte    objstate
        +4   uint[6] events          (codesload/down/up/move/etc.,
                                       0xFFFFFFFF means "no handler")
        +28  int     memorypos
        +32  byte    move
        +33  byte    sendkey
        +34  byte    aph
        +35  byte    regaddr
        +36  short   movex
        +38  short   movey
        +40  short   x
        +42  short   y
        +44  short   w
        +46  short   h
        +48  short   endx
        +50  short   endy
    """
    OBJ_STRIDE = 52 + 180     # F-series: PianyiDataSize_Bianyi == 180
    by_page: dict[int, list[dict]] = {}
    for entry in page_dir:
        pid = entry["id"]
        objs = []
        for local_id in range(entry["objqyt"]):
            global_idx = entry["objstar"] + local_id
            o = info["objxinxiadd"] + global_idx * OBJ_STRIDE
            objType, id_, merry, objstate = data[o:o + 4]
            events = struct.unpack_from("<6I", data, o + 4)
            memorypos = struct.unpack_from("<I", data, o + 28)[0]
            movex, movey, x, y, w, h, endx, endy = struct.unpack_from(
                "<8h", data, o + 36
            )
            # Component name is *not* in the on-disk record (it's only
            # in the HMI source). Synthesize one so callers / tests can
            # still address components by string name.
            name = f"obj{id_}" if objType != 121 else f"page{pid}"
            objs.append({
                "type": objType, "id": id_, "merry": merry,
                "objstate": objstate, "events": events,
                "memorypos": memorypos,
                "x": x, "y": y, "w": w, "h": h,
                "endx": endx, "endy": endy,
                "movex": movex, "movey": movey,
                "name": name,
            })
        by_page[pid] = objs
    return by_page


def _orientation_from_guidire(guidire: int) -> int:
    """Translate the H1 `guidire` byte (file offset 0x14) to a rotation
    in degrees. Same mapping `scripts/nextion_sim.py` uses when sniffing
    a sibling TFT for the HMI loader."""
    return {0x00: 90, 0x01: 0, 0x02: 270, 0x03: 180}.get(guidire & 0xFF, 0)
