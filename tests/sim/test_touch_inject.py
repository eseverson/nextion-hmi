"""TouchInject parsing + headless dispatch tests."""
from __future__ import annotations

import pytest

from sim.parser import parse, TouchInject, Unsupported
from sim.headless import HeadlessApp
from sim.loader import load_hmi


class _StubTransport:
    def __init__(self):
        self._frames = []
        self._sent = []

    def push(self, frame: bytes) -> None:
        self._frames.append(frame)

    def recv_frame(self):
        return self._frames.pop(0) if self._frames else None

    def send_frame(self, payload: bytes) -> None:
        self._sent.append(payload)

    def close(self) -> None:
        pass


def test_parse_touch_default_action_is_click():
    op = parse(b"touch m0")
    assert op == TouchInject(target="m0", action="click")


def test_parse_touch_explicit_press():
    assert parse(b"touch b0 press") == TouchInject(target="b0", action="press")


def test_parse_touch_explicit_release():
    assert parse(b"touch b0 release") == TouchInject(target="b0", action="release")


def test_parse_touch_by_id():
    assert parse(b"touch 21") == TouchInject(target=21, action="click")


def test_parse_touch_bad_action():
    op = parse(b"touch m0 hover")
    assert isinstance(op, Unsupported)


def test_parse_touch_missing_target():
    op = parse(b"touch ")
    assert isinstance(op, Unsupported)


def test_headless_touch_click_navigates_via_hotspot(hmi_path, tmp_path):
    """Clicking m0 on the main page (Touch Press = `page 1`) should switch to settings."""
    state = load_hmi(hmi_path)
    transport = _StubTransport()
    app = HeadlessApp(state, transport, out_path=tmp_path / "live.png")
    assert state.active_page.name == "main"
    transport.push(b"touch m0")
    app.step()
    assert state.active_page.name == "settings"


def test_headless_touch_press_only_does_not_release(hmi_path, tmp_path):
    state = load_hmi(hmi_path)
    transport = _StubTransport()
    app = HeadlessApp(state, transport, out_path=tmp_path / "live.png")
    # m0's codesdown fires page switch; press-only should still trigger that
    # (since the script ran in codesdown), but no codesup.
    transport.push(b"touch m0 press")
    app.step()
    # After press, we're on settings (from the page-switch script).
    assert state.active_page.name == "settings"
