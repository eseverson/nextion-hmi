"""Procedure-call dispatch table shared by App and HeadlessApp.

Nextion event scripts call into procedural ops like `page 1`, `vis x0,1`,
`fill 0,0,480,320,0`. This module registers a single canonical set of
handlers against the global `sim.script._PROCS` registry. Each handler
closes over a `host` object exposing:

  - `host.state`        — the live DisplayState
  - `host._switch_page(page)` — page-switch with codesload/unload events

That's the minimum surface; the host can be either an App (with Tk) or a
HeadlessApp (with no Tk). Drawing primitives go through the per-page
overlay in `sim.draw`.
"""
from __future__ import annotations
import logging

from sim import draw as sim_draw
from sim import script as sim_script
from sim.expr import parse as parse_expr, evaluate as eval_expr
from sim.script import _split_top_level

log = logging.getLogger("sim.procs")


def _ev_args(ctx, args_str: str) -> list:
    s = args_str.strip()
    if not s:
        return []
    return [eval_expr(parse_expr(p.strip()), ctx)
            for p in _split_top_level(s, ",")]


def register_all(host) -> None:
    """Register the standard procedure set against `host`."""
    state = host.state

    def page_proc(ctx, args: str) -> None:
        target = args.strip()
        try:
            p = state.pages_by_id.get(int(target))
        except ValueError:
            p = state.pages.get(target)
        if p is not None:
            host._switch_page(p)

    def vis_proc(ctx, args: str) -> None:
        parts = _split_top_level(args, ",")
        if len(parts) != 2:
            return
        c = state.active_page.by_name(parts[0].strip())
        if c is None:
            return
        c.set("vis", int(eval_expr(parse_expr(parts[1].strip()), ctx)))
        state.dirty = True

    def tsw_proc(ctx, args: str) -> None:
        parts = _split_top_level(args, ",")
        if len(parts) != 2:
            return
        c = state.active_page.by_name(parts[0].strip())
        if c is None:
            return
        c.set("tsw", int(eval_expr(parse_expr(parts[1].strip()), ctx)))

    def cls_proc(ctx, args: str) -> None:
        v = _ev_args(ctx, args)
        if v:
            sim_draw.cls(state, int(v[0]))

    def fill_proc(ctx, args: str) -> None:
        v = _ev_args(ctx, args)
        if len(v) >= 5:
            sim_draw.fill(state, int(v[0]), int(v[1]), int(v[2]), int(v[3]), int(v[4]))

    def line_proc(ctx, args: str) -> None:
        v = _ev_args(ctx, args)
        if len(v) >= 5:
            sim_draw.line(state, int(v[0]), int(v[1]), int(v[2]), int(v[3]), int(v[4]))

    def cir_proc(ctx, args: str) -> None:
        v = _ev_args(ctx, args)
        if len(v) >= 4:
            sim_draw.cir(state, int(v[0]), int(v[1]), int(v[2]), int(v[3]))

    def cirs_proc(ctx, args: str) -> None:
        v = _ev_args(ctx, args)
        if len(v) >= 4:
            sim_draw.cirs(state, int(v[0]), int(v[1]), int(v[2]), int(v[3]))

    def cle_proc(ctx, args: str) -> None:
        v = _ev_args(ctx, args)
        if len(v) >= 4:
            sim_draw.cle(state, int(v[0]), int(v[1]), int(v[2]), int(v[3]))

    def xstr_proc(ctx, args: str) -> None:
        pieces = _split_top_level(args, ",")
        if len(pieces) < 11:
            return
        ints = [int(eval_expr(parse_expr(p.strip()), ctx)) for p in pieces[:10]]
        text_val = eval_expr(parse_expr(pieces[10].strip()), ctx)
        sim_draw.xstr(state, *ints, str(text_val))

    def print_proc(ctx, args: str) -> None:
        s = args.strip()
        try:
            v = eval_expr(parse_expr(s), ctx)
            log.info("print: %s", v)
        except Exception:
            log.info("print: %s", s)

    def printh_proc(ctx, args: str) -> None:
        try:
            payload = bytes(int(p, 16) for p in args.split())
            log.info("printh: %s", payload.hex())
        except ValueError:
            log.info("printh: (invalid) %s", args)

    sim_script.register_proc("page", page_proc)
    sim_script.register_proc("ref", lambda ctx, a: None)
    sim_script.register_proc("vis", vis_proc)
    sim_script.register_proc("tsw", tsw_proc)
    sim_script.register_proc("cls", cls_proc)
    sim_script.register_proc("fill", fill_proc)
    sim_script.register_proc("line", line_proc)
    sim_script.register_proc("cir", cir_proc)
    sim_script.register_proc("cirs", cirs_proc)
    sim_script.register_proc("cle", cle_proc)
    sim_script.register_proc("xstr", xstr_proc)
    sim_script.register_proc("print", print_proc)
    sim_script.register_proc("printh", printh_proc)
    sim_script.register_proc("sendme", lambda ctx, a: None)
    sim_script.register_proc("get", lambda ctx, a: None)
