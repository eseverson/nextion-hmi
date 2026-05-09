from sim.state import (
    DisplayState, Page, Component, ScriptContext,
)


def _state():
    red = Component(name="red", id=25, type=52, attrs={"val": 64170, "vscope": 1})
    x0 = Component(name="x0", id=1, type=59, attrs={"val": 100, "bco": 10566})
    main = Page(name="main", id=0, attrs={"w": 480, "h": 320}, components=[red, x0])
    settings = Page(name="settings", id=1, attrs={"w": 480, "h": 320}, components=[])
    return DisplayState(pages={"main": main, "settings": settings})


def test_locals_isolated_per_context():
    s = _state()
    a = ScriptContext(s)
    b = ScriptContext(s)
    a.declare_local("x", 1)
    b.declare_local("x", 99)
    assert a.read_name("x") == 1
    assert b.read_name("x") == 99


def test_sys_vars_global_via_state():
    s = _state()
    a = ScriptContext(s)
    a.write_name("sys0", 7)
    b = ScriptContext(s)
    assert b.read_name("sys0") == 7
    assert s.sys[0] == 7


def test_dp_returns_active_page_id_and_is_readonly():
    s = _state()
    ctx = ScriptContext(s)
    assert ctx.read_name("dp") == 0
    ctx.write_name("dp", 999)  # silently ignored
    assert s.active_page.id == 0


def test_write_attr_marks_state_dirty():
    s = _state()
    s.dirty = False
    ctx = ScriptContext(s)
    ctx.write_attr("x0", "bco", 12345)
    assert s.pages["main"].by_name("x0").attrs["bco"] == 12345
    assert s.dirty


def test_bare_component_name_reads_val():
    s = _state()
    ctx = ScriptContext(s)
    assert ctx.read_name("red") == 64170


def test_set_active_clears_previous_overlay():
    s = _state()
    main = s.pages["main"]
    main.overlay = "fake-img"
    s.set_active(s.pages["settings"])
    assert main.overlay is None
