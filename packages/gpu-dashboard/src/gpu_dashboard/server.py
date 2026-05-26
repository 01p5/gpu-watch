"""HTTP server shell. Mirrors slurm-mgr's gpu-dashboard, including the
"don't fall through to SPA on API 404" guard so callers see structured
error JSON.
"""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from gpuwatchlib import (
    DcgmExporterClient, JsonlAuditLogger, NodeRegistry, SSHRunner,
)

from .routes import Deps, parse_request, route_with_fleet

logger = logging.getLogger(__name__)


DEFAULT_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "dist"

# Paths the server treats as API. A 404 from one of these never falls
# through to the SPA — the structured JSON error reaches the client.
API_PREFIXES = ("/nodes", "/fleet", "/healthz", "/audit")


def _is_api_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in API_PREFIXES)


class Handler(BaseHTTPRequestHandler):
    deps: Deps                # type: ignore[assignment]
    static_dir: Path          # type: ignore[assignment]

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        logger.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None: self._handle("GET")
    def do_POST(self) -> None: self._handle("POST")
    def do_DELETE(self) -> None: self._handle("DELETE")
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _handle(self, method: str) -> None:
        path, query = parse_request(self.path)
        body = self._read_body()

        status, headers, payload = route_with_fleet(method, path, query, body, self.deps)

        if status == 404 and method == "GET" and not _is_api_path(path):
            if self._maybe_serve_static(path):
                return

        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _read_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return None
        raw = self.rfile.read(length)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    def _maybe_serve_static(self, path: str) -> bool:
        if not self.static_dir.exists():
            return False
        rel = path.lstrip("/") or "index.html"
        candidate = (self.static_dir / rel).resolve()
        if not str(candidate).startswith(str(self.static_dir.resolve())):
            return False                                # traversal guard
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.exists():
            candidate = self.static_dir / "index.html"
            if not candidate.exists():
                return False
        body = candidate.read_bytes()
        ctype = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        return True


def build_server(host: str = "127.0.0.1", port: int = 8780,
                 registry: NodeRegistry | None = None,
                 audit: JsonlAuditLogger | None = None,
                 static_dir: Path | None = None) -> ThreadingHTTPServer:
    reg = registry or NodeRegistry()
    aud = audit or JsonlAuditLogger()

    def runner_factory(node):
        return SSHRunner(node)

    def dcgm_factory(url: str) -> DcgmExporterClient:
        return DcgmExporterClient(url)

    deps = Deps(
        registry=reg, runner_factory=runner_factory,
        dcgm_factory=dcgm_factory, audit=aud,
    )

    handler_cls = type("BoundHandler", (Handler,), {
        "deps": deps,
        "static_dir": static_dir or DEFAULT_STATIC_DIR,
    })
    return ThreadingHTTPServer((host, port), handler_cls)


def main() -> int:
    parser = argparse.ArgumentParser(prog="gpu-dashboard")
    parser.add_argument("--host", default=os.environ.get("GPU_WATCH_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("GPU_WATCH_PORT", "8780")))
    parser.add_argument("--static-dir", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server = build_server(
        host=args.host, port=args.port,
        static_dir=Path(args.static_dir) if args.static_dir else None,
    )
    logger.info("gpu-dashboard listening on http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
