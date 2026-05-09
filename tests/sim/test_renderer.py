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
    # At 20% dim, average pixel intensity should be lower
    avg_full = sum(sum(p) for p in img_full.getdata()) / (img_full.size[0] * img_full.size[1] * 3)
    avg_dim = sum(sum(p) for p in img_dim.getdata()) / (img_dim.size[0] * img_dim.size[1] * 3)
    assert avg_dim < avg_full * 0.6
