"""tft_init_encoder — encoder for per-component init bytecode.

The inverse of :mod:`tft_bytecode`. Given a component (type, id, x, y,
w, h, attrs), produces the per-component init bytecode stream that
the editor would write at ``strdataaddr + PianyiData[+0x34]``.

The encoder mirrors the editor's
``hmitype.mobj::Getatt_Codes`` → ``Getbianyi`` → ``bianyionline``
pipeline (see [`findings/init-bytecode-encoder.md`]). Each visible
component's persistent init bytecode is a sequence of length-prefixed
blocks, one per Nextion script line that the editor synthesises for
the Ref event of that component.

The encoder does NOT implement the full script-bytecode compiler
(gap #3). It implements only the subset of mnemonics that the
per-component init-event templates use:

  setbrush, fstr, nstr, zstr, xstr, fill, pic, xpic, qrcode, cir,
  cirs, draw3d, addt

For each, the byte-shape is fixed: a 3-byte opcode header, followed
by comma-separated args that are either ASCII literals or 5-byte
LOAD operands (``01 LL HH 00 00 = LOAD u32(0xHHLL)``).

Looking up the LOAD operand value (the attribute address) requires
the value-table layout (agent 1's territory, gap #1). The encoder
takes that resolver as a callable parameter, so it can be plugged in
once the value table is mapped.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
import struct
import sys

# Allow running this file directly as `python3 scripts/lib/tft_init_encoder.py`
# in addition to being imported as `scripts.lib.tft_init_encoder`.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.lib.script_compiler_extras import (  # noqa: E402
    emit_cjmp, emit_jmp, convert_entry_to_byte_distances,
    flatten_cglist, COMPARATOR_ENDID,
)


# -- Type-ID → human label ------------------------------------------

# From `hmitype.AppData.appobjs.<field>.id` ctor (see findings doc).
TYPE_LABELS: dict[int, str] = {
    0: "waveform",
    1: "slider",
    5: "croppicture",  # observed as type=5 in test fixtures
    50: "touchcap",
    51: "timer",
    52: "variable",
    53: "button_t",      # DualStateButton
    54: "number",
    55: "gtext",         # ScrollingText
    56: "checkbox",
    57: "radiobutton",
    58: "qrcode",
    59: "xfloat",
    60: "expic",
    61: "combobox",
    62: "sltext",
    63: "filestream",
    64: "filedirectory",
    65: "filebrowser",
    66: "datarecord",
    67: "switchbutton",
    68: "textselect",
    69: "Printer3D",
    70: "ColPic",
    98: "button",
    106: "prog",         # ProgressBar
    109: "touch",        # Hotspot
    110: "TouchUpVP",
    112: "pic",          # Picture
    113: "picc",         # type 113 q0 (cropped picture)
    116: "text",
    121: "page",
    122: "zhizhen",      # Gauge
    125: "inputTxt_VP",
    126: "input_VP",
    127: "yfloat_VP",
    128: "text_VP",
    129: "xfloat_VP",
    130: "prog_VP",
    131: "pic_VP",
    132: "button_VP",
    133: "QrcodeVP",
    134: "TouchBsVP",
    135: "TouchMenuVP",
    136: "MovepicVP",
    137: "CurveVP",
    138: "JPEGVP",
    139: "DraVP",
    254: "other",
}

# Component types that emit an empty init bytecode block (length=0).
EMPTY_INIT_TYPES: frozenset[int] = frozenset({
    0,   # Waveform
    1,   # Slider
    5,   # CropPicture
    50,  # TouchCap
    51,  # Timer
    52,  # Variable
    109, # Hotspot
})


# -- Opcode-header table --------------------------------------------

# Observed byte sequences in nxt-1.67.1 / model 100 fixtures. These
# differ slightly from TFTTool's nxt-1.65.1 mapping (`setbrush` is at
# slot 0x1c in 1.65.1 but appears as `09 1d 08` in 1.67.1 — likely a
# one-slot insertion in the size-8 opcode list).
OPCODE: dict[str, bytes] = {
    "setbrush": bytes((0x09, 0x1d, 0x08)),  # 14-arg state-set
    "qrcode":   bytes((0x09, 0x08, 0x08)),
    "draw3d":   bytes((0x09, 0x07, 0x08)),
    "nstr":     bytes((0x09, 0x13, 0x04)),
    "fstr":     bytes((0x09, 0x14, 0x04)),  # observed as 09 14 04 in 1.67.1
    # zstr (bounded text render) is `09 17 04` in 1.67.1, verified against
    # the Text / Button / Button_T / GText init blocks in
    # `17_more_components/17.tft`. The `09 15 04` value in the older nxt-
    # 1.65.1 table is for a different firmware; the 1.67.1 fixtures
    # consistently use `09 17 04` for the 5-arg
    # `zstr <x0>,<y0>,<x1>,<y1>,<txt>` draw.
    "zstr":     bytes((0x09, 0x17, 0x04)),
    "xstr":     bytes((0x09, 0x14, 0x04)),  # collides with fstr in 1.67.1
    "fill":     bytes((0x09, 0x0d, 0x04)),
    "pic":      bytes((0x09, 0x01, 0x04)),
    "xpic":     bytes((0x09, 0x0a, 0x04)),
    "cir":      bytes((0x09, 0x04, 0x04)),
    "cirs":     bytes((0x09, 0x16, 0x04)),
    "addt":     bytes((0x09, 0x17, 0x04)),
    # Page-init / "init" mnemonic — used by the page itself
    "init":     bytes((0x09, 0x18, 0x04)),
    # Compact `=N*M/X` form, e.g. Checkbox helper "sya0='&w&'/4"
    "shorthand": bytes((0x04, 0x04, 0x05)),
}


# -- Template DSL ----------------------------------------------------

# A template line is a tuple ``(mnemonic, args)`` where ``args`` is a
# list of ``Arg``: each Arg is either:
#   ``('lit', str)`` — emit ASCII bytes (the str)
#   ``('attr', str)`` — emit a 5-byte LOAD for the attr's address
#   ``('coord', str)`` — substitute x/y/w/h/id/etc, then emit ASCII
#   ``('expr', str)`` — emit ASCII for an expression that contains
#                       both literals and attr placeholders (rare in
#                       Ref event; common in `if(...)` lines)

# Below: the per-type Ref-event templates, dispatched by `sta`
# (background-fill mode).

# For sta=0 (transparent), sta=1 (border), sta=2 (background pic),
# sta=3 (no fill), the setbrush varies in two positions:
#   * arg 7 (zero-indexed): picc (sta=0,1,3), pic (sta=2)
#   * arg 10: mode literal `0`/`1`/`2`/`3`
# arg 7 special: sta=1 uses `bco`, sta=3 uses literal `0`.

def _setbrush(sta: int, bco_attr: str = "picc", *, mode: int | None = None,
              pw_attr: str = "pw_literal", pw_value: str = "0",
              trailing: str = "0",
              inline_spax_spay: bool = False) -> tuple:
    """Build a `setbrush` template per the editor's sta-dispatch.

    arg7 = the colour/picture/0 in position 7 (the third color arg).

    When ``inline_spax_spay`` is True, spax and spay are emitted as
    ASCII decimal literals (``('inline_attr', ...)``), not as LOAD
    operands.  This applies to text (116), button (98), and button_t
    (53), whose ``attposup`` for spax/spay is -1 in the editor's
    attribute schema (compile-time constant, no RAM backing).
    """
    if mode is None:
        mode = sta
    args = [
        ('coord', 'x'), ('lit', ','),
        ('coord', 'y'), ('lit', ','),
        ('coord', 'w'), ('lit', ','),
        ('coord', 'h'), ('lit', ','),
        ('attr', 'font'), ('lit', ','),
        ('attr', 'pco'), ('lit', ','),
        # arg7 — picc/bco/pic/0 based on sta
    ]
    if sta == 0:
        args += [('attr', 'picc')]
    elif sta == 1:
        args += [('attr', 'bco')]
    elif sta == 2:
        args += [('attr', 'pic')]
    elif sta == 3:
        args += [('lit', '0')]
    else:
        raise ValueError(f"unknown sta={sta}")
    spax_arg = ('inline_attr', 'spax') if inline_spax_spay else ('attr', 'spax')
    spay_arg = ('inline_attr', 'spay') if inline_spax_spay else ('attr', 'spay')
    args += [
        ('lit', ','),
        ('attr', 'xcen'), ('lit', ','),
        ('attr', 'ycen'), ('lit', ','),
        ('lit', str(mode)), ('lit', ','),
        ('attr', 'isbr'), ('lit', ','),
        spax_arg, ('lit', ','),
        spay_arg, ('lit', ','),
    ]
    # arg14 — `pw` for text (LOAD), literal 0 for others (inline)
    if pw_attr == "pw":
        args += [('attr', 'pw')]
    else:
        args += [('lit', pw_value)]
    args += [('lit', ','), ('lit', trailing)]
    return ('setbrush', args)


def _fstr() -> tuple:
    return ('fstr', [
        ('attr', 'val'), ('lit', ','),
        ('attr', 'vvs0'), ('lit', ','),
        ('attr', 'vvs1'),
    ])


def _nstr() -> tuple:
    return ('nstr', [
        ('attr', 'val'), ('lit', ','),
        ('coord', 'lenth'), ('lit', ','),
        ('coord', 'format'),
    ])


def _zstr_txt() -> tuple:
    """Text/Button/Button_T zstr: 4 long-form 32767 bounds + LOAD(txt).

    Verified against `17_more_components/17.tft` blocks @0x80721
    (t0 zstr), @0x8186a (b0 zstr), @0x81dc9 (b0_t zstr). Each emits
    32767 as the 5-byte long-form literal `03 ff 7f 00 00` (NOT ASCII)
    because the editor's expression compiler uses the
    `Strmake_StrToS32` long-form whenever a numeric token won't fit
    in the inline-ASCII budget (>3 chars).
    """
    return ('zstr', [
        ('long', 32767), ('lit', ','),
        ('long', 32767), ('lit', ','),
        ('long', 32767), ('lit', ','),
        ('long', 32767), ('lit', ','),
        ('attr', 'txt'),
    ])


def _zstr_vvs_txt() -> tuple:
    """gtext (ScrollingText) variant: zstr with vvs0..3 as bounds."""
    return ('zstr', [
        ('attr', 'vvs0'), ('lit', ','),
        ('attr', 'vvs1'), ('lit', ','),
        ('attr', 'vvs2'), ('lit', ','),
        ('attr', 'vvs3'), ('lit', ','),
        ('attr', 'txt'),
    ])


def _fill_page() -> tuple:
    """Background fill at the component's bounding box, using the page
    bco (a per-page attribute outside the per-component window)."""
    return ('fill', [
        ('coord', 'x'), ('lit', ','),
        ('coord', 'y'), ('lit', ','),
        ('coord', 'w'), ('lit', ','),
        ('coord', 'h'), ('lit', ','),
        ('attr', 'page_bco'),
    ])


def _pic() -> tuple:
    return ('pic', [
        ('coord', 'x'), ('lit', ','),
        ('coord', 'y'), ('lit', ','),
        ('attr', 'pic'),
    ])


def _qrcode(sta: int) -> tuple:
    # When pic is unused (sta != 2), the editor inlines `65535` as a
    # **long-form int literal** (`03 ff ff 00 00`), not ASCII. Use
    # ('long', N) to emit that form.
    return ('qrcode', [
        ('coord', 'x'), ('lit', ','),
        ('coord', 'y'), ('lit', ','),
        ('coord', 'w'), ('lit', ','),
        ('attr', 'bco'), ('lit', ','),
        ('attr', 'pco'), ('lit', ','),
        ('attr', 'pic') if sta == 2 else ('long', 65535), ('lit', ','),
        ('attr', 'dis'), ('lit', ','),
        ('attr', 'txt'),
    ])


# -- Per-type Ref event templates -----------------------------------

# Returns a list of source-line tuples that the editor would emit
# for the Ref event of a component of the given type / sta / etc.

def ref_templates(comp_type: int, attrs: dict) -> list[tuple]:
    """Return the list of (mnemonic, args) tuples for the Ref event."""
    if comp_type in EMPTY_INIT_TYPES:
        return []

    label = TYPE_LABELS.get(comp_type, str(comp_type))
    sta = int(attrs.get('sta', 0))
    style = int(attrs.get('style', 0))

    if label == "page":
        return [_fill_page()]

    if label in ("number", "xfloat"):
        draw = _nstr() if label == "number" else _fstr()
        return [_setbrush(sta, mode=sta), draw]

    if label in ("text", "gtext"):
        draw = _zstr_txt() if label == "text" else _zstr_vvs_txt()
        # text (116) has attposup == -1 for spax/spay (compile-time
        # constant, inlined as ASCII). gtext (55) has attposup positive
        # → spax/spay are LOAD operands.
        # See findings/text-setbrush-variant.md for the dispatch table.
        sb = _setbrush(
            sta, mode=sta,
            pw_attr=("pw" if label == "text" else "pw_literal"),
            inline_spax_spay=(label == "text"),
        )
        return [sb, draw]

    if label == "pic":
        return [_pic()]

    if label == "picc":
        # xpic with VVS offsets — q0 / cropped picture
        return [('xpic', [
            ('coord', 'x'), ('lit', ','),
            ('coord', 'y'), ('lit', ','),
            ('coord', 'w'), ('lit', ','),
            ('coord', 'h'), ('lit', ','),
            ('expr', ('coord', 'x', '+', ('attr', 'vvs0'))), ('lit', ','),
            ('expr', ('coord', 'y', '+', ('attr', 'vvs1'))), ('lit', ','),
            ('attr', 'picc'),
        ])]

    if label == "qrcode":
        return [_qrcode(sta)]

    # button / button_t / checkbox / radio — these have if/else
    # control flow which requires the script-compiler (gap #3).
    # For now, return the source-line templates; the bytecode
    # compilation of control-flow lines is out of scope for this
    # encoder.
    if label == "button":
        # if(val==1) { setbrush <pressed> } else { setbrush <released> } zstr
        return [
            ('if', [('lit', "'&val&'==1")]),
            ('lbrace', []),
            _setbrush_buttoncolours(pressed=True, sta=sta, inline_spax_spay=True),
            ('rbrace_else', []),
            ('lbrace', []),
            _setbrush_buttoncolours(pressed=False, sta=sta, inline_spax_spay=True),
            ('rbrace', []),
            _zstr_txt(),
        ]
    if label == "button_t":
        # Same shape as button — separate event also defined for down/up
        return [
            ('if', [('lit', "'&val&'==1")]),
            ('lbrace', []),
            _setbrush_buttoncolours(pressed=True, sta=sta, inline_spax_spay=True),
            ('rbrace_else', []),
            ('lbrace', []),
            _setbrush_buttoncolours(pressed=False, sta=sta, inline_spax_spay=True),
            ('rbrace', []),
            _zstr_txt(),
        ]
    if label == "checkbox":
        return [
            ('assign', [('lit', 'sya0=')] + _wexpr_div(4)),
            ('assign', [('lit', 'sya1=')] + _wexpr_minus_sya('sya0+sya0')),
            ('fill', [
                ('coord', 'x'), ('lit', ','),
                ('coord', 'y'), ('lit', ','),
                ('coord', 'w'), ('lit', ','),
                ('coord', 'h'), ('lit', ','),
                ('attr', 'bco'),
            ]),
            ('if', [('lit', "'&val&'==1")]),
            ('lbrace', []),
            ('fill', [
                ('expr', ('coord', 'x', '+', 'sya0')), ('lit', ','),
                ('expr', ('coord', 'y', '+', 'sya0')), ('lit', ','),
                ('lit', 'sya1'), ('lit', ','),
                ('lit', 'sya1'), ('lit', ','),
                ('attr', 'pco'),
            ]),
            ('rbrace', []),
        ]
    if label == "radiobutton":
        # Complex (cir/cirs with center+radius math). Stub out:
        return [
            ('comment', [('lit', '# radiobutton: see findings doc')])
        ]
    if label == "prog":
        dir_ = int(attrs.get('dir', 0))  # 0=horizontal, 1=vertical
        if dir_ == 0:
            return [
                ('assign', [('lit', 'sya0=')] + _val_times_w_div_100()),
                ('fill', [
                    ('coord', 'x'), ('lit', ','),
                    ('coord', 'y'), ('lit', ','),
                    ('lit', 'sya0'), ('lit', ','),
                    ('coord', 'h'), ('lit', ','),
                    ('attr', 'pco'),
                ]),
                ('fill', [
                    ('expr', ('coord', 'x', '+', 'sya0')), ('lit', ','),
                    ('coord', 'y'), ('lit', ','),
                    ('expr', ('coord', 'w', '-', 'sya0')), ('lit', ','),
                    ('coord', 'h'), ('lit', ','),
                    ('attr', 'bco'),
                ]),
            ]
        # vertical / other styles: see findings doc
        return [('comment', [('lit', '# prog dir!=0: see findings doc')])]

    if label == "timer":
        # timer Ref doesn't render — the timer event template is:
        # timerset '&en&','&id&','&tim&',0
        return []

    # Unknown / not yet templated — return empty to signal that gap #1
    # data alone won't fix this; this type needs a template.
    return []


def _setbrush_buttoncolours(*, pressed: bool, sta: int, style: int = 0,
                             inline_spax_spay: bool = False) -> tuple:
    """Helper: button's setbrush uses pco2/picc2 when pressed,
    pco/picc when released.

    Note that ``picc2``/``picc`` actually aliases ``bco2/bco`` (sta=1)
    or ``pic2/pic`` (sta=2) — they all share the same binattinf
    record because their per-attribute ``attpos`` collapses onto +6
    (or +8) for the GuiObjButton/Button_T layout. So we always emit
    LOAD(``picc`` / ``picc2``); the runtime resolves to whichever
    visual mode is active.

    When ``inline_spax_spay`` is True, spax and spay are emitted as
    ASCII decimal literals instead of LOAD operands.  Required for
    button (98) and button_t (53) whose spax/spay have ``attposup == -1``
    in the editor's attribute schema.  pw is also -1 for these types,
    so it is always emitted as a literal ``'0'`` here.

    ``style`` controls the setbrush trailing (arg 14):
      * sta=1, style=4 → trailing="1" (the style=4 case adds a draw3d
        bevel; the editor still emits trailing=1 even though it is
        unused by the render path for this style — verified against
        the b0 / b0_t blocks in 17_more_components/17.tft).
      * Otherwise → trailing="0".
    """
    color_pair = ('pco2', 'picc2') if pressed else ('pco', 'picc')
    # picc/picc2 aliases bco/bco2/pic/pic2 — all share +6/+8 in
    # GuiObjButton's attribute layout, so a single LOAD(picc)/LOAD(picc2)
    # is correct regardless of sta. (See attrs-raw.txt and the LOAD
    # operand decoding in `17_more_components/17.tft` b0 / b0_t.)
    spax_arg = ('inline_attr', 'spax') if inline_spax_spay else ('attr', 'spax')
    spay_arg = ('inline_attr', 'spay') if inline_spax_spay else ('attr', 'spay')
    trailing = '1' if (sta == 1 and style == 4) else '0'
    return ('setbrush', [
        ('coord', 'x'), ('lit', ','),
        ('coord', 'y'), ('lit', ','),
        ('coord', 'w'), ('lit', ','),
        ('coord', 'h'), ('lit', ','),
        ('attr', 'font'), ('lit', ','),
        ('attr', color_pair[0]), ('lit', ','),
        ('attr', color_pair[1]), ('lit', ','),
        ('attr', 'xcen'), ('lit', ','),
        ('attr', 'ycen'), ('lit', ','),
        ('lit', str(sta)), ('lit', ','),
        ('attr', 'isbr'), ('lit', ','),
        spax_arg, ('lit', ','),
        spay_arg, ('lit', ','),
        ('lit', '0'), ('lit', ','),
        ('lit', trailing),
    ])


def _wexpr_div(n: int) -> list:
    return [('coord', 'w'), ('lit', f'/{n}')]


def _wexpr_minus_sya(expr: str) -> list:
    return [('coord', 'w'), ('lit', f'-{expr}')]


def _val_times_w_div_100() -> list:
    return [('attr', 'val'), ('lit', '*'),
            ('coord', 'w'), ('lit', '/100')]


# -- Low-level emitter ----------------------------------------------

def _load_op(addr: int) -> bytes:
    """5-byte LOAD: `01 LL HH 00 00`.

    The encoded value is the **attribute record index** (u16) into
    the per-page 24-byte-stride attribute-record table at
    ``strdata + pagexinxi.attdataaddr``. See
    ``nextion/scripts/lib/tft_attrs.py`` and
    ``nextion/findings/attribute-records.md`` for the table layout.
    """
    if not (0 <= addr <= 0xFFFFFFFF):
        raise ValueError(f"LOAD addr out of range: {addr}")
    return b"\x01" + struct.pack("<I", addr)


def _long_lit(value: int) -> bytes:
    """5-byte long-form int literal: `03 LL HH HH HH`. The editor emits
    this form for compile-time-constant int values that don't fit a
    short ASCII representation in setbrush/qrcode arg context
    (e.g. `65535` becomes `03 ff ff 00 00`)."""
    return b"\x03" + struct.pack("<I", value & 0xFFFFFFFF)


def _emit_arg(arg, x, y, w, h, comp_id, page_id, attrs, attr_addr) -> bytes:
    kind = arg[0]
    if kind == 'lit':
        return arg[1].encode("ascii")
    if kind == 'coord':
        name = arg[1]
        return _resolve_coord(name, x, y, w, h, comp_id, page_id, attrs).encode("ascii")
    if kind == 'attr':
        # Resolve the attribute's LOAD address via callback.
        return _load_op(attr_addr(arg[1]))
    if kind == 'inline_attr':
        # Emit the attribute's current value as ASCII decimal (no LOAD).
        # Used for attributes whose attposup == -1 in the editor schema
        # (compile-time constant, no RAM slot): spax/spay for text/button/button_t.
        value = str(attrs.get(arg[1], 0))
        return value.encode("ascii")
    if kind == 'long':
        return _long_lit(arg[1])
    if kind == 'expr':
        # ('expr', tuple) where tuple is something like ('coord', 'x', '+', ('attr', 'vvs0'))
        # Emit each element in order.
        out = bytearray()
        for piece in arg[1:][0]:  # arg[1] is the tuple, iterate its items
            if isinstance(piece, str):
                out += piece.encode("ascii")
            else:
                out += _emit_arg(piece, x, y, w, h, comp_id, page_id, attrs, attr_addr)
        return bytes(out)
    raise ValueError(f"unknown arg kind: {kind}")


def _resolve_coord(name: str, x, y, w, h, comp_id, page_id, attrs) -> str:
    """Resolve a compile-time-constant placeholder to a decimal
    string. Used for x/y/w/h, id, pageid, lenth, format, dis, en,
    tim — these come from the component definition, not the
    runtime attribute table."""
    if name == 'x': return str(x)
    if name == 'y': return str(y)
    if name == 'w': return str(w)
    if name == 'h': return str(h)
    if name == 'id': return str(comp_id)
    if name == 'pageid': return str(page_id)
    # Otherwise look up from attrs as a literal
    v = attrs.get(name, 0)
    return str(int(v))


def _emit_line(line: tuple, x, y, w, h, comp_id, page_id, attrs, attr_addr) -> bytes:
    """Emit a single length-prefixed bytecode block from a template
    line. Returns ``b''`` for templates we can't compile yet (control
    flow, expression-heavy lines)."""
    mnemonic, args = line
    if mnemonic in {"if", "lbrace", "rbrace", "rbrace_else", "assign", "comment"}:
        # These need the full script-bytecode compiler (gap #3).
        return b""
    if mnemonic not in OPCODE:
        raise ValueError(f"unknown mnemonic: {mnemonic}")
    body = bytearray()
    body += OPCODE[mnemonic]
    for a in args:
        body += _emit_arg(a, x, y, w, h, comp_id, page_id, attrs, attr_addr)
    return bytes(body)


# -- Button/Button_T control-flow encoder ---------------------------

def _draw3d_button_bevel(*, pressed: bool) -> tuple:
    """Style=4 button draw3d bevel.

    The editor emits a `draw3d x,y,w,h,<lightc>,<darkc>,1` block. The
    colour pair flips between pressed and released:

      * pressed  → (0x4228, 0xe71c) = (16936, 59164)
      * released → (0xe71c, 0x4228) = (59164, 16936)

    Both are emitted as 5-byte long-form literals (because 16936 >999
    so doesn't fit inline-ASCII).

    Verified against `17_more_components/17.tft`:
      * b0 (Button) pressed draw3d  @ 0x81892 (after block [1] / [2])
      * b0 (Button) released draw3d @ 0x81923
      * b0_t (Button_T) draw3d blocks at the parallel offsets.
    """
    if pressed:
        c1, c2 = 16936, 59164
    else:
        c1, c2 = 59164, 16936
    return ('draw3d', [
        ('coord', 'x'), ('lit', ','),
        ('coord', 'y'), ('lit', ','),
        ('coord', 'w'), ('lit', ','),
        ('coord', 'h'), ('lit', ','),
        ('long', c1), ('lit', ','),
        ('long', c2), ('lit', ','),
        ('lit', '1'),
    ])


def _encode_button_init(
    comp: "Component",
    attr_addr: Callable[[str], int],
) -> bytes:
    """Encode the button/button_t Ref event using if/else control flow.

    Emits, for sta=1 / style=4 (the only fixture-verified combo):

        if(val==1) {
          setbrush_pressed; zstr; draw3d_pressed   # blocks [1..3]
        } else {
          setbrush_released; zstr; draw3d_released # blocks [5..7]
        }

    The compiled cglist is::

        [0] cjmp(val == 1, FAIL ⇒ skip past pressed branch)
        [1] setbrush(pressed colours)
        [2] zstr(32767×4, txt)
        [3] draw3d(pressed bevel colours)         # only if style=4
        [4] jmp(skip past released branch)
        [5] setbrush(released colours)
        [6] zstr(32767×4, txt)
        [7] draw3d(released bevel colours)        # only if style=4

    Key shape details (verified byte-identical against
    `17_more_components/17.tft` b0 page 2 and b0_t page 3):

      * The comparator is the **un-negated** `==` (endid 1). cjmp's
        VM semantics are "jump on FAIL" (see
        `findings/script-control-flow.md`), so `cjmp(val==1)` falls
        through into the pressed branch and jumps to the released
        branch on failure.
      * Each branch carries its own zstr (and optionally draw3d).
        Earlier docs implied a single trailing zstr outside the
        if-else; that's incorrect for the F-series init bytecode.
      * draw3d only appears for `sta=1, style=4` (the bevel border
        style); for other styles, it is omitted.

    This bypasses the normal ``_emit_line`` path because the if/else
    structure requires ``script_compiler_extras`` to emit cjmp/jmp
    opcodes and patch byte-distance targets.
    """
    sta = int(comp.attrs.get('sta', 0))
    style = int(comp.attrs.get('style', 0))
    has_draw3d = (sta == 1 and style == 4)
    x, y, w, h = comp.x, comp.y, comp.w, comp.h
    page_id, comp_id = comp.page_id, comp.comp_id
    attrs = comp.attrs

    def emit(line: tuple) -> bytes:
        return _emit_line(line, x, y, w, h, comp_id, page_id, attrs, attr_addr)

    pressed_set = emit(_setbrush_buttoncolours(
        pressed=True, sta=sta, style=style, inline_spax_spay=True))
    released_set = emit(_setbrush_buttoncolours(
        pressed=False, sta=sta, style=style, inline_spax_spay=True))
    zstr_body = emit(_zstr_txt())
    pressed_d3d = emit(_draw3d_button_bevel(pressed=True)) if has_draw3d else None
    released_d3d = emit(_draw3d_button_bevel(pressed=False)) if has_draw3d else None

    # cjmp: if(val == 1) — un-negated. cjmp jumps when condition
    # FAILS, so the fall-through path is the pressed branch.
    lhs = _load_op(attr_addr("val"))
    rhs = b"1"
    endid_eq = COMPARATOR_ENDID["=="]  # 1

    # Build cglist. Entry-distance placeholders:
    #   cjmp ⇒ skip past pressed branch (3 or 4 entries: set, zstr,
    #          [d3d], jmp).
    #   jmp  ⇒ skip past released branch (2 or 3 entries: set, zstr,
    #          [d3d]).
    pressed_entries = [pressed_set, zstr_body]
    if pressed_d3d is not None:
        pressed_entries.append(pressed_d3d)
    released_entries = [released_set, zstr_body if released_d3d is None
                        else zstr_body]
    if released_d3d is not None:
        released_entries = [released_set, zstr_body, released_d3d]

    cjmp_skip = len(pressed_entries) + 1  # +1 for the jmp entry
    jmp_skip = len(released_entries)

    cjmp_body = emit_cjmp(lhs, rhs, endid_eq, placeholder_int=cjmp_skip)
    jmp_body = emit_jmp(jmp_skip)

    cglist: list[bytes] = (
        [cjmp_body] + pressed_entries + [jmp_body] + released_entries
    )

    # Stage 2: convert entry distances to byte distances.
    cjmp_template = cjmp_body[:3]
    convert_entry_to_byte_distances(cglist, cjmp_template)

    return flatten_cglist(cglist)


# -- Public API -----------------------------------------------------

@dataclass
class Component:
    """One component to encode."""
    comp_type: int
    comp_id: int
    page_id: int
    x: int
    y: int
    w: int
    h: int
    attrs: dict     # name -> value (compile-time-constant attrs only)


def encode_init_block(
    comp: Component,
    attr_addr: Callable[[str], int],
) -> bytes:
    """Return the concatenated length-prefixed init-bytecode blocks
    for one component's Ref event.

    `attr_addr(name) -> int` resolves a dynamic-attr placeholder
    (e.g. "font", "pco", "bco", "val", "vvs0", "page_bco") to its
    LOAD address in the per-page attribute-value region. This is
    the gap-#1 dependency.

    Returns ``b""`` if the type has no Ref-event templates (Hotspot,
    Timer, Variable, Slider, Waveform, CropPicture).
    """
    label = TYPE_LABELS.get(comp.comp_type, "")
    if label in ("button", "button_t"):
        return _encode_button_init(comp, attr_addr)

    templates = ref_templates(comp.comp_type, comp.attrs)
    if not templates:
        return b""
    out = bytearray()
    for line in templates:
        body = _emit_line(
            line, comp.x, comp.y, comp.w, comp.h,
            comp.comp_id, comp.page_id, comp.attrs, attr_addr,
        )
        if not body:
            # Skip lines we can't compile yet (control flow).
            # An empty length-0 block is the convention for "no code"
            # but here we just omit — the caller's downstream code
            # should not rely on a specific block count for types
            # whose Ref event contains control flow.
            continue
        out += struct.pack("<I", len(body)) + body
    return bytes(out)


def disassemble_blocks_to_lines(blob: bytes) -> list[tuple[bytes, bytes]]:
    """Helper: split a stream of length-prefixed blocks into
    (length, body) pairs. Skips zero-length blocks. Useful for
    round-trip verification."""
    out: list[tuple[bytes, bytes]] = []
    i = 0
    n = len(blob)
    while i + 4 <= n:
        ln = struct.unpack_from("<I", blob, i)[0]
        if ln == 0:
            i += 4
            continue
        if i + 4 + ln > n:
            break
        out.append((blob[i:i + 4], blob[i + 4:i + 4 + ln]))
        i += 4 + ln
    return out


# -- Round-trip test self-check -------------------------------------

def _self_test():
    """Encode an XFloat at (0,20,160,50), sta=1, and compare to the
    block observed at file offset 0x800e2 in
    tests/editor outputs/17_more_components/17.tft.

    The LOAD addresses are taken from the observed bytecode; in real
    use, `attr_addr` would compute these from the per-page attribute
    layout (gap #1).
    """
    # Observed: 09 1d 08 0,20,160,50,LOAD(55),LOAD(57),LOAD(56),LOAD(58),LOAD(59),1,LOAD(63),LOAD(64),LOAD(65),0,0
    # That's setbrush x,y,w,h,font,pco,bco,xcen,ycen,1,isbr,spax,spay,0,0
    # plus a fstr block: 09 14 04 LOAD(60),LOAD(61),LOAD(62) = fstr val,vvs0,vvs1
    # plus a fill block: 09 0d 04 0,20,160,50,LOAD(24) = fill x,y,w,h,page_bco
    observed_addrs = {
        'font': 55, 'pco': 57, 'bco': 56, 'picc': 56,  # sta=1 uses bco
        'xcen': 58, 'ycen': 59,
        'isbr': 63, 'spax': 64, 'spay': 65,
        'val': 60, 'vvs0': 61, 'vvs1': 62,
        'page_bco': 24,
    }

    def addr(name: str) -> int:
        return observed_addrs[name]

    comp = Component(
        comp_type=59, comp_id=1, page_id=0,
        x=0, y=20, w=160, h=50,
        attrs={'sta': 1, 'style': 4},
    )

    blob = encode_init_block(comp, addr)
    expected_b1 = bytes.fromhex(
        "091d08302c32302c3136302c35302c01370000002c01390000002c01380000002c"
        "013a0000002c013b0000002c312c013f0000002c01400000002c01410000002c302c30"
    )
    expected_b2 = bytes.fromhex(
        "091404013c0000002c013d0000002c013e000000"
    )
    expected = (
        struct.pack("<I", len(expected_b1)) + expected_b1
        + struct.pack("<I", len(expected_b2)) + expected_b2
    )

    # The encoder produces setbrush + fstr (Ref event). The actual
    # bytecode stream in the TFT also has a trailing `fill x,y,w,h,
    # page_bco` block, but that's part of the editref/load-page
    # routine, not the Ref event — see findings doc.
    if blob != expected:
        print("ENCODE MISMATCH")
        print(f"  got:      {blob.hex()}")
        print(f"  expected: {expected.hex()}")
        return False
    print("OK: XFloat (0,20,160,50) sta=1 setbrush+fstr round-trips byte-for-byte")
    return True


def _self_test_qrcode():
    """Verify a QRCode at (131,63,82) sta=0 round-trips."""
    observed = {'bco': 1667, 'pco': 1668, 'dis': 1666, 'txt': 1669}
    comp = Component(comp_type=58, comp_id=10, page_id=3,
                     x=131, y=63, w=82, h=82,
                     attrs={'sta': 0})
    blob = encode_init_block(comp, lambda n: observed[n])
    expected_body = bytes.fromhex(
        "0908083133312c36332c38322c01830600002c01840600002c03ffff00002c"
        "01820600002c0185060000"
    )
    expected = struct.pack("<I", len(expected_body)) + expected_body
    if blob != expected:
        print("QRCODE MISMATCH")
        print(f"  got:      {blob.hex()}")
        print(f"  expected: {expected.hex()}")
        return False
    print("OK: QRCode (131,63,82) sta=0 round-trips byte-for-byte")
    return True


def _self_test_picture():
    """Verify Picture pic 166,77,LOAD(1304) round-trip."""
    comp = Component(comp_type=112, comp_id=5, page_id=1,
                     x=166, y=77, w=213, h=158, attrs={})
    blob = encode_init_block(comp, lambda n: {'pic': 1304}.get(n, 0))
    expected_body = bytes.fromhex("0901043136362c37372c0118050000")
    expected = struct.pack("<I", len(expected_body)) + expected_body
    if blob != expected:
        print("PICTURE MISMATCH")
        print(f"  got:      {blob.hex()}")
        print(f"  expected: {expected.hex()}")
        return False
    print("OK: Picture pic 166,77,1304 round-trips byte-for-byte")
    return True


def _self_test_page():
    """Verify Page fill round-trip."""
    comp = Component(comp_type=121, comp_id=0, page_id=0,
                     x=0, y=0, w=480, h=320, attrs={})
    blob = encode_init_block(comp, lambda n: {'page_bco': 24}.get(n, 0))
    expected_body = (b"\x09\x0d\x04" + b"0,0,480,320," + b"\x01" + struct.pack("<I", 24))
    expected = struct.pack("<I", len(expected_body)) + expected_body
    if blob != expected:
        print("PAGE MISMATCH")
        return False
    print("OK: Page (0,0,480,320) fill round-trips byte-for-byte")
    return True


def _self_test_text_full():
    """Verify the FULL Text component init block (setbrush + zstr)
    round-trips byte-identically against `17_more_components/17.tft`
    t0 @ file offset 0x806dd (init_off=0x6dd, strdata=0x80000).

    Block sequence (from the fixture):
      [0] setbrush x,y,w,h, font, pco, bco, xcen, ycen, 1, isbr,
                   spax_inline, spay_inline, pw, 0
      [1] zstr <long 32767>×4, txt

    LOAD addresses are taken from the fixture's binattinf table; in
    real use, ``attr_addr`` would resolve these from the per-page
    attribute layout (see ``tft_attrs_encoder.py``).
    """
    observed_addrs = {
        'font': 457, 'pco': 459, 'bco': 458,
        'xcen': 460, 'ycen': 461,
        'isbr': 465, 'pw': 462, 'txt': 463,
    }
    def addr(name: str) -> int:
        if name not in observed_addrs:
            raise KeyError(f"unexpected attr_addr({name!r})")
        return observed_addrs[name]

    comp = Component(
        comp_type=116, comp_id=11, page_id=0,
        x=0, y=69, w=160, h=31,
        attrs={'sta': 1, 'style': 4, 'spax': 0, 'spay': 0},
    )

    expected_b0 = bytes.fromhex(
        "091d08302c36392c3136302c33312c01c90100002c01cb0100002c01ca0100002c"
        "01cc0100002c01cd0100002c312c01d10100002c302c302c01ce0100002c30"
    )
    expected_b1 = bytes.fromhex(
        "09170403ff7f00002c03ff7f00002c03ff7f00002c03ff7f00002c01cf010000"
    )
    expected = (
        struct.pack("<I", len(expected_b0)) + expected_b0
        + struct.pack("<I", len(expected_b1)) + expected_b1
    )

    blob = encode_init_block(comp, addr)
    if blob != expected:
        print("TEXT FULL MISMATCH")
        print(f"  got:      {blob.hex()}")
        print(f"  expected: {expected.hex()}")
        return False
    print("OK: Text (0,69,160,31) sta=1 full setbrush+zstr round-trips byte-for-byte")
    return True


def _self_test_button():
    """Verify the FULL Button component init block (cjmp+pressed branch
    +jmp+released branch, each with setbrush/zstr/draw3d for style=4)
    round-trips byte-identically against `17_more_components/17.tft`
    b0 page 2 @ file offset 0x81814 (init_off=0x1814).

    Button schema (F-series): sta=1, style=4 → bevel border with
    draw3d. The cjmp uses un-negated `==` (endid 1), so the
    fall-through path renders the pressed state.
    """
    # LOAD record indices from the fixture (per-component base = 1260
    # plus the refallatt offset for each attr).
    observed_addrs = {
        'font': 1264,
        'picc':  1265,  # aliased: pic/picc/bco share +6 → one record
        'picc2': 1266,  # aliased: pic2/picc2/bco2 share +8 → one record
        'pco':   1267,
        'pco2':  1268,
        'xcen':  1269,
        'ycen':  1270,
        'val':   1271,
        'txt':   1272,
        'isbr':  1274,
    }
    def addr(name: str) -> int:
        if name not in observed_addrs:
            raise KeyError(f"unexpected attr_addr({name!r})")
        return observed_addrs[name]

    comp = Component(
        comp_type=98, comp_id=4, page_id=2,
        x=52, y=233, w=100, h=50,
        attrs={'sta': 1, 'style': 4, 'spax': 0, 'spay': 0},
    )

    # Concatenated expected blocks from the fixture dump (8 blocks).
    expected_blocks_hex = [
        "09000401f70400002c312c312c0393000000",
        "091d0835322c3233332c3130302c35302c01f00400002c01f40400002c01f20400002c"
        "01f50400002c01f60400002c312c01fa0400002c302c302c302c31",
        "09170403ff7f00002c03ff7f00002c03ff7f00002c03ff7f00002c01f8040000",
        "09070835322c3233332c3130302c35302c03284200002c031ce700002c31",
        "54200388000000",
        "091d0835322c3233332c3130302c35302c01f00400002c01f30400002c01f10400002c"
        "01f50400002c01f60400002c312c01fa0400002c302c302c302c31",
        "09170403ff7f00002c03ff7f00002c03ff7f00002c03ff7f00002c01f8040000",
        "09070835322c3233332c3130302c35302c031ce700002c03284200002c31",
    ]
    expected = b""
    for h in expected_blocks_hex:
        body = bytes.fromhex(h)
        expected += struct.pack("<I", len(body)) + body

    blob = encode_init_block(comp, addr)
    if blob != expected:
        print("BUTTON MISMATCH")
        print(f"  got:      {blob.hex()}")
        print(f"  expected: {expected.hex()}")
        # Block-by-block diff
        gb = disassemble_blocks_to_lines(blob)
        eb = disassemble_blocks_to_lines(expected)
        for i in range(max(len(gb), len(eb))):
            g = gb[i][1].hex() if i < len(gb) else "(missing)"
            e = eb[i][1].hex() if i < len(eb) else "(missing)"
            mark = "  " if g == e else "!!"
            print(f"  {mark}[{i}] got={g}")
            print(f"  {mark}    exp={e}")
        return False
    print("OK: Button (52,233,100,50) sta=1 style=4 full cjmp+pressed+jmp+released "
          "round-trips byte-for-byte")
    return True


def _self_test_button_t():
    """Same shape as ``_self_test_button`` but for the Button_T
    (DualStateButton, lei 53) instance b0 on page 3 of
    `17_more_components/17.tft` (file offset 0x81d71).

    Button_T's per-component attribute layout is identical to
    GuiObjButton's, so the encoder uses the same code path. This
    test exists to confirm parity across both lei codes.
    """
    observed_addrs = {
        'font': 1564,        # 0x61c
        'picc':  1565,       # 0x61d
        'picc2': 1566,       # 0x61e
        'pco':   1567,       # 0x61f
        'pco2':  1568,       # 0x620
        'xcen':  1569,       # 0x621
        'ycen':  1570,       # 0x622
        'val':   1571,       # 0x623
        'txt':   1572,       # 0x624
        'isbr':  1574,       # 0x626
    }
    def addr(name: str) -> int:
        if name not in observed_addrs:
            raise KeyError(f"unexpected attr_addr({name!r})")
        return observed_addrs[name]

    comp = Component(
        comp_type=53, comp_id=7, page_id=3,
        x=409, y=161, w=60, h=60,
        attrs={'sta': 1, 'style': 4, 'spax': 0, 'spay': 0},
    )

    expected_blocks_hex = [
        "09000401230600002c312c312c0393000000",
        "091d083430392c3136312c36302c36302c011c0600002c01200600002c011e0600002c"
        "01210600002c01220600002c312c01260600002c302c302c302c31",
        "09170403ff7f00002c03ff7f00002c03ff7f00002c03ff7f00002c0124060000",
        "0907083430392c3136312c36302c36302c03284200002c031ce700002c31",
        "54200388000000",
        "091d083430392c3136312c36302c36302c011c0600002c011f0600002c011d0600002c"
        "01210600002c01220600002c312c01260600002c302c302c302c31",
        "09170403ff7f00002c03ff7f00002c03ff7f00002c03ff7f00002c0124060000",
        "0907083430392c3136312c36302c36302c031ce700002c03284200002c31",
    ]
    expected = b""
    for h in expected_blocks_hex:
        body = bytes.fromhex(h)
        expected += struct.pack("<I", len(body)) + body

    blob = encode_init_block(comp, addr)
    if blob != expected:
        print("BUTTON_T MISMATCH")
        print(f"  got:      {blob.hex()}")
        print(f"  expected: {expected.hex()}")
        gb = disassemble_blocks_to_lines(blob)
        eb = disassemble_blocks_to_lines(expected)
        for i in range(max(len(gb), len(eb))):
            g = gb[i][1].hex() if i < len(gb) else "(missing)"
            e = eb[i][1].hex() if i < len(eb) else "(missing)"
            mark = "  " if g == e else "!!"
            print(f"  {mark}[{i}] got={g}")
            print(f"  {mark}    exp={e}")
        return False
    print("OK: Button_T (409,161,60,60) sta=1 style=4 full init "
          "round-trips byte-for-byte")
    return True


def _self_test_gtext():
    """Verify the GText (ScrollingText, lei 55) setbrush + zstr blocks
    round-trip byte-identically against `17_more_components/17.tft`
    g0 page 3 @ file offset 0x81b48 (init_off=0x1b48).

    GText differs from Text in two ways:
      * spax / spay are stored in RAM (attposup positive in F-series
        → LOAD operands, not inline ASCII).
      * The zstr's first 4 args are vvs0/vvs1/vvs2/vvs3 (also LOADs)
        rather than the literal 32767 bounds Text uses.

    The full fixture init contains two additional blocks (a scroll-init
    `09 24 08` block and a `4c 20 03` motion handler jmp) that are
    part of GText's secondary event chain, not the Ref event. Those
    are out of scope for this encoder.
    """
    observed_addrs = {
        'font': 1395,        # 0x573
        'bco':  1396,        # 0x574 (alias picc/pic at +6)
        'pco':  1397,        # 0x575
        'xcen': 1398,        # 0x576
        'ycen': 1399,        # 0x577
        'isbr': 1406,        # 0x57e
        'spax': 1407,        # 0x57f (RAM-backed for GText)
        'spay': 1408,        # 0x580 (RAM-backed for GText)
        'vvs0': 1409,        # 0x581
        'vvs1': 1410,        # 0x582
        'vvs2': 1411,        # 0x583
        'vvs3': 1412,        # 0x584
        'txt':  1404,        # 0x57c
    }
    def addr(name: str) -> int:
        if name not in observed_addrs:
            raise KeyError(f"unexpected attr_addr({name!r})")
        return observed_addrs[name]

    comp = Component(
        comp_type=55, comp_id=2, page_id=3,
        x=188, y=21, w=240, h=30,
        attrs={'sta': 1, 'style': 4},
    )
    blob = encode_init_block(comp, addr)
    # Take only the first two blocks (setbrush + zstr).
    blocks = disassemble_blocks_to_lines(blob)
    if len(blocks) < 2:
        print(f"GTEXT MISMATCH: expected ≥2 blocks, got {len(blocks)}")
        return False
    sb = blocks[0][1]
    zs = blocks[1][1]

    expected_sb = bytes.fromhex(
        "091d083138382c32312c3234302c33302c01730500002c01750500002c01740500002c"
        "01760500002c01770500002c312c017e0500002c017f0500002c01800500002c302c30"
    )
    expected_zs = bytes.fromhex(
        "09170401810500002c01820500002c01830500002c01840500002c017c050000"
    )
    if sb != expected_sb:
        print("GTEXT SETBRUSH MISMATCH")
        print(f"  got:      {sb.hex()}")
        print(f"  expected: {expected_sb.hex()}")
        return False
    if zs != expected_zs:
        print("GTEXT ZSTR MISMATCH")
        print(f"  got:      {zs.hex()}")
        print(f"  expected: {expected_zs.hex()}")
        return False
    print("OK: GText (188,21,240,30) sta=1 setbrush+zstr round-trips byte-for-byte")
    return True


def _self_test_text():
    """Verify a Text component's setbrush block emits spax/spay as
    ASCII literals (inline_attr), not as LOAD operands.

    Fixture: text at (0, 69, 160, 31), sta=1, style=4, spax=0, spay=0.
    Observed at file offset 0x806dd in
    ``tests/editor outputs/17_more_components/17.tft``.

    Attribute addresses extracted from the observed bytecode:
      font=457, pco=459, bco=458, xcen=460, ycen=461,
      isbr=465, pw=462, txt=463.

    The test verifies only the setbrush block (block 0), because the
    zstr block in this fixture uses ``09 17 04`` (not ``09 15 04``) and
    long-form int literals for 32767 — both are pre-existing
    OPCODE/literal-encoding differences that are not in scope here.
    """
    observed_addrs = {
        'font': 457, 'pco': 459, 'bco': 458,
        'xcen': 460, 'ycen': 461,
        'isbr': 465, 'pw': 462, 'txt': 463,
        # spax/spay are inline — attr_addr will NOT be called for them
    }

    def addr(name: str) -> int:
        if name not in observed_addrs:
            raise KeyError(f"attr_addr called for {name!r} — should be inline")
        return observed_addrs[name]

    comp = Component(
        comp_type=116, comp_id=2, page_id=0,
        x=0, y=69, w=160, h=31,
        attrs={'sta': 1, 'style': 4, 'spax': 0, 'spay': 0},
    )

    blob = encode_init_block(comp, addr)
    # Disassemble the first block (setbrush) only — zstr block 2 uses a
    # different opcode byte and long-literal encoding not yet emulated.
    blocks = disassemble_blocks_to_lines(blob)
    assert blocks, "expected at least one block from Text encoder"
    _len_prefix, setbrush_body = blocks[0]

    expected_setbrush = bytes.fromhex(
        "091d08302c36392c3136302c33312c01c90100002c01cb0100002c"
        "01ca0100002c01cc0100002c01cd0100002c312c01d10100002c"
        "302c302c01ce0100002c30"
    )
    if setbrush_body != expected_setbrush:
        print("TEXT SETBRUSH MISMATCH")
        print(f"  got:      {setbrush_body.hex()}")
        print(f"  expected: {expected_setbrush.hex()}")
        return False
    print("OK: Text (0,69,160,31) sta=1 setbrush emits spax/spay as inline ASCII literals")
    return True


if __name__ == "__main__":
    ok = _self_test()
    ok = _self_test_qrcode() and ok
    ok = _self_test_picture() and ok
    ok = _self_test_page() and ok
    ok = _self_test_text() and ok
    ok = _self_test_text_full() and ok
    ok = _self_test_button() and ok
    ok = _self_test_button_t() and ok
    ok = _self_test_gtext() and ok
    if not ok:
        import sys; sys.exit(1)
