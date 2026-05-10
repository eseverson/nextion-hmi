"""Orientation rendering — verify post-rotation works for 180°/90°/270°."""
from PIL import ImageChops

from sim.loader import load_hmi
from sim.renderer import Renderer


def test_orientation_0_is_default(hmi_path):
    state = load_hmi(hmi_path)
    state.orientation = 0
    img = Renderer().render(state)
    assert img.size == (state.active_page.attrs["w"], state.active_page.attrs["h"])


def test_orientation_180_flips_image(hmi_path):
    """A 180° rotation should flip pixels diagonally — the top-left pixel
    of the rotated image equals the bottom-right of the original."""
    state = load_hmi(hmi_path)
    state.orientation = 0
    base = Renderer().render(state)
    state.dirty = True
    state.orientation = 180
    flipped = Renderer().render(state)
    assert flipped.size == base.size  # 180° preserves dims
    w, h = base.size
    assert flipped.getpixel((0, 0)) == base.getpixel((w - 1, h - 1))
    assert flipped.getpixel((w - 1, h - 1)) == base.getpixel((0, 0))


def test_orientation_90_swaps_dims(hmi_path):
    state = load_hmi(hmi_path)
    state.orientation = 0
    base = Renderer().render(state)
    state.dirty = True
    state.orientation = 90
    rot = Renderer().render(state)
    bw, bh = base.size
    rw, rh = rot.size
    assert (rw, rh) == (bh, bw)  # 90° swaps width and height


def test_orientation_270_also_swaps(hmi_path):
    state = load_hmi(hmi_path)
    state.orientation = 270
    rot = Renderer().render(state)
    bw, bh = state.active_page.attrs["w"], state.active_page.attrs["h"]
    assert rot.size == (bh, bw)
