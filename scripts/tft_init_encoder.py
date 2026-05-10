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
from typing import Callable, Iterable
import struct


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
    "zstr":     bytes((0x09, 0x15, 0x04)),
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
              trailing: str = "0") -> tuple:
    """Build a `setbrush` template per the editor's sta-dispatch.

    arg7 = the colour/picture/0 in position 7 (the third color arg).
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
    args += [
        ('lit', ','),
        ('attr', 'xcen'), ('lit', ','),
        ('attr', 'ycen'), ('lit', ','),
        ('lit', str(mode)), ('lit', ','),
        ('attr', 'isbr'), ('lit', ','),
        ('attr', 'spax'), ('lit', ','),
        ('attr', 'spay'), ('lit', ','),
    ]
    # arg14 — `pw` for text, literal 0 for others
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
    return ('zstr', [
        ('lit', '32767'), ('lit', ','),
        ('lit', '32767'), ('lit', ','),
        ('lit', '32767'), ('lit', ','),
        ('lit', '32767'), ('lit', ','),
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
        sb = _setbrush(sta, mode=sta, pw_attr=("pw" if label == "text" else "pw_literal"))
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
            _setbrush_buttoncolours(pressed=True, sta=sta),
            ('rbrace_else', []),
            ('lbrace', []),
            _setbrush_buttoncolours(pressed=False, sta=sta),
            ('rbrace', []),
            _zstr_txt(),
        ]
    if label == "button_t":
        # Same shape as button — separate event also defined for down/up
        return [
            ('if', [('lit', "'&val&'==1")]),
            ('lbrace', []),
            _setbrush_buttoncolours(pressed=True, sta=sta),
            ('rbrace_else', []),
            ('lbrace', []),
            _setbrush_buttoncolours(pressed=False, sta=sta),
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


def _setbrush_buttoncolours(*, pressed: bool, sta: int) -> tuple:
    """Helper: button's setbrush uses pco2/picc2 when pressed,
    pco/picc when released."""
    color_pair = ('pco2', 'picc2') if pressed else ('pco', 'picc')
    if sta == 1:
        # border-mode uses bco/bco2 instead of picc/picc2
        color_pair = (color_pair[0], 'bco2' if pressed else 'bco')
    elif sta == 2:
        # pic mode uses pic2/pic
        color_pair = (color_pair[0], 'pic2' if pressed else 'pic')
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
        ('attr', 'spax'), ('lit', ','),
        ('attr', 'spay'), ('lit', ','),
        ('lit', '0'), ('lit', ','),
        ('lit', '0'),
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
    ``nextion/scripts/tft_attrs.py`` and
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


if __name__ == "__main__":
    ok = _self_test()
    ok = _self_test_qrcode() and ok
    ok = _self_test_picture() and ok
    ok = _self_test_page() and ok
    if not ok:
        import sys; sys.exit(1)
