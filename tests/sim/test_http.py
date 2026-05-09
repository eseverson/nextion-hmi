"""HTTP introspection server tests — start a server bound to a HeadlessApp
and hit the endpoints with stdlib urllib."""
from __future__ import annotations
import json
import time
import urllib.request

import pytest

from sim.headless import HeadlessApp
from sim.http import IntrospectionServer
from sim.loader import load_hmi


class _StubTransport:
    def __init__(self):
        self._frames = []

    def recv_frame(self):
        return self._frames.pop(0) if self._frames else None

    def send_frame(self, payload):
        pass

    def close(self):
        pass


@pytest.fixture
def app_and_server(hmi_path, tmp_path):
    state = load_hmi(hmi_path)
    transport = _StubTransport()
    app = HeadlessApp(state, transport, out_path=tmp_path / "live.png")
    server = IntrospectionServer(app, port=0)
    server.start()
    yield app, server
    server.stop()


def _http_get(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=2) as resp:
        return resp.status, resp.read()


def _http_post(url: str, body: bytes) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=2) as resp:
        return resp.status, resp.read()


def test_index_returns_html(app_and_server):
    app, server = app_and_server
    status, body = _http_get(f"http://127.0.0.1:{server.port}/")
    assert status == 200
    assert b"<img" in body and b"frame.png" in body


def test_frame_png_returns_png_bytes(app_and_server):
    app, server = app_and_server
    status, body = _http_get(f"http://127.0.0.1:{server.port}/frame.png")
    assert status == 200
    assert body.startswith(b"\x89PNG\r\n\x1a\n")


def test_state_json_lists_pages_and_active(app_and_server):
    app, server = app_and_server
    status, body = _http_get(f"http://127.0.0.1:{server.port}/state.json")
    assert status == 200
    j = json.loads(body)
    assert j["active_page"] in j["pages"]
    assert "main" in j["pages"]
    assert j["dim"] == 100


def test_command_post_applies_mutation(app_and_server):
    app, server = app_and_server
    status, _ = _http_post(
        f"http://127.0.0.1:{server.port}/command",
        b"x0.val=4242",
    )
    assert status == 200
    assert app.state.pages["main"].by_name("x0").attrs["val"] == 4242


def test_touch_post_navigates(app_and_server):
    app, server = app_and_server
    assert app.state.active_page.name == "main"
    status, _ = _http_post(f"http://127.0.0.1:{server.port}/touch", b"m0")
    assert status == 200
    assert app.state.active_page.name == "settings"
