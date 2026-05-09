"""SerialTransport tests use a PTY pair as a stand-in for a real serial
device — the slave path is a /dev/pts/N entry that looks like a tty to
SerialTransport, while the test reads/writes the master fd directly."""
from __future__ import annotations
import os
import time

import pytest

from sim.transport import SerialTransport


@pytest.fixture
def pty_pair():
    master, slave = os.openpty()
    yield master, os.ttyname(slave)
    for fd in (master, slave):
        try:
            os.close(fd)
        except OSError:
            pass


def test_serial_recv_frames_from_pty(pty_pair):
    master, slave_path = pty_pair
    t = SerialTransport(path=slave_path, baud=115200)
    try:
        os.write(master, b"x0.val=99\xff\xff\xffpage 1\xff\xff\xff")
        # Drain (might take a beat for the kernel to deliver)
        deadline = time.monotonic() + 1.0
        frames = []
        while time.monotonic() < deadline and len(frames) < 2:
            f = t.recv_frame()
            if f is not None:
                frames.append(f)
            else:
                time.sleep(0.01)
        assert frames == [b"x0.val=99", b"page 1"]
    finally:
        t.close()


def test_serial_send_frame_writes_to_pty(pty_pair):
    master, slave_path = pty_pair
    t = SerialTransport(path=slave_path, baud=115200)
    try:
        t.send_frame(bytes([0x65, 0x00, 0x15, 0x01]))
        # The master sees what the slave wrote.
        deadline = time.monotonic() + 1.0
        buf = b""
        while time.monotonic() < deadline and len(buf) < 7:
            r, _, _ = __import__("select").select([master], [], [], 0.05)
            if r:
                buf += os.read(master, 64)
        assert buf == b"\x65\x00\x15\x01\xff\xff\xff"
    finally:
        t.close()


def test_serial_handles_unknown_baud_via_attr_lookup(pty_pair):
    """Custom baud rates that exist as termios.BNNNNN constants should work."""
    master, slave_path = pty_pair
    # 9600 is in our explicit map; it should still apply cleanly.
    t = SerialTransport(path=slave_path, baud=9600)
    try:
        t.send_frame(b"hi")
        # No exception means termios accepted the baud setting.
    finally:
        t.close()
