from __future__ import annotations
import logging
from typing import Optional

from sim.state import DisplayState, ComponentRef, ScriptContext
from sim.parser import (
    Operation, Mutation, PageSwitch, GlobalSet, Refresh, ClearScreen,
    Print, PrintH, Unsupported, IntLiteral, StrLiteral, AttrRef, ExprValue,
)
from sim.expr import evaluate as eval_expr

log = logging.getLogger("sim.exec")


def _resolve_value(state: DisplayState, value):
    if isinstance(value, IntLiteral):
        return value.value
    if isinstance(value, StrLiteral):
        return value.value
    if isinstance(value, AttrRef):
        v = state.read_attr(value.obj, value.attr)
        if v is None:
            log.warning("unresolved reference %s.%s", value.obj, value.attr)
        return v
    if isinstance(value, ExprValue):
        # TCP-driven mutations have no script-local scope; build a fresh
        # ScriptContext so the expression can read sys vars / component
        # attrs the same way scripts do.
        try:
            return eval_expr(value.node, ScriptContext(state))
        except Exception:
            log.exception("failed to evaluate RHS expression")
            return None
    return None


def execute(state: DisplayState, op: Operation) -> None:
    if isinstance(op, Mutation):
        comp = state.resolve(ComponentRef(op.target))
        if comp is None:
            log.warning("unknown component '%s'", op.target)
            return
        v = _resolve_value(state, op.value)
        if v is None:
            return
        comp.set(op.attr, v)
        state.dirty = True
        return

    if isinstance(op, PageSwitch):
        if isinstance(op.target, int):
            page = state.pages_by_id.get(op.target)
        else:
            page = state.pages.get(op.target)
        if page is None:
            log.warning("unknown page '%s'", op.target)
            return
        state.set_active(page)
        return

    if isinstance(op, GlobalSet):
        if isinstance(op.value, (AttrRef, ExprValue)):
            v = _resolve_value(state, op.value)
            if v is None:
                return
            value = int(v)
        else:
            value = op.value
        if op.name in ("dim", "dims"):
            state.dim = max(0, min(100, value))
            state.dirty = True
        # baud / recmod / thup / usup: acknowledged, no-op
        return

    if isinstance(op, Refresh):
        # We always render live; nothing to do.
        return

    if isinstance(op, ClearScreen):
        state.active_page.attrs["bco"] = op.color
        for c in state.active_page.components:
            c.dirty = True
        state.dirty = True
        return

    if isinstance(op, Print):
        log.info("print: %s", op.text)
        return

    if isinstance(op, PrintH):
        log.info("printh: %s", op.payload.hex())
        return

    if isinstance(op, Unsupported):
        log.warning("Unsupported op: %r (%s)", op.text, op.reason)
        return

    log.warning("unhandled op type: %r", op)
