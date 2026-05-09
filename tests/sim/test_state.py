from sim.state import DisplayState, Page, Component, RGB565, ComponentRef


def test_rgb565_decodes_to_rgb888():
    c = RGB565(0x2946)
    assert c.to_rgb888() == (5 * 255 // 31, 10 * 255 // 63, 6 * 255 // 31)


def test_component_has_writeable_attrs():
    c = Component(name="x0", id=1, type=59, attrs={"val": 0, "bco": 10566})
    c.set("val", 42)
    assert c.attrs["val"] == 42
    assert c.dirty


def test_page_lookup_by_name_and_id():
    c0 = Component(name="x0", id=1, type=59, attrs={})
    c1 = Component(name="x1", id=2, type=59, attrs={})
    p = Page(name="main", id=0, attrs={"w": 480, "h": 320}, components=[c0, c1])
    assert p.by_name("x1") is c1
    assert p.by_id(1) is c0
    assert p.by_name("nope") is None


def test_display_state_active_page_starts_at_zero():
    main = Page(name="main", id=0, attrs={"w": 480, "h": 320}, components=[])
    settings = Page(name="settings", id=1, attrs={"w": 480, "h": 320}, components=[])
    state = DisplayState(pages={"main": main, "settings": settings})
    assert state.active_page is main


def test_display_state_resolves_global_variable():
    red = Component(name="red", id=25, type=52, attrs={"val": 64170, "vscope": 1})
    main = Page(name="main", id=0, attrs={"w": 480, "h": 320}, components=[red])
    state = DisplayState(pages={"main": main})
    assert state.resolve(ComponentRef("red")).attrs["val"] == 64170


def test_display_state_resolves_dotted_attribute():
    red = Component(name="red", id=25, type=52, attrs={"val": 64170, "vscope": 1})
    main = Page(name="main", id=0, attrs={"w": 480, "h": 320}, components=[red])
    state = DisplayState(pages={"main": main})
    assert state.read_attr("red", "val") == 64170
