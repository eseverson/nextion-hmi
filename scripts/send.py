#!/usr/bin/env python3
"""send.py — send Nextion commands to a running nextion_sim instance.

Each command is automatically framed with the trailing 0xff 0xff 0xff
terminator. Pass commands as positional args, or pipe them via stdin
(one per line).

Usage:
    scripts/send.py 'x0.val=12345' 's0.txt="Hello"' 'page settings'
    echo 'x0.val=42' | scripts/send.py
    scripts/send.py --host 192.168.1.10 --port 9999 'page main'
    scripts/send.py --touch m0                       # click hotspot
    scripts/send.py --state                          # fetch sim state via HTTP
"""
from __future__ import annotations
import argparse
import json
import socket
import sys
import time
import urllib.request

TERMINATOR = b"\xff\xff\xff"


def _send_tcp(host: str, port: int, cmds: list[str], read_events: bool) -> None:
    s = socket.create_connection((host, port))
    try:
        for c in cmds:
            payload = c.encode("latin-1") + TERMINATOR
            s.sendall(payload)
            print(f"sent: {c}")
        if read_events:
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
        time.sleep(0.05)  # let the sim drain
        s.close()


def _http_get(url: str, timeout: float = 2.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9999, help="TCP transport port (default 9999).")
    ap.add_argument("--http-port", type=int, default=None, help="If set, use HTTP introspection on this port.")
    ap.add_argument("--read-events", action="store_true",
                    help="After sending TCP, wait briefly for any events the sim sends back.")
    ap.add_argument("--touch", metavar="TARGET",
                    help="Send a `touch <target>` click instead of raw commands.")
    ap.add_argument("--state", action="store_true",
                    help="Fetch and pretty-print sim state via HTTP introspection (--http-port required).")
    ap.add_argument("commands", nargs="*",
                    help="Commands to send (no \\xff terminator needed).")
    args = ap.parse_args()

    if args.state:
        if args.http_port is None:
            ap.error("--state requires --http-port")
        body = _http_get(f"http://{args.host}:{args.http_port}/state.json")
        print(json.dumps(json.loads(body), indent=2))
        return 0

    cmds = list(args.commands)
    if args.touch:
        cmds.insert(0, f"touch {args.touch}")
    if not sys.stdin.isatty():
        cmds.extend(line.rstrip("\n") for line in sys.stdin if line.strip())
    if not cmds:
        ap.error("no commands given (positional args, --touch, or stdin)")

    _send_tcp(args.host, args.port, cmds, args.read_events)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
