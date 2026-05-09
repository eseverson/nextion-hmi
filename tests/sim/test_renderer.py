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


def test_renderer_uses_zi_glyphs_when_available(hmi_path):
    """The 'RPM' label on the main page should be rendered with the project's
    actual ZI font (font 0). We assert by inspecting the t1 component's box
    and checking that ink pixels appear in the component's pco color, sized
    to the ZI font's height (24 px) — not the heuristic Liberation Mono
    point size (~70% of comp height ~21pt rasterised)."""
    state = load_hmi(hmi_path)
    assert 0 in state.fonts, "ZI font 0 must be loaded for this assertion"
    img = Renderer().render(state)
    main = state.pages["main"]
    t1 = main.by_name("t1")
    assert t1.attrs.get("txt") == "RPM"
    # The t1 component box; gather pixels in pco colour inside it.
    bx, by = t1.attrs["x"], t1.attrs["y"]
    bw, bh = t1.attrs["w"], t1.attrs["h"]
    pco = t1.attrs.get("pco")
    assert pco is not None
    # Convert the RGB565 pco to RGB888 (matches renderer's mapping).
    r = ((pco >> 11) & 0x1F) * 255 // 31
    g = ((pco >> 5) & 0x3F) * 255 // 63
    b = (pco & 0x1F) * 255 // 31
    target = (r, g, b)
    found_target = 0
    for x in range(bx, bx + bw):
        for y in range(by, by + bh):
            if img.getpixel((x, y)) == target:
                found_target += 1
    # ZI rendering pastes solid pco wherever the alpha mask is fully opaque,
    # so we expect a substantial number of exactly-matching pixels.
    assert found_target > 50, f"expected real glyph ink in t1 box, found {found_target}"
    # And the ink should be vertically constrained to roughly the font height
    # (24 px), not spread across the full 30 px box.
    inked_rows = set()
    for x in range(bx, bx + bw):
        for y in range(by, by + bh):
            if img.getpixel((x, y)) == target:
                inked_rows.add(y)
    if inked_rows:
        span = max(inked_rows) - min(inked_rows) + 1
        assert span <= state.fonts[0].height, (
            f"ink spans {span}px, expected <= ZI font height {state.fonts[0].height}px"
        )


def test_renderer_falls_back_to_ttf_when_font_missing(hmi_path):
    """If a component references a font_id we don't have, the renderer must
    not crash — it falls back to the TTF substitute path."""
    state = load_hmi(hmi_path)
    # Force the t1 label to use a non-existent font id.
    main = state.pages["main"]
    t1 = main.by_name("t1")
    t1.attrs["font"] = 99
    # And also clear the fonts dict to simulate "ZI fonts unavailable".
    state.fonts = {}
    img = Renderer().render(state)
    main_attrs = state.pages["main"].attrs
    assert img.size == (main_attrs["w"], main_attrs["h"])
