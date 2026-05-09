"""RecordingTransport tests — verify each frame is logged + delegated."""
from __future__ import annotations
import json
from pathlib import Path

from sim.recorder import RecordingTransport


class _StubInner:
    def __init__(self):
        self._frames = []
        self._sent = []

    def recv_frame(self):
        return self._frames.pop(0) if self._frames else None

    def send_frame(self, payload):
        self._sent.append(payload)

    def close(self):
        pass


def test_record_logs_rx_and_tx_frames(tmp_path):
    inner = _StubInner()
    log = tmp_path / "session.jsonl"
    rt = RecordingTransport(inner, log)

    inner._frames.append(b"x0.val=99")
    assert rt.recv_frame() == b"x0.val=99"
    rt.send_frame(b"\x65\x00\x15\x01")
    rt.close()

    lines = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    rx, tx = lines
    assert rx["dir"] == "rx" and rx["frame"] == b"x0.val=99".hex()
    assert tx["dir"] == "tx" and tx["frame"] == b"\x65\x00\x15\x01".hex()


def test_record_passes_recv_none_through(tmp_path):
    inner = _StubInner()
    rt = RecordingTransport(inner, tmp_path / "log.jsonl")
    assert rt.recv_frame() is None
    rt.close()
    assert (tmp_path / "log.jsonl").read_text().strip() == ""


def test_record_delegates_send_to_inner(tmp_path):
    inner = _StubInner()
    rt = RecordingTransport(inner, tmp_path / "log.jsonl")
    rt.send_frame(b"hello")
    rt.close()
    assert inner._sent == [b"hello"]
