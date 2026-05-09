from sim.loader import load_hmi
from sim.parser import parse
from sim.exec import execute


def test_set_xfloat_val(hmi_path):
    state = load_hmi(hmi_path)
    main = state.pages["main"]
    assert main.by_name("x0").attrs["val"] == 123456
    execute(state, parse(b"x0.val=42"))
    assert main.by_name("x0").attrs["val"] == 42
    assert state.dirty


def test_set_text_string(hmi_path):
    state = load_hmi(hmi_path)
    state.dirty = False
    execute(state, parse(b's0.txt="MAP Error"'))
    assert state.pages["main"].by_name("s0").attrs["txt"] == "MAP Error"
    assert state.dirty


def test_set_attr_via_reference(hmi_path):
    state = load_hmi(hmi_path)
    red_val = state.pages["main"].by_name("red").attrs["val"]
    execute(state, parse(b"s0.bco=red.val"))
    assert state.pages["main"].by_name("s0").attrs["bco"] == red_val


def test_page_switch_by_name(hmi_path):
    state = load_hmi(hmi_path)
    execute(state, parse(b"page settings"))
    assert state.active_page.name == "settings"


def test_page_switch_by_id(hmi_path):
    state = load_hmi(hmi_path)
    target_id = state.pages["settings"].id
    execute(state, parse(f"page {target_id}".encode()))
    assert state.active_page.name == "settings"


def test_dim_writes_state(hmi_path):
    state = load_hmi(hmi_path)
    execute(state, parse(b"dim=50"))
    assert state.dim == 50
    assert state.dirty


def test_unknown_component_logs_no_crash(hmi_path, caplog):
    state = load_hmi(hmi_path)
    execute(state, parse(b"qqq.val=1"))
    # No crash; warning logged
    assert any("qqq" in r.message for r in caplog.records)


def test_unsupported_op_logs(hmi_path, caplog):
    state = load_hmi(hmi_path)
    execute(state, parse(b"sys0=x7.val-x4.val"))
    assert any("expression" in r.message or "Unsupported" in r.message for r in caplog.records)
