"""Recording transport — wraps any Transport and logs every framed
exchange to a JSONL file. Pair with `scripts/replay.py` to feed the
captured stream back into a fresh sim, preserving wall-clock spacing.

Format: one JSON object per line:

    {"t_ms": <int monotonic-ms since first event>,
     "dir": "rx" | "tx",
     "frame": "<hex string>"}

`rx` is bytes the host received from the wire (i.e. commands sent by
whoever was driving the sim). `tx` is bytes the host wrote to the wire
(i.e. event frames the sim emitted).
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Optional

from sim.transport import Transport


class RecordingTransport(Transport):
    """Decorates an inner Transport, mirroring all framed I/O to a file."""

    def __init__(self, inner: Transport, path: str | Path):
        super().__init__()
        self._inner = inner
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(self._path, "w", buffering=1)  # line-buffered
        self._epoch_ms = int(time.monotonic() * 1000)

    def _log(self, direction: str, frame: bytes) -> None:
        rec = {
            "t_ms": int(time.monotonic() * 1000) - self._epoch_ms,
            "dir": direction,
            "frame": frame.hex(),
        }
        self._fp.write(json.dumps(rec) + "\n")

    # The base class's recv_frame uses a buffer + _pump_into_buffer.
    # Overriding directly is simpler than fighting the inheritance chain.
    def recv_frame(self) -> Optional[bytes]:
        f = self._inner.recv_frame()
        if f is not None:
            self._log("rx", f)
        return f

    def send_frame(self, payload: bytes) -> None:
        self._log("tx", payload)
        self._inner.send_frame(payload)

    @property
    def port(self) -> int | None:
        return getattr(self._inner, "port", None)

    @property
    def path(self) -> str | None:
        return getattr(self._inner, "path", None)

    def start(self) -> None:
        if hasattr(self._inner, "start"):
            self._inner.start()

    def close(self) -> None:
        try:
            self._fp.close()
        except OSError:
            pass
        self._inner.close()
