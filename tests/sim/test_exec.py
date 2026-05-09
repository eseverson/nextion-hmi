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
    # P1 accepts expression RHS; use truly malformed input.
    execute(state, parse(b"this is not a command"))
    assert any("Unsupported" in r.message for r in caplog.records)


def test_expr_rhs_arithmetic_with_attr_ref(hmi_path):
    state = load_hmi(hmi_path)
    red_val = state.pages["main"].by_name("red").attrs["val"]
    execute(state, parse(b"s0.bco=red.val+1"))
    assert state.pages["main"].by_name("s0").attrs["bco"] == red_val + 1
    assert state.dirty


def test_expr_rhs_for_global_dim(hmi_path):
    state = load_hmi(hmi_path)
    # h0 lives on the settings page; set its val first to a known value.
    h0 = state.pages["settings"].by_name("h0")
    h0.attrs["val"] = 42
    execute(state, parse(b"dim=h0.val"))
    assert state.dim == 42
    assert state.dirty


def test_expr_rhs_uses_sysvar(hmi_path):
    state = load_hmi(hmi_path)
    state.sys[0] = 10
    execute(state, parse(b"x0.val=sys0+5"))
    assert state.pages["main"].by_name("x0").attrs["val"] == 15


def test_expr_rhs_string_concat(hmi_path):
    state = load_hmi(hmi_path)
    execute(state, parse(b's0.txt="hello"+"world"'))
    assert state.pages["main"].by_name("s0").attrs["txt"] == "helloworld"
    assert state.dirty


def test_expr_rhs_malformed_returns_unsupported(hmi_path, caplog):
    state = load_hmi(hmi_path)
    op = parse(b"x0.val=1+")
    from sim.parser import Unsupported
    assert isinstance(op, Unsupported)
    execute(state, op)
    assert any("Unsupported" in r.message for r in caplog.records)
