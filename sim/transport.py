# sim/transport.py
from __future__ import annotations
import os
import select
import socket
import sys
import threading
from typing import Optional


_TERMINATOR = b"\xff\xff\xff"


class Transport:
    """Base framer; subclasses provide bytes I/O."""

    def __init__(self):
        self._buf = bytearray()
        self._lock = threading.Lock()

    # ---- Framing ----
    def _next_frame_from_buffer(self) -> Optional[bytes]:
        idx = self._buf.find(_TERMINATOR)
        if idx == -1:
            return None
        frame = bytes(self._buf[:idx])
        del self._buf[: idx + len(_TERMINATOR)]
        return frame

    def recv_frame(self) -> Optional[bytes]:
        with self._lock:
            self._pump_into_buffer()
            return self._next_frame_from_buffer()

    def _pump_into_buffer(self) -> None:
        """Subclass hook: read any available bytes into self._buf without blocking."""
        pass

    def _write_raw(self, payload: bytes) -> None:
        raise NotImplementedError

    def send_frame(self, payload: bytes) -> None:
        self._write_raw(payload + _TERMINATOR)

    def close(self) -> None:
        pass


class TcpTransport(Transport):
    """Multi-client TCP server.

    Accepts any number of concurrent connections. recv merges bytes from
    all clients into the same framing buffer. send broadcasts events to
    every connected client (so a firmware client and a debug observer can
    both watch). Dead connections are reaped silently.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9999):
        super().__init__()
        self._host = host
        self._port_requested = port
        self._server: Optional[socket.socket] = None
        self._clients: set[socket.socket] = set()
        self._accept_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.port = port

    def start(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self._host, self._port_requested))
        s.listen(8)
        s.settimeout(0.1)
        self._server = s
        self.port = s.getsockname()[1]
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            conn.setblocking(False)
            with self._lock:
                self._clients.add(conn)

    def _pump_into_buffer(self) -> None:
        # NOTE: caller (recv_frame) already holds self._lock. Do NOT reacquire.
        # _accept_loop also takes the lock, so iteration over _clients is safe
        # because while we hold the lock, the accept thread is blocked.
        clients = list(self._clients)
        dead: list[socket.socket] = []
        for c in clients:
            try:
                while True:
                    chunk = c.recv(4096)
                    if not chunk:
                        dead.append(c)
                        break
                    self._buf.extend(chunk)
            except BlockingIOError:
                continue
            except (ConnectionResetError, OSError):
                dead.append(c)
        for c in dead:
            self._clients.discard(c)
            try:
                c.close()
            except OSError:
                pass

    def _write_raw(self, payload: bytes) -> None:
        with self._lock:
            clients = list(self._clients)
            dead: list[socket.socket] = []
            for c in clients:
                try:
                    c.sendall(payload)
                except OSError:
                    dead.append(c)
            for c in dead:
                self._clients.discard(c)
                try:
                    c.close()
                except OSError:
                    pass

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            for c in list(self._clients):
                try:
                    c.close()
                except OSError:
                    pass
            self._clients.clear()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass


class StdinTransport(Transport):
    def __init__(self):
        super().__init__()
        self._fd = sys.stdin.fileno()

    def _pump_into_buffer(self) -> None:
        r, _, _ = select.select([self._fd], [], [], 0)
        if r:
            chunk = os.read(self._fd, 4096)
            if chunk:
                self._buf.extend(chunk)

    def _write_raw(self, payload: bytes) -> None:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()


class PtyTransport(Transport):
    def __init__(self):
        super().__init__()
        self._master, self._slave = os.openpty()
        self.path = os.ttyname(self._slave)

    def _pump_into_buffer(self) -> None:
        r, _, _ = select.select([self._master], [], [], 0)
        if r:
            try:
                chunk = os.read(self._master, 4096)
                if chunk:
                    self._buf.extend(chunk)
            except OSError:
                pass

    def _write_raw(self, payload: bytes) -> None:
        try:
            os.write(self._master, payload)
        except OSError:
            pass

    def close(self) -> None:
        for fd in (self._master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass


class SerialTransport(Transport):
    """Open an existing serial device (e.g. /dev/ttyUSB0) and frame.

    Useful for hardware-in-the-loop testing: wire your MCU's UART to a
    USB-serial adapter on the host, point the sim at the adapter, and the
    firmware drives the sim as if it were a real Nextion.

    Sets baud rate via termios. Sets 8N1 raw mode. Falls back to
    whatever-the-OS-gave-us if termios calls fail (e.g. on devices that
    don't support TCSANOW).
    """

    _BAUD_MAP = {
        2400: 0o000013, 4800: 0o000014, 9600: 0o000015, 19200: 0o000016,
        38400: 0o000017, 57600: 0o010001, 115200: 0o010002,
        230400: 0o010003, 460800: 0o010004, 921600: 0o010007,
    }

    def __init__(self, path: str, baud: int = 115200):
        super().__init__()
        self.path = path
        self.baud = baud
        self._fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            self._configure_termios()
        except Exception:
            # Best-effort. Many "serial" devices (PTYs, sockets-as-tty)
            # don't fully implement termios; the byte stream still works.
            pass

    def _configure_termios(self) -> None:
        import termios
        attrs = termios.tcgetattr(self._fd)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
        # 8N1 raw mode
        cflag &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)
        cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL
        iflag &= ~(termios.IXON | termios.IXOFF | termios.IXANY
                   | termios.IGNBRK | termios.BRKINT | termios.PARMRK
                   | termios.ISTRIP | termios.INLCR | termios.IGNCR | termios.ICRNL)
        oflag &= ~termios.OPOST
        lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON
                   | termios.ISIG | termios.IEXTEN)
        speed = self._BAUD_MAP.get(self.baud)
        if speed is None:
            # Fall back to a constant by name lookup, e.g. termios.B115200.
            attr_name = f"B{self.baud}"
            speed = getattr(termios, attr_name, termios.B115200)
        ispeed = ospeed = speed
        termios.tcsetattr(
            self._fd, termios.TCSANOW,
            [iflag, oflag, cflag, lflag, ispeed, ospeed, cc],
        )

    def _pump_into_buffer(self) -> None:
        r, _, _ = select.select([self._fd], [], [], 0)
        if r:
            try:
                chunk = os.read(self._fd, 4096)
                if chunk:
                    self._buf.extend(chunk)
            except (OSError, BlockingIOError):
                pass

    def _write_raw(self, payload: bytes) -> None:
        try:
            os.write(self._fd, payload)
        except OSError:
            pass

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


class EventEmitter:
    """Constructs Nextion event byte sequences and sends them via a Transport."""

    def __init__(self, transport):
        self._t = transport

    def touch_press(self, page_id: int, comp_id: int) -> None:
        self._t.send_frame(bytes([0x65, page_id & 0xFF, comp_id & 0xFF, 0x01]))

    def touch_release(self, page_id: int, comp_id: int) -> None:
        self._t.send_frame(bytes([0x65, page_id & 0xFF, comp_id & 0xFF, 0x00]))
