# tests/sim/test_transport.py
import socket
import threading
import time

import pytest

from sim.transport import (
    Transport,
    TcpTransport,
    StdinTransport,
    PtyTransport,
    EventEmitter,
)


def test_framer_strips_trailing_marker():
    t = Transport()
    t._buf.extend(b"x0.val=1\xff\xff\xffpage 1\xff\xff\xff")
    assert t._next_frame_from_buffer() == b"x0.val=1"
    assert t._next_frame_from_buffer() == b"page 1"
    assert t._next_frame_from_buffer() is None


def test_framer_holds_partial_frame():
    t = Transport()
    t._buf.extend(b"x0.val=1\xff\xff")  # only two of three terminators
    assert t._next_frame_from_buffer() is None


def test_send_frame_appends_terminators(monkeypatch):
    sent = []
    t = Transport()
    t._write_raw = lambda b: sent.append(b)
    t.send_frame(b"\x65\x00\x15\x01")
    assert sent == [b"\x65\x00\x15\x01\xff\xff\xff"]


def test_tcp_transport_round_trip():
    t = TcpTransport(host="127.0.0.1", port=0)  # 0 = ephemeral
    t.start()
    try:
        port = t.port
        # Client connects
        client = socket.create_connection(("127.0.0.1", port))
        client.sendall(b"x0.val=99\xff\xff\xff")
        # Server (transport) reads
        deadline = time.monotonic() + 1.0
        frame = None
        while time.monotonic() < deadline:
            frame = t.recv_frame()
            if frame is not None:
                break
            time.sleep(0.01)
        assert frame == b"x0.val=99"
        # Server sends event back
        t.send_frame(b"\x65\x00\x15\x01")
        client.settimeout(1.0)
        data = client.recv(64)
        assert data == b"\x65\x00\x15\x01\xff\xff\xff"
        client.close()
    finally:
        t.close()


def test_tcp_multi_client_broadcast():
    """Two clients connected; an event sent by the sim must reach both."""
    t = TcpTransport(host="127.0.0.1", port=0)
    t.start()
    try:
        a = socket.create_connection(("127.0.0.1", t.port))
        b = socket.create_connection(("127.0.0.1", t.port))
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and len(t._clients) < 2:
            time.sleep(0.01)
        assert len(t._clients) == 2
        a.sendall(b"a.val=1\xff\xff\xff")
        b.sendall(b"b.val=2\xff\xff\xff")
        deadline = time.monotonic() + 1.0
        frames = set()
        while time.monotonic() < deadline and len(frames) < 2:
            f = t.recv_frame()
            if f is not None:
                frames.add(f)
            else:
                time.sleep(0.01)
        assert frames == {b"a.val=1", b"b.val=2"}
        t.send_frame(b"\x65\x00\x15\x01")
        a.settimeout(1.0); b.settimeout(1.0)
        assert a.recv(64) == b"\x65\x00\x15\x01\xff\xff\xff"
        assert b.recv(64) == b"\x65\x00\x15\x01\xff\xff\xff"
        a.close(); b.close()
    finally:
        t.close()


def test_event_emitter_touch_press():
    sent = []

    class _Stub:
        def send_frame(self, payload):
            sent.append(payload)

    emitter = EventEmitter(_Stub())
    # page id 0, comp id 21 (= 0x15)
    emitter.touch_press(page_id=0, comp_id=21)
    emitter.touch_release(page_id=0, comp_id=21)
    assert sent == [b"\x65\x00\x15\x01", b"\x65\x00\x15\x00"]
