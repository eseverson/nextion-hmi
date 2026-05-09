from sim import draw as sim_draw
from sim.loader import load_hmi
from sim.renderer import Renderer


def test_renderer_produces_image_at_page_size(hmi_path):
    state = load_hmi(hmi_path)
    img = Renderer().render(state)
    main = state.pages["main"]
    assert img.size == (main.attrs["w"], main.attrs["h"])


def test_renderer_respects_dim(hmi_path):
    state = load_hmi(hmi_path)
    img_full = Renderer().render(state)
    state.dim = 20
    img_dim = Renderer().render(state)
    avg_full = sum(sum(p) for p in img_full.getdata()) / (img_full.size[0] * img_full.size[1] * 3)
    avg_dim = sum(sum(p) for p in img_dim.getdata()) / (img_dim.size[0] * img_dim.size[1] * 3)
    assert avg_dim < avg_full * 0.6


def test_renderer_skips_components_with_vis_zero(hmi_path):
    state = load_hmi(hmi_path)
    main = state.pages["main"]
    base = Renderer().render(state)
    # Hide one of the XFloat readouts and re-render
    main.by_name("x0").set("vis", 0)
    state.dirty = True
    after = Renderer().render(state)
    assert base.size == after.size
    # The renders should differ — x0's region is now page-bg-coloured.
    assert list(base.getdata()) != list(after.getdata())


def test_renderer_composites_overlay(hmi_path):
    """A red rect drawn into the page overlay must show up in the final render."""
    state = load_hmi(hmi_path)
    # Render once with no overlay, then draw a red rect, then render again.
    base = Renderer().render(state)
    # Pure red in RGB565 is 0xF800
    sim_draw.fill(state, 100, 50, 80, 40, 0xF800)
    after = Renderer().render(state)
    # Sample the centre of the rect — should be ~ (255, 0, 0).
    px = after.getpixel((140, 70))
    assert px[0] > 200 and px[1] < 60 and px[2] < 60, f"expected red, got {px}"
    # And outside the rect, the pixel should match the base render.
    assert base.getpixel((10, 10)) == after.getpixel((10, 10))
