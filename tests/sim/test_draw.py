from PIL import Image

from sim.state import DisplayState, Page, Component, RGB565
from sim import draw


def make_state(w: int = 100, h: int = 80) -> DisplayState:
    page = Page(name="main", id=0, attrs={"w": w, "h": h, "bco": 0}, components=[])
    return DisplayState(pages={"main": page})


# --- lazy overlay creation ----------------------------------------------------

def test_overlay_allocated_lazily_on_first_draw():
    state = make_state(120, 90)
    assert state.active_page.overlay is None
    draw.cls(state, 0xF800)  # bright red
    overlay = state.active_page.overlay
    assert overlay is not None
    assert overlay.size == (120, 90)
    assert overlay.mode == "RGBA"


def test_overlay_reused_across_calls():
    state = make_state()
    draw.cls(state, 0x07E0)
    first = state.active_page.overlay
    draw.fill(state, 5, 5, 10, 10, 0xF800)
    second = state.active_page.overlay
    assert first is second  # same object reused


# --- cls ----------------------------------------------------------------------

def test_cls_fills_whole_page_and_marks_dirty():
    state = make_state(60, 40)
    state.dirty = False
    color = 0x07E0  # green
    draw.cls(state, color)
    overlay = state.active_page.overlay
    expected = (*RGB565(color).to_rgb888(), 255)
    # Sample corners + middle
    for px in [(0, 0), (59, 0), (0, 39), (59, 39), (30, 20)]:
        assert overlay.getpixel(px) == expected
    assert state.dirty is True


# --- fill ---------------------------------------------------------------------

def test_fill_paints_only_target_rect():
    state = make_state(60, 40)
    color = 0xF800  # red
    draw.fill(state, 10, 5, 20, 15, color)
    overlay = state.active_page.overlay
    expected = (*RGB565(color).to_rgb888(), 255)
    # inside the rect
    assert overlay.getpixel((10, 5)) == expected
    assert overlay.getpixel((29, 19)) == expected
    assert overlay.getpixel((20, 12)) == expected
    # outside the rect — still transparent
    assert overlay.getpixel((0, 0)) == (0, 0, 0, 0)
    assert overlay.getpixel((40, 30)) == (0, 0, 0, 0)


# --- cle ----------------------------------------------------------------------

def test_cle_clears_region_to_transparent():
    state = make_state(60, 40)
    draw.cls(state, 0xF800)  # paint everything red
    draw.cle(state, 10, 5, 20, 15)  # clear a window
    overlay = state.active_page.overlay
    # Inside the cleared window
    assert overlay.getpixel((10, 5)) == (0, 0, 0, 0)
    assert overlay.getpixel((29, 19)) == (0, 0, 0, 0)
    # Outside still red
    red = (*RGB565(0xF800).to_rgb888(), 255)
    assert overlay.getpixel((0, 0)) == red
    assert overlay.getpixel((40, 30)) == red


# --- cir ----------------------------------------------------------------------

def test_cir_draws_outline_only():
    state = make_state(60, 60)
    color = 0x001F  # blue
    draw.cir(state, 30, 30, 10, color)
    overlay = state.active_page.overlay
    expected = (*RGB565(color).to_rgb888(), 255)
    # Some pixel on the perimeter should be set
    perimeter_set = any(
        overlay.getpixel((30 + 10, y))[3] != 0 or
        overlay.getpixel((30 - 10, y))[3] != 0
        for y in range(25, 36)
    )
    assert perimeter_set
    # Centre should remain transparent (outline only)
    assert overlay.getpixel((30, 30)) == (0, 0, 0, 0)


# --- cirs ---------------------------------------------------------------------

def test_cirs_fills_circle():
    state = make_state(60, 60)
    color = 0x07E0  # green
    draw.cirs(state, 30, 30, 10, color)
    overlay = state.active_page.overlay
    expected = (*RGB565(color).to_rgb888(), 255)
    # Centre + a few interior pixels are filled
    assert overlay.getpixel((30, 30)) == expected
    assert overlay.getpixel((28, 31)) == expected
    # Far corners stay transparent
    assert overlay.getpixel((0, 0)) == (0, 0, 0, 0)
    assert overlay.getpixel((59, 59)) == (0, 0, 0, 0)


# --- line ---------------------------------------------------------------------

def test_line_marks_dirty_and_paints_endpoints():
    state = make_state(60, 60)
    state.dirty = False
    color = 0xFFFF
    draw.line(state, 0, 0, 30, 30, color)
    overlay = state.active_page.overlay
    expected = (*RGB565(color).to_rgb888(), 255)
    assert overlay.getpixel((0, 0)) == expected
    assert overlay.getpixel((30, 30)) == expected
    assert state.dirty is True


# --- xstr ---------------------------------------------------------------------

def test_xstr_draws_visible_text_with_solid_bg():
    state = make_state(120, 60)
    pco = 0xFFFF  # white text
    bco = 0xF800  # red background
    draw.xstr(state, 10, 10, 100, 30, font_id=0, pco=pco, bco=bco,
              xcen=1, ycen=1, sta=1, text="HI")
    overlay = state.active_page.overlay
    # Background rect exterior should be red
    red = (*RGB565(bco).to_rgb888(), 255)
    assert overlay.getpixel((11, 11)) == red
    # Some pixel near centre should be non-transparent (either bg or glyph)
    assert overlay.getpixel((60, 25))[3] == 255
    # Outside the rect still transparent
    assert overlay.getpixel((0, 0)) == (0, 0, 0, 0)


def test_xstr_crop_mode_skips_bg_fill():
    state = make_state(120, 60)
    # sta=0 → no bg fill; only glyphs paint
    draw.xstr(state, 10, 10, 100, 30, font_id=0, pco=0xFFFF, bco=0xF800,
              xcen=1, ycen=1, sta=0, text="HI")
    overlay = state.active_page.overlay
    # Corner of rect should NOT be red — bg suppressed
    assert overlay.getpixel((11, 11)) != (*RGB565(0xF800).to_rgb888(), 255)
