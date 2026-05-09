from sim.loader import load_hmi


def test_loader_returns_display_state(hmi_path):
    state = load_hmi(hmi_path)
    assert "main" in state.pages
    assert "settings" in state.pages
    assert state.active_page.name in state.pages

    main = state.pages["main"]
    assert main.attrs["w"] == 480
    assert main.attrs["h"] == 320
    assert main.by_name("x0") is not None
    assert main.by_name("t1").attrs["txt"] == "RPM"
    assert main.by_name("j0").attrs["val"] == 50


def test_loader_indexes_global_color_vars(hmi_path):
    state = load_hmi(hmi_path)
    main = state.pages["main"]
    assert main.by_name("red").attrs["val"] == 64170


def test_loader_captures_event_scripts(hmi_path):
    state = load_hmi(hmi_path)
    # The error page's m0 hotspot has Touch Press Event: page 0
    error = state.pages["error"]
    m0 = error.by_name("m0")
    assert m0 is not None
    code = m0.events.get("codesdown", "").strip()
    assert code.startswith("page ")
