import pytest
from sim.parser import (
    parse,
    Mutation,
    PageSwitch,
    GlobalSet,
    Refresh,
    ClearScreen,
    Print,
    PrintH,
    Unsupported,
    IntLiteral,
    StrLiteral,
    AttrRef,
)


def test_int_attribute_set():
    op = parse(b"x0.val=12345")
    assert op == Mutation("x0", "val", IntLiteral(12345))


def test_negative_int():
    op = parse(b"x0.val=-7")
    assert op == Mutation("x0", "val", IntLiteral(-7))


def test_string_attribute_set():
    op = parse(b's0.txt="MAP Error"')
    assert op == Mutation("s0", "txt", StrLiteral("MAP Error"))


def test_string_with_escaped_quote():
    op = parse(b's0.txt="he said \\"hi\\""')
    assert op == Mutation("s0", "txt", StrLiteral('he said "hi"'))


def test_attribute_reference_value():
    op = parse(b"s0.bco=red.val")
    assert op == Mutation("s0", "bco", AttrRef("red", "val"))


def test_page_switch_by_id():
    assert parse(b"page 1") == PageSwitch(1)


def test_page_switch_by_name():
    assert parse(b"page settings") == PageSwitch("settings")


def test_dim_global():
    assert parse(b"dim=80") == GlobalSet("dim", 80)


def test_baud_global_acknowledged():
    assert parse(b"baud=115200") == GlobalSet("baud", 115200)


def test_refresh():
    assert parse(b"ref t0") == Refresh("t0")


def test_cls_with_color():
    assert parse(b"cls 0") == ClearScreen(0)


def test_print_string():
    assert parse(b'print "hi"') == Print("hi")


def test_printh_bytes():
    assert parse(b"printh 00 ff 7f") == PrintH(b"\x00\xff\x7f")


def test_unrecognised_returns_unsupported():
    op = parse(b"sys0=x7.val-x4.val")
    assert isinstance(op, Unsupported)
    assert "expression" in op.reason or "parse" in op.reason


def test_empty_frame_is_unsupported():
    op = parse(b"")
    assert isinstance(op, Unsupported)
