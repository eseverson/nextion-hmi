#!/usr/bin/env python3
"""send.py — send Nextion commands to a running nextion_sim TCP listener.

Each command is automatically framed with the trailing 0xff 0xff 0xff
terminator. Pass commands as positional args, or pipe them via stdin
(one per line).

Usage:
    scripts/send.py 'x0.val=12345' 's0.txt="Hello"' 'page settings'
    echo 'x0.val=42' | scripts/send.py
    scripts/send.py --host 192.168.1.10 --port 9999 'page main'
"""
from __future__ import annotations
import argparse
import socket
import sys
import time

TERMINATOR = b"\xff\xff\xff"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--read-events", action="store_true",
                    help="After sending, wait briefly for any events the sim sends back.")
    ap.add_argument("commands", nargs="*",
                    help="Commands to send (no \\xff terminator needed).")
    args = ap.parse_args()

    cmds = list(args.commands)
    if not sys.stdin.isatty():
        cmds.extend(line.rstrip("\n") for line in sys.stdin if line.strip())
    if not cmds:
        ap.error("no commands given (positional args or stdin)")

    s = socket.create_connection((args.host, args.port))
    try:
        for c in cmds:
            payload = c.encode("latin-1") + TERMINATOR
            s.sendall(payload)
            print(f"sent: {c}")
        if args.read_events:
            s.settimeout(0.3)
            try:
                while True:
                    data = s.recv(64)
                    if not data:
                        break
                    print(f"recv: {data.hex()}")
            except (socket.timeout, TimeoutError):
                pass
    finally:
        # Give the sim a moment to drain before we close the socket.
        time.sleep(0.05)
        s.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
