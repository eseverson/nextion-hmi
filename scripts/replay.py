#!/usr/bin/env python3
"""replay.py — replay a recorded sim session into a running simulator.

Reads a JSONL recording produced by `RecordingTransport` and re-sends the
`rx` (host-received) frames at their original timing. `tx` frames (events
the sim emitted) are skipped — the new sim will produce its own.

Usage:
    scripts/replay.py log.jsonl                          # tcp 127.0.0.1:9999
    scripts/replay.py log.jsonl --port 12345
    scripts/replay.py log.jsonl --speed 4                # 4x faster
"""
from __future__ import annotations
import argparse
import json
import socket
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", help="JSONL recording file")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--speed", type=float, default=1.0,
                    help="Playback speed multiplier; 0 to send everything as fast as possible.")
    args = ap.parse_args()

    path = Path(args.log)
    if not path.exists():
        print(f"missing: {path}", file=sys.stderr)
        return 1

    sock = socket.create_connection((args.host, args.port))
    sock.settimeout(0.5)
    try:
        last_t = 0
        wall_start = time.monotonic()
        sent = 0
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("dir") != "rx":
                continue
            t_ms = rec["t_ms"]
            # Wait until our wall clock reaches this offset (scaled by speed).
            if args.speed > 0:
                target = wall_start + (t_ms / 1000.0) / args.speed
                now = time.monotonic()
                if target > now:
                    time.sleep(target - now)
            payload = bytes.fromhex(rec["frame"])
            sock.sendall(payload + b"\xff\xff\xff")
            sent += 1
            last_t = t_ms
        print(f"replayed {sent} frame(s) over {last_t}ms (wall {time.monotonic()-wall_start:.2f}s)")
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
