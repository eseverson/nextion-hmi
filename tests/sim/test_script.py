import pytest
from sim.state import DisplayState, Page, Component, ScriptContext
from sim.script import (
    parse_script, run, register_proc,
    IntDecl, Assign, If, While, For, Call,
)


def _fresh_state():
    red = Component(name="red", id=25, type=52, attrs={"val": 64170, "vscope": 1})
    bco = Component(name="bco", id=26, type=52, attrs={"val": 10566, "vscope": 1})
    x0 = Component(name="x0", id=1, type=59, attrs={"val": 0, "bco": 10566})
    x1 = Component(name="x1", id=2, type=59, attrs={"val": 0, "bco": 10566})
    main = Page(name="main", id=0, attrs={"w": 480, "h": 320},
                components=[red, bco, x0, x1])
    return DisplayState(pages={"main": main})


def test_parse_simple_assign():
    stmts = parse_script("x0.val=42")
    assert len(stmts) == 1
    assert isinstance(stmts[0], Assign)
    assert stmts[0].target == "x0.val"


def test_parse_int_decl_multi():
    stmts = parse_script("int sys0=0,sys1=1,sys2=2")
    assert len(stmts) == 1
    assert isinstance(stmts[0], IntDecl)
    assert len(stmts[0].decls) == 3


def test_parse_if_else_chain():
    src = """
    if(x0.val>2000)
    {
        x0.bco=red.val
    }else if(x0.val>1700)
    {
        x0.bco=red.val
    }else
    {
        x0.bco=bco.val
    }
    """
    stmts = parse_script(src)
    assert len(stmts) == 1
    assert isinstance(stmts[0], If)
    assert len(stmts[0].elifs) == 1
    assert len(stmts[0].else_block) == 1


def test_parse_while():
    stmts = parse_script("while(sys0<10){\nsys0=sys0+1\n}")
    assert isinstance(stmts[0], While)


def test_parse_for():
    stmts = parse_script("for(int i=0;i<3;i=i+1)\n{\nsys0=sys0+1\n}")
    assert isinstance(stmts[0], For)
    assert isinstance(stmts[0].init, IntDecl)


def test_parse_call():
    stmts = parse_script("page 1")
    assert isinstance(stmts[0], Call)
    assert stmts[0].name == "page"
    assert stmts[0].args == "1"


def test_parse_strips_line_comments():
    stmts = parse_script("// hello\nx0.val=1 // trailing\n")
    assert len(stmts) == 1
    assert isinstance(stmts[0], Assign)


def test_run_simple_assign():
    s = _fresh_state()
    ctx = ScriptContext(s)
    run("x0.val=42", ctx)
    assert s.pages["main"].by_name("x0").attrs["val"] == 42


def test_run_attr_via_attr_reference():
    s = _fresh_state()
    ctx = ScriptContext(s)
    run("x0.bco=red.val", ctx)
    assert s.pages["main"].by_name("x0").attrs["bco"] == 64170


def test_run_if_else_chain():
    s = _fresh_state()
    s.pages["main"].by_name("x0").attrs["val"] = 1500
    ctx = ScriptContext(s)
    run("""
    if(x0.val>2000)
    {
        x0.bco=red.val
    }else if(x0.val>1000)
    {
        x0.bco=red.val
    }else
    {
        x0.bco=bco.val
    }
    """, ctx)
    assert s.pages["main"].by_name("x0").attrs["bco"] == 64170  # red branch hit


def test_run_locals_and_arithmetic():
    s = _fresh_state()
    s.pages["main"].by_name("x0").attrs["val"] = 145
    s.pages["main"].by_name("x1").attrs["val"] = 100
    ctx = ScriptContext(s)
    run("sys0=x0.val-x1.val", ctx)
    assert s.sys[0] == 45


def test_run_logical_or():
    s = _fresh_state()
    ctx = ScriptContext(s)
    s.sys[0] = -25
    run("""
    if(sys0>20||sys0<-20)
    {
        x0.bco=red.val
    }
    """, ctx)
    assert s.pages["main"].by_name("x0").attrs["bco"] == 64170


def test_run_while_loop_increments_local():
    s = _fresh_state()
    ctx = ScriptContext(s)
    run("int i=0\nwhile(i<5){\ni=i+1\n}", ctx)
    assert ctx.read_name("i") == 5


def test_run_for_loop_sums():
    s = _fresh_state()
    ctx = ScriptContext(s)
    run("int total=0\nfor(int i=1;i<=4;i=i+1){\ntotal=total+i\n}", ctx)
    assert ctx.read_name("total") == 10


def test_unknown_proc_does_not_crash():
    s = _fresh_state()
    ctx = ScriptContext(s)
    run("nonsense 1,2,3", ctx)


def test_proc_dispatch_calls_registered_handler():
    captured = {}

    def handler(ctx, args):
        captured["args"] = args

    register_proc("recordme", handler)
    s = _fresh_state()
    ctx = ScriptContext(s)
    run("recordme hello,world", ctx)
    assert captured["args"] == "hello,world"


def test_full_main_timer_event_runs_without_error():
    """The actual Timer event from main.HMI's source. End-to-end smoke."""
    src = """
    if(x0.val>2000)
    {
        x0.bco=red.val
    }else if(x0.val>1700)
    {
        x0.bco=red.val
    }else
    {
        x0.bco=bco.val
    }
    sys0=x0.val-x1.val
    if(sys0>20||sys0<-20)
    {
        x0.bco=red.val
        x1.bco=red.val
    }
    """
    s = _fresh_state()
    s.pages["main"].by_name("x0").attrs["val"] = 2200
    s.pages["main"].by_name("x1").attrs["val"] = 100
    ctx = ScriptContext(s)
    run(src, ctx)
    assert s.pages["main"].by_name("x0").attrs["bco"] == 64170
    assert s.pages["main"].by_name("x1").attrs["bco"] == 64170
