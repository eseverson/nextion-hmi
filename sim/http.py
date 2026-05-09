"""HTTP introspection / control plane for a running simulator.

Run alongside any transport. Exposes:
- `GET /`             — minimal HTML auto-refresher for the live frame
- `GET /frame.png`    — current rendered frame (PNG)
- `GET /state.json`   — current state (active page, sys vars, dim, components)
- `POST /command`     — body is a Nextion command (UTF-8 / latin-1, no terminator)
- `POST /touch`       — body is `<target>` or `<target> <action>`

The control endpoints feed `app.handle_frame(...)` so the same dispatch
logic runs as if the command had arrived on the wire.
"""
from __future__ import annotations
import io
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("sim.http")


_INDEX_HTML = b"""<!doctype html>
<title>Nextion sim</title>
<style>body{margin:0;background:#111;color:#ccc;font-family:monospace}
img{display:block;margin:10px auto;image-rendering:pixelated;border:1px solid #333}
pre{padding:8px}</style>
<img id=f src=/frame.png>
<pre id=s>loading</pre>
<script>
async function tick(){
  document.getElementById('f').src='/frame.png?'+Date.now();
  try{
    let r = await fetch('/state.json');
    let j = await r.json();
    document.getElementById('s').textContent = JSON.stringify(j,null,2);
  }catch(e){}
}
setInterval(tick, 250);
tick();
</script>
"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _ok(self, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._ok(body, "application/json")

    def _err(self, status: int, msg: str) -> None:
        body = msg.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @property
    def app(self):
        return self.server.app  # type: ignore[attr-defined]

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._ok(_INDEX_HTML)
            return
        if self.path.startswith("/frame.png"):
            img = self.app.renderer.render(self.app.state)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            self._ok(buf.getvalue(), "image/png")
            return
        if self.path == "/state.json":
            self._json(_state_to_dict(self.app.state))
            return
        self._err(404, f"not found: {self.path}")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b""
        if self.path == "/command":
            try:
                self.app.handle_frame(body.strip())
            except Exception:
                log.exception("handle_frame failed")
                return self._err(500, "handle_frame failed")
            return self._ok(b"ok\n", "text/plain; charset=utf-8")
        if self.path == "/touch":
            text = body.decode("latin-1").strip()
            if not text:
                return self._err(400, "missing target")
            frame = ("touch " + text).encode("latin-1")
            try:
                self.app.handle_frame(frame)
            except Exception:
                log.exception("touch dispatch failed")
                return self._err(500, "touch failed")
            return self._ok(b"ok\n", "text/plain; charset=utf-8")
        self._err(404, f"not found: {self.path}")


def _state_to_dict(state) -> dict:
    pages = {}
    for name, p in state.pages.items():
        pages[name] = {
            "id": p.id,
            "size": [p.attrs.get("w"), p.attrs.get("h")],
            "components": [
                {
                    "name": c.name,
                    "id": c.id,
                    "type": c.type,
                    "val": c.attrs.get("val"),
                    "txt": c.attrs.get("txt"),
                    "bco": c.attrs.get("bco"),
                    "pco": c.attrs.get("pco"),
                    "vis": c.attrs.get("vis", 1),
                }
                for c in p.components
            ],
        }
    return {
        "active_page": state.active_page.name,
        "active_page_id": state.active_page.id,
        "dim": state.dim,
        "sys": list(state.sys),
        "pages": pages,
    }


class IntrospectionServer:
    """HTTP server bound to an App / HeadlessApp instance.

    Starts on a daemon thread so it doesn't block the main loop. Pass
    `port=0` to ask the OS for an ephemeral port; the chosen port is
    available as `server.port`.
    """

    def __init__(self, app, host: str = "127.0.0.1", port: int = 0):
        self.app = app
        self._server = ThreadingHTTPServer((host, port), _Handler)
        self._server.app = app  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="sim-http",
            daemon=True,
        )
        self._thread.start()
        log.info("introspection at http://%s:%d/",
                 self._server.server_address[0], self.port)

    def stop(self) -> None:
        try:
            self._server.shutdown()
        except Exception:
            pass
        self._server.server_close()
