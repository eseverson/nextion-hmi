import pytest

from sim.expr import tokenize, parse, evaluate


class FakeCtx:
    def __init__(self, names=None, attrs=None):
        self.names = names or {}
        self.attrs = attrs or {}

    def read_name(self, n):
        return self.names.get(n)

    def read_attr(self, o, a):
        return self.attrs.get((o, a))


def ev(text, **kw):
    return evaluate(parse(text), FakeCtx(**kw))


def test_int_literal():
    assert ev("42") == 42


def test_negative():
    assert ev("-7") == -7


def test_string_literal():
    assert ev('"hi"') == "hi"


def test_string_concat():
    assert ev('"a" + "b"') == "ab"


def test_arithmetic_precedence():
    assert ev("1 + 2 * 3") == 7
    assert ev("(1 + 2) * 3") == 9


def test_int_division():
    assert ev("7 / 2") == 3
    assert ev("-7 / 2") == -4  # floor division per Python int semantics


def test_modulo():
    assert ev("10 % 3") == 1


def test_div_by_zero_safe():
    assert ev("5 / 0") == 0
    assert ev("5 % 0") == 0


def test_comparison_returns_0_or_1():
    assert ev("3 < 4") == 1
    assert ev("3 > 4") == 0
    assert ev("3 == 3") == 1
    assert ev("3 != 3") == 0


def test_logical_and_or_short_circuit():
    assert ev("1 && 0") == 0
    assert ev("0 || 1") == 1
    # short-circuit: right side wouldn't be evaluable but left fails fast
    assert ev("0 && (1/0)") == 0


def test_not_operator():
    assert ev("!0") == 1
    assert ev("!5") == 0


def test_attribute_read():
    assert ev("x0.val", attrs={("x0", "val"): 42}) == 42


def test_name_read():
    assert ev("sys0", names={"sys0": 99}) == 99


def test_name_unset_resolves_to_zero():
    assert ev("undef") == 0
    assert ev("undef.attr") == 0


def test_complex_expression_from_main_timer():
    # if (sys0>20||sys0<-20) — the right side of || tested for sys0=-25
    ctx = FakeCtx(names={"sys0": -25})
    assert evaluate(parse("sys0>20||sys0<-20"), ctx) == 1
    ctx = FakeCtx(names={"sys0": 5})
    assert evaluate(parse("sys0>20||sys0<-20"), ctx) == 0


def test_nested_parens():
    assert ev("((1 + 2) * (3 + 4))") == 21


def test_tokenize_recognises_two_char_ops():
    kinds = [t.kind for t in tokenize("a <= b && c != d || !e")]
    assert "LE" in kinds and "AND" in kinds and "NE" in kinds and "OR" in kinds and "NOT" in kinds
