"""GET /healthz (see text-extractor/specs/overview.md, Health)."""

import json
import logging
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)


def make_server(host: str, port: int, checks: dict[str, Callable[[], None]]) -> ThreadingHTTPServer:
    """checks maps a dependency name to a function that raises when it is unavailable."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/healthz":
                return self._send(404, {"error": {"code": "not_found", "message": "unknown path"}})
            failing, errors = [], {}
            for name, check in checks.items():
                try:
                    check()
                except Exception as e:
                    failing.append(name)
                    errors[name] = str(e)
                    log.warning("health check failed", extra={"dependency": name, "error": str(e)})
            if failing:  # shape: /specs/pipeline-contracts.md, Health endpoint
                return self._send(503, {"status": "unavailable", "failing": failing, "errors": errors})
            self._send(200, {"status": "ok"})

        def _send(self, status: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):  # silence the default access log
            pass

    return ThreadingHTTPServer((host, port), Handler)


def serve_in_background(server: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, name="health", daemon=True)
    t.start()
    return t
