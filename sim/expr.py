"""Nextion expression tokenizer + parser + evaluator.

Supports the Nextion-flavoured expression grammar exercised by event-handler
scripts: int / string literals, dotted attribute reads (`x0.val`),
arithmetic (`+ - * / %`, integer division), comparison (`< > <= >= == !=`),
logical (`&& || !`), parens.

Returns int or str. Comparison and logical operators yield 0 or 1 (Nextion
has no bool type). Strings only support `+` (concat) and equality
comparisons.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Union

# ---------- Tokens ----------

TOKEN_KINDS = (
    "INT", "STRING", "IDENT",
    "PLUS", "MINUS", "STAR", "SLASH", "PERCENT",
    "LT", "GT", "LE", "GE", "EQ", "NE",
    "AND", "OR", "NOT",
    "DOT", "COMMA", "LPAREN", "RPAREN",
    "EOF",
)


@dataclass
class Token:
    kind: str
    value: object


def tokenize(text: str) -> list[Token]:
    out: list[Token] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == '"':
            buf = []
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    buf.append(text[i + 1])
                    i += 2
                    continue
                buf.append(text[i])
                i += 1
            if i >= n:
                raise ValueError("unterminated string literal")
            i += 1
            out.append(Token("STRING", "".join(buf)))
            continue
        if c.isdigit():
            j = i
            while j < n and text[j].isdigit():
                j += 1
            out.append(Token("INT", int(text[i:j])))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            out.append(Token("IDENT", text[i:j]))
            i = j
            continue
        # Two-character operators
        two = text[i:i + 2]
        if two == "<=":
            out.append(Token("LE", "<="))
            i += 2
            continue
        if two == ">=":
            out.append(Token("GE", ">="))
            i += 2
            continue
        if two == "==":
            out.append(Token("EQ", "=="))
            i += 2
            continue
        if two == "!=":
            out.append(Token("NE", "!="))
            i += 2
            continue
        if two == "&&":
            out.append(Token("AND", "&&"))
            i += 2
            continue
        if two == "||":
            out.append(Token("OR", "||"))
            i += 2
            continue
        # Single-char
        single_map = {
            "+": "PLUS", "-": "MINUS", "*": "STAR", "/": "SLASH", "%": "PERCENT",
            "<": "LT", ">": "GT", "!": "NOT",
            ".": "DOT", ",": "COMMA", "(": "LPAREN", ")": "RPAREN",
        }
        if c in single_map:
            out.append(Token(single_map[c], c))
            i += 1
            continue
        raise ValueError(f"unexpected character {c!r} at {i} in {text!r}")
    out.append(Token("EOF", None))
    return out


# ---------- AST ----------

@dataclass(frozen=True)
class Lit:
    value: object  # int or str


@dataclass(frozen=True)
class Name:
    name: str  # bare identifier (local var or sys/dp/etc.)


@dataclass(frozen=True)
class Attr:
    obj: str
    attr: str


@dataclass(frozen=True)
class Unary:
    op: str        # "-" or "!"
    operand: "Expr"


@dataclass(frozen=True)
class Binary:
    op: str
    left: "Expr"
    right: "Expr"


Expr = Union[Lit, Name, Attr, Unary, Binary]


# ---------- Parser ----------

class _Parser:
    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.i = 0

    def _peek(self) -> Token:
        return self.toks[self.i]

    def _advance(self) -> Token:
        t = self.toks[self.i]
        self.i += 1
        return t

    def _eat(self, kind: str) -> Token:
        t = self._advance()
        if t.kind != kind:
            raise ValueError(f"expected {kind} got {t.kind} ({t.value!r})")
        return t

    # Grammar (low to high precedence):
    # expr     := orexpr
    # orexpr   := andexpr ('||' andexpr)*
    # andexpr  := cmp ('&&' cmp)*
    # cmp      := add (('<' | '<=' | '>' | '>=' | '==' | '!=') add)?
    # add      := mul (('+' | '-') mul)*
    # mul      := unary (('*' | '/' | '%') unary)*
    # unary    := ('-' | '!')? primary
    # primary  := INT | STRING | name | '(' expr ')'
    # name     := IDENT ('.' IDENT)?

    def parse(self) -> Expr:
        e = self._or()
        if self._peek().kind != "EOF":
            raise ValueError(f"trailing tokens at {self._peek()}")
        return e

    def _or(self) -> Expr:
        left = self._and()
        while self._peek().kind == "OR":
            self._advance()
            right = self._and()
            left = Binary("||", left, right)
        return left

    def _and(self) -> Expr:
        left = self._cmp()
        while self._peek().kind == "AND":
            self._advance()
            right = self._cmp()
            left = Binary("&&", left, right)
        return left

    def _cmp(self) -> Expr:
        left = self._add()
        if self._peek().kind in ("LT", "LE", "GT", "GE", "EQ", "NE"):
            op = self._advance().value
            right = self._add()
            return Binary(op, left, right)
        return left

    def _add(self) -> Expr:
        left = self._mul()
        while self._peek().kind in ("PLUS", "MINUS"):
            op = self._advance().value
            right = self._mul()
            left = Binary(op, left, right)
        return left

    def _mul(self) -> Expr:
        left = self._unary()
        while self._peek().kind in ("STAR", "SLASH", "PERCENT"):
            op = self._advance().value
            right = self._unary()
            left = Binary(op, left, right)
        return left

    def _unary(self) -> Expr:
        if self._peek().kind == "MINUS":
            self._advance()
            return Unary("-", self._unary())
        if self._peek().kind == "NOT":
            self._advance()
            return Unary("!", self._unary())
        return self._primary()

    def _primary(self) -> Expr:
        t = self._peek()
        if t.kind == "INT":
            self._advance()
            return Lit(t.value)
        if t.kind == "STRING":
            self._advance()
            return Lit(t.value)
        if t.kind == "LPAREN":
            self._advance()
            e = self._or()
            self._eat("RPAREN")
            return e
        if t.kind == "IDENT":
            self._advance()
            if self._peek().kind == "DOT":
                self._advance()
                attr_tok = self._eat("IDENT")
                return Attr(t.value, attr_tok.value)
            return Name(t.value)
        raise ValueError(f"unexpected token {t}")


def parse(text: str) -> Expr:
    return _Parser(tokenize(text)).parse()


# ---------- Evaluator ----------

def _truthy(v) -> bool:
    if isinstance(v, str):
        return len(v) > 0
    return bool(v)


def _bin(op: str, a, b):
    # Numeric promotion: ints stay ints; strings only support + and ==/!=.
    if op == "+":
        if isinstance(a, str) or isinstance(b, str):
            return str(a) + str(b)
        return int(a) + int(b)
    if op == "-":
        return int(a) - int(b)
    if op == "*":
        return int(a) * int(b)
    if op == "/":
        if int(b) == 0:
            return 0
        return int(a) // int(b)
    if op == "%":
        if int(b) == 0:
            return 0
        return int(a) % int(b)
    if op == "<":
        return 1 if a < b else 0
    if op == "<=":
        return 1 if a <= b else 0
    if op == ">":
        return 1 if a > b else 0
    if op == ">=":
        return 1 if a >= b else 0
    if op == "==":
        return 1 if a == b else 0
    if op == "!=":
        return 1 if a != b else 0
    if op == "&&":
        return 1 if _truthy(a) and _truthy(b) else 0
    if op == "||":
        return 1 if _truthy(a) or _truthy(b) else 0
    raise ValueError(f"unknown op {op}")


def evaluate(node: Expr, ctx) -> object:
    """Evaluate `node` against a `ScriptContext`-like object exposing
    `read_name(str) -> object` and `read_attr(obj, attr) -> object`.
    """
    if isinstance(node, Lit):
        return node.value
    if isinstance(node, Name):
        v = ctx.read_name(node.name)
        return 0 if v is None else v
    if isinstance(node, Attr):
        v = ctx.read_attr(node.obj, node.attr)
        return 0 if v is None else v
    if isinstance(node, Unary):
        v = evaluate(node.operand, ctx)
        if node.op == "-":
            return -int(v)
        if node.op == "!":
            return 0 if _truthy(v) else 1
        raise ValueError(node.op)
    if isinstance(node, Binary):
        # Short-circuit && and ||
        if node.op == "&&":
            a = evaluate(node.left, ctx)
            if not _truthy(a):
                return 0
            return 1 if _truthy(evaluate(node.right, ctx)) else 0
        if node.op == "||":
            a = evaluate(node.left, ctx)
            if _truthy(a):
                return 1
            return 1 if _truthy(evaluate(node.right, ctx)) else 0
        a = evaluate(node.left, ctx)
        b = evaluate(node.right, ctx)
        return _bin(node.op, a, b)
    raise TypeError(f"unknown node {node!r}")
