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
from sim.headless import HeadlessApp  # noqa: E402
from sim.http import IntrospectionServer  # noqa: E402
from sim.loader import load_hmi  # noqa: E402
from sim.transport import (  # noqa: E402
    TcpTransport, PtyTransport, StdinTransport, SerialTransport,
)


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
    if spec.startswith("serial:"):
        rest = spec[len("serial:"):]
        path, _, baud = rest.partition(":")
        baud_int = int(baud) if baud else 115200
        t = SerialTransport(path=path, baud=baud_int)
        print(f"Serial: {path} @ {baud_int} 8N1", flush=True)
        return t
    raise SystemExit(f"unknown --bind: {spec}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hmi", default=str(REPO_ROOT / "source" / "nextion.hmi.HMI"))
    ap.add_argument("--bind", default="tcp:127.0.0.1:9999")
    ap.add_argument("--scale", type=int, default=1)
    ap.add_argument("--start-page", default=None)
    ap.add_argument("--orientation", type=int, default=0,
                    choices=[0, 90, 180, 270],
                    help="Display orientation in degrees. Mirrors the TFT's "
                         "ui_orientation field. The HMI stores coords at 0°; "
                         "this rotates at render time.")
    ap.add_argument("--log-commands", action="store_true")
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--headless", action="store_true",
                    help="Run without Tk; render frames to --headless-out.")
    ap.add_argument("--headless-out", default=str(REPO_ROOT / "work" / "live.png"))
    ap.add_argument("--http", type=int, default=None, metavar="PORT",
                    help="Also start an HTTP introspection/control server on PORT.")
    ap.add_argument("--record", default=None, metavar="PATH",
                    help="Record framed I/O to PATH (JSONL); replay with scripts/replay.py.")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    state = load_hmi(args.hmi)
    if args.start_page and args.start_page in state.pages:
        state.active_page = state.pages[args.start_page]

    # Resolve orientation: explicit --orientation wins; otherwise auto-detect
    # from a sibling .tft (same stem). The TFT's H1+0x14 byte encodes
    # orientation per finding G10/N: 0x01=0° / 0x00=90° / 0x03=180° / 0x02=270°.
    if args.orientation:
        state.orientation = args.orientation
    else:
        tft_path = Path(args.hmi).with_suffix(".tft")
        if not tft_path.exists():
            # also try the original lowercase
            tft_path = Path(str(args.hmi).removesuffix(".HMI") + ".tft")
        if tft_path.exists():
            try:
                with open(tft_path, "rb") as f:
                    f.seek(0x14)
                    o_byte = f.read(1)[0]
                state.orientation = {0x00: 90, 0x01: 0, 0x02: 270, 0x03: 180}.get(o_byte, 0)
                if state.orientation:
                    print(f"Auto-detected orientation {state.orientation}° from {tft_path.name}",
                          flush=True)
            except OSError:
                pass

    transport = _build_transport(args.bind)
    if args.record:
        from sim.recorder import RecordingTransport
        transport = RecordingTransport(transport, args.record)
        print(f"Recording to {args.record}", flush=True)
    if args.headless:
        print(f"Headless: rendering to {args.headless_out}", flush=True)
        app = HeadlessApp(state, transport, out_path=args.headless_out,
                          log_commands=args.log_commands)
    else:
        app = App(state, transport, scale=args.scale,
                  log_commands=args.log_commands)
    if args.http is not None:
        srv = IntrospectionServer(app, port=args.http)
        srv.start()
        print(f"Introspection: http://127.0.0.1:{srv.port}/", flush=True)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
