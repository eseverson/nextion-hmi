#!/usr/bin/env python3
"""nextion_sim — live Linux simulator for the dashboard.

Loads the HMI and starts a window that responds to Nextion-style serial
commands over TCP / PTY / stdin. Use --bind to choose the transport.
"""
from __future__ import annotations
import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sim.app import App  # noqa: E402
from sim.loader import load_hmi  # noqa: E402
from sim.transport import TcpTransport, PtyTransport, StdinTransport  # noqa: E402


def _build_transport(spec: str):
    if spec == "stdin":
        return StdinTransport()
    if spec == "pty":
        t = PtyTransport()
        print(f"PTY available at: {t.path}", flush=True)
        return t
    if spec.startswith("tcp:"):
        host, _, port = spec[4:].rpartition(":")
        host = host or "127.0.0.1"
        t = TcpTransport(host=host, port=int(port))
        t.start()
        print(f"Listening on tcp://{host}:{t.port}", flush=True)
        return t
    raise SystemExit(f"unknown --bind: {spec}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hmi", default=str(REPO_ROOT / "source" / "nextion.hmi.HMI"))
    ap.add_argument("--bind", default="tcp:127.0.0.1:9999")
    ap.add_argument("--scale", type=int, default=1)
    ap.add_argument("--start-page", default=None)
    ap.add_argument("--log-commands", action="store_true")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    state = load_hmi(args.hmi)
    if args.start_page and args.start_page in state.pages:
        state.active_page = state.pages[args.start_page]
    transport = _build_transport(args.bind)
    App(state, transport, scale=args.scale, log_commands=args.log_commands).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
