"""HeadlessApp tests run the boot path and a few ticks without Tk."""
from __future__ import annotations
import os
import socket
import time

import pytest

from sim.headless import HeadlessApp
from sim.loader import load_hmi
from sim.transport import StdinTransport, TcpTransport


class _StubTransport:
    """In-memory transport stub for tests — frames drip via push()."""

    def __init__(self):
        self._frames = []
        self._sent = []

    def push(self, frame: bytes) -> None:
        self._frames.append(frame)

    def recv_frame(self):
        if self._frames:
            return self._frames.pop(0)
        return None

    def send_frame(self, payload: bytes) -> None:
        self._sent.append(payload)

    def close(self) -> None:
        pass


def test_headless_boots_and_renders(hmi_path, tmp_path):
    state = load_hmi(hmi_path)
    transport = _StubTransport()
    out = tmp_path / "live.png"
    app = HeadlessApp(state, transport, out_path=out, tick_ms=10)
    # Boot already triggered a redraw via constructor; do an explicit step.
    app.step()
    # First boot triggers _redraw via run() but we're calling step() directly,
    # so trigger via state.dirty. We can also just do app._redraw().
    app._redraw()
    assert out.exists()
    assert out.stat().st_size > 0


def test_headless_applies_command_and_re_renders(hmi_path, tmp_path):
    state = load_hmi(hmi_path)
    transport = _StubTransport()
    out = tmp_path / "live.png"
    app = HeadlessApp(state, transport, out_path=out)
    transport.push(b"x0.val=12345")
    app.step()
    assert state.pages["main"].by_name("x0").attrs["val"] == 12345


def test_headless_page_switch_fires_codesload(hmi_path, tmp_path):
    """Switching to settings should run its codesload (h0.val=dim)."""
    state = load_hmi(hmi_path)
    transport = _StubTransport()
    out = tmp_path / "live.png"
    app = HeadlessApp(state, transport, out_path=out)
    state.dim = 73
    transport.push(b"page settings")
    app.step()
    settings = state.pages["settings"]
    assert state.active_page is settings
    h0 = settings.by_name("h0")
    assert h0 is not None
    assert h0.attrs.get("val") == 73


def test_headless_timer_fires_on_step(hmi_path, tmp_path):
    state = load_hmi(hmi_path)
    transport = _StubTransport()
    out = tmp_path / "live.png"
    app = HeadlessApp(state, transport, out_path=out)
    main = state.pages["main"]
    main.by_name("x1").attrs["val"] = 7000
    # Force a tim window
    time.sleep(0.5)  # main page Timer has tim=400
    app.step()
    red_val = main.by_name("red").attrs["val"]
    assert main.by_name("x1").attrs["bco"] == red_val
